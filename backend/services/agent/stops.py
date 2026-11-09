"""Stops on the way: the route's stops, adding, rejecting, dropping and ordering them, and the alternatives kept
for "another way?". LlmAgentProvider inherits these methods; the ones that call the router stay there."""
import logging
import re
from typing import Optional

from models import AgentRequest, AgentResponse, Place, RouteInfo
from services import approach
from services.geo import haversine_km
from services.routing import VIA_STOP_S
from services.agent.common import (ALT_ORIGIN_KM, CATEGORY_WORD, PASSED_KM, REACHED_KM, STOP_DETOUR_FRACTION,
                                   STOP_DETOUR_MIN_S, _nearest_index, _profile_key, _session_if)
from services.agent.phrases import (ALL_STOPS, GENERIC_NAME_WORDS, ORDER_VERB, STOP_KIND, STOP_ORDER, THAT_STOP,
                                    _name_tokens, stop_named)
from services.agent.replies import (VIA_CLEARED, VIA_DEST_NOT_A_STOP, VIA_DROPPED, VIA_LEFT, VIA_NONE, VIA_NOT_A_STOP,
                                    VIA_REJECTED, VIA_SKIPPED, VIA_STRAIGHT, VIA_WHICH, _miles, _with_warnings)

logger = logging.getLogger("truckbuddy.agent")


class StopsMixin:
    @staticmethod
    def _remember_alternatives(req: AgentRequest, place: Place, route: RouteInfo) -> None:
        """A new primary route: its alternatives serve "another way?" later; any stops of the old route are gone."""
        sess = _session_if(req.session_id)
        if sess is not None:
            sess["alternatives"] = {"place": place.id, "routes": list(route.alternatives), "i": 0,
                                    "current": route.model_copy(update={"alternatives": []}),
                                    "origin": (req.lat, req.lng), "profile": _profile_key(req.truck_profile)}
            sess.pop("via", None)
            sess.pop("last_offer", None)

    @staticmethod
    def _remember_cost(req: AgentRequest, route: RouteInfo) -> None:
        """The dialog's copy of the route is taken before the route is priced: the diesel estimate goes onto it
        too, so the driver's ledger saves a load with the same cost the Routes sheet shows."""
        sess = _session_if(req.session_id)
        cur = ((sess or {}).get("alternatives") or {}).get("current")
        if cur is not None:
            cur.fuel_usd, cur.cost_usd, cur.tolls_usd = route.fuel_usd, route.cost_usd, route.tolls_usd

    @staticmethod
    def _alternatives_stale(cached: Optional[dict], req: AgentRequest, place: Place) -> bool:
        """The cached alternatives are for this destination, this truck, from near here and for the stops as they
        are; otherwise "another way" plans afresh from where the truck is (QA 2026-11-01: a cached route from
        Sacramento was offered near Reno, with Sacramento's miles; alternatives for 13 ft 6 in after "set height
        to 14 ft"; the no-stop alternatives after a stop was added)."""
        if not cached or cached.get("place") != place.id or cached.get("refetch"):
            return True
        if cached.get("profile") is not None and cached["profile"] != _profile_key(req.truck_profile):
            return True
        origin = cached.get("origin")
        return bool(origin) and haversine_km(origin[0], origin[1], req.lat, req.lng) > ALT_ORIGIN_KM

    @staticmethod
    def _current_geometry(req: AgentRequest, sess: dict) -> list:
        """The line of the route being driven (the dialog's copy), or [] when this dialog did not set it."""
        cached = sess.get("alternatives")
        if not cached or not req.active_place or cached.get("place") != req.active_place.id:
            return []
        cur = cached.get("current")
        return list(cur.geometry) if cur is not None else []

    @staticmethod
    def _apps_stops(req: AgentRequest) -> bool:
        """The app sent the route's stops (route_stops, an empty list included): they are the truth, the dialog's
        copy in sess["via"] only a cache of what it last set. An older app sends none."""
        return "route_stops" in req.model_fields_set

    def _stops(self, req: AgentRequest, sess: dict) -> list[Place]:
        """Stops still ahead on the way to the active destination (empty for any other route); every reader of the
        route's stops goes through here. The app's when it sends them: a dialog that starts after a restart knows
        nothing of them (2026-11-02). Else the dialog's own, where a stop the truck has driven past is dropped:
        re-planning through it sent the driver back to it."""
        if not req.active_place:
            return []
        if self._apps_stops(req):
            # the app cuts the stops behind; one the truck is parked at is made, as below
            return [s for s in req.route_stops if haversine_km(s.lat, s.lng, req.lat, req.lng) >= REACHED_KM]
        via = sess.get("via")
        if not via or not req.active_place or via.get("place") != req.active_place.id:
            return []
        stops = list(via["stops"])
        geometry = self._current_geometry(req, sess)
        if stops and geometry:
            here = _nearest_index(geometry, req.lat, req.lng)

            def still_ahead(s: Place) -> bool:
                d = haversine_km(s.lat, s.lng, req.lat, req.lng)
                if d < REACHED_KM:  # parked at the stop: "add the TA too" must not route through this lot again
                    return False
                return _nearest_index(geometry, s.lat, s.lng) >= here or d < PASSED_KM

            ahead = [s for s in stops if still_ahead(s)]
            if len(ahead) != len(stops):
                logger.info("stops passed: %s", ", ".join(s.name for s in stops if s not in ahead))
                via["stops"] = ahead
                stops = ahead
        return stops

    def _order_stops(self, req: AgentRequest, sess: dict, stops: list[Place]) -> list[Place]:
        """Stops in the order the road meets them (by the nearest vertex of the current line): "swing by Love's
        first" after a Fernley stop used to drive to Fernley and back. Without the line (a dialog started after a
        restart) the stops keep the order they have and the new one, last, goes before the first stop farther
        from the truck than it is."""
        geometry = self._current_geometry(req, sess)
        if geometry:
            return sorted(stops, key=lambda s: (_nearest_index(geometry, s.lat, s.lng),
                                                haversine_km(s.lat, s.lng, req.lat, req.lng)))
        if not stops:
            return []
        *kept, new = stops
        far = haversine_km(new.lat, new.lng, req.lat, req.lng)
        i = next((i for i, s in enumerate(kept) if haversine_km(s.lat, s.lng, req.lat, req.lng) > far), len(kept))
        return kept[:i] + [new] + kept[i:]

    def _ahead(self, req: AgentRequest, sess: dict, places: list[Place]) -> list[Place]:
        """The places ahead of the truck on the way to the active destination. The search is around the truck, and
        the nearest truck stop could be 13 miles back: a U-turn the detour question (45 minutes) never asked about
        (review 2026-11-02, bug 19). Along the dialog's route line when there is one, else nearer to the
        destination than the truck is."""
        dest = req.active_place
        if not dest:
            return list(places)
        geometry = self._current_geometry(req, sess)
        if len(geometry) >= 2:
            along = [0.0]
            for i in range(1, len(geometry)):
                along.append(along[-1] + haversine_km(*geometry[i - 1], *geometry[i]))
            here = along[_nearest_index(geometry, req.lat, req.lng)]

            def ahead(p: Place) -> bool:
                # further along the line than it is off the line: a stop 20 km to the side of where the truck
                # is (the line passes nearest to it right here) is a detour back, not a stop ahead
                i = _nearest_index(geometry, p.lat, p.lng)
                return along[i] - here > haversine_km(p.lat, p.lng, *geometry[i])
        else:
            left = haversine_km(req.lat, req.lng, dest.lat, dest.lng)

            def ahead(p: Place) -> bool:
                return haversine_km(p.lat, p.lng, dest.lat, dest.lng) < left
        kept = [p for p in places if ahead(p)]
        if len(kept) != len(places):
            logger.info("stop search: behind the truck, left out: %s", ", ".join(p.name for p in places if p not in kept))
        return kept

    def _offered_stop(self, req: AgentRequest, sess: dict) -> Optional[Place]:
        """The stop added last on the way to the active destination, while it is still one of the stops."""
        offer = sess.get("last_offer") or {}
        if offer.get("kind") != "stop" or not req.active_place or offer.get("place") != req.active_place.id:
            return None
        return next((s for s in self._stops(req, sess) if s.id == offer.get("id")), None)

    async def _reject_stop(self, req: AgentRequest, sess: dict, stop: Place, text: str) -> AgentResponse:
        """"Not that one" right after a stop was added turns the stop down, never the destination (review
        2026-11-02, bug 15: Reno was rejected, and the same truck stop came back as the destination). Another
        place of the kind takes the stop's place; with none, the route goes on without it."""
        place = req.active_place
        rejected = sess.setdefault("rejected", [])
        if stop.id not in rejected:
            await self._note_rejection(sess, stop)
            rejected.append(stop.id)
        kept = [s for s in self._stops(req, sess) if s.id != stop.id]
        name = stop.name.split(",")[0]
        other = await self._stop_search(req, sess, stop.category, {place.id, *rejected, *(s.id for s in kept)})
        if other is not None and other.id not in rejected and other.id != place.id:
            res = await self._add_stop(req, {"intent": "add_stop", "place": other.model_dump(), "_drop": stop.id},
                                       sess, text)
            if res.status in ("route_set", "confirm"):
                res.reply = VIA_REJECTED.format(name=name) + res.reply
            return res
        route = await self._route_with_stops(req, kept)
        self._remember_driven(req, sess, route)
        if kept:
            sess["via"] = {"place": place.id, "stops": kept}
        else:
            sess.pop("via", None)
        sess.pop("last_offer", None)
        time_, mi = route.summary.split(" · ")[0], _miles(route.distance_mi)
        rest = (VIA_LEFT if kept else VIA_STRAIGHT).format(time=time_, mi=mi)
        reply = _with_warnings(VIA_DROPPED.format(name=name, what=CATEGORY_WORD.get(stop.category, "stop")) + rest, route)
        return AgentResponse(status="route_set", transcript=text, category=place.category, reply=reply, place=place,
                             route=route, parking=approach.for_route(place, route), options=kept, source=self.source)

    def _via_now(self, req: AgentRequest, sess: dict) -> list[list[float]]:
        """The stops on the way as [[lat, lng], ...]: the route's stops (_stops) when the app sent them or the
        dialog set them for this destination, else the app's coordinates (route_via, from an app that sends no
        route_stops; a dialog that starts after a restart knows nothing of them)."""
        via = sess.get("via")
        if self._apps_stops(req) or (via and req.active_place and via.get("place") == req.active_place.id):
            return [[s.lat, s.lng] for s in self._stops(req, sess)]
        return [list(v) for v in req.route_via]

    @staticmethod
    def _remember_driven(req: AgentRequest, sess: dict, route: RouteInfo) -> None:
        """A route with the stops as they are now is the line being driven; the alternatives cached for the
        no-stop route would lose the stops, so "another way" plans afresh through them."""
        if req.active_place:
            sess["alternatives"] = {"place": req.active_place.id, "routes": [], "i": 0, "refetch": True,
                                    "current": route.model_copy(update={"alternatives": []}),
                                    "origin": (req.lat, req.lng), "profile": _profile_key(req.truck_profile)}

    @staticmethod
    def _remaining_s(cur: RouteInfo, lat: float, lng: float) -> float:
        """Seconds left on the driven route from the truck's nearest vertex, the time spread by distance (the same
        maths the app's ETA uses)."""
        g = cur.geometry
        if len(g) < 2:
            return float(cur.duration_s)
        here = _nearest_index(g, lat, lng)
        legs = [haversine_km(g[i - 1][0], g[i - 1][1], g[i][0], g[i][1]) for i in range(1, len(g))]
        total = sum(legs) or 1.0
        return float(cur.duration_s) * (sum(legs[here:]) / total)

    def _stop_detour(self, req: AgentRequest, sess: dict, added: int, route: RouteInfo) -> Optional[tuple[float, float]]:
        """(extra seconds beyond the stop's own time, the rest of the trip before the stop) when a stop takes the
        trip far out of the way; None when it is a reasonable stop or when the trip before is unknown. The new
        route starts where the truck is, so it is compared with what was left of the old one from here, not
        with the whole trip as planned."""
        cached = sess.get("alternatives") or {}
        cur = cached.get("current") if req.active_place and cached.get("place") == req.active_place.id else None
        if cur is not None:
            old = self._remaining_s(cur, req.lat, req.lng)
        elif req.active_duration_min:
            old = req.active_duration_min * 60.0  # the planned total: understates the detour, so asks less, never more
        else:
            return None
        if old <= 0:
            return None
        extra = float(route.duration_s) - old - VIA_STOP_S * max(0, added)
        return (extra, old) if extra > max(STOP_DETOUR_MIN_S, STOP_DETOUR_FRACTION * old) else None

    async def _clear_stops(self, req: AgentRequest, sess: dict, text: str, d: Optional[dict] = None) -> AgentResponse:
        """"No stops, straight there" drops them all; "skip the Pilot" only the stop named."""
        place = req.active_place
        stops = self._stops(req, sess)
        if not place or not stops:
            return AgentResponse(status="chat", transcript=text, reply=VIA_NONE, source=self.source)
        query = str((d or {}).get("query") or "").strip()
        if not query and not ALL_STOPS.search(text):
            # No query from the model: the driver's own object decides ("skip the truck stop" cleared every stop,
            # the rest area too). Only what follows the order verb; "no stops" and the like clear them all below.
            obj = ORDER_VERB.sub("", text, count=1)
            if obj != text:
                query = obj.strip()
        named = stop_named(text, query, stops, place)
        if named:
            return await self._drop_stop(req, sess, named, stops, text)
        if query and not ALL_STOPS.search(query) and not ALL_STOPS.search(text):
            which = self._stop_meant(req, sess, query, stops)
            if isinstance(which, Place):
                return await self._drop_stop(req, sess, which, stops, text)
            if which:  # more than one could be meant: the names, never a guess
                names = [s.name.split(",")[0] for s in which]
                return AgentResponse(status="chat", transcript=text, source=self.source,
                                     reply=VIA_WHICH.format(names=", ".join(names[:-1]) + " or " + names[-1]))
            if any(len(w) >= 3 for w in _name_tokens(query) & _name_tokens(place.name) - GENERIC_NAME_WORDS):  # "skip Reno"
                return AgentResponse(status="chat", transcript=text, source=self.source,
                                     reply=VIA_DEST_NOT_A_STOP.format(name=place.name.split(",")[0]))
            # a place that is not one of the stops: keep them all
            return AgentResponse(status="chat", transcript=text, reply=VIA_NOT_A_STOP.format(name=query),
                                 source=self.source)
        route = await self._route_with_stops(req, [])
        self._remember_driven(req, sess, route)
        sess.pop("via", None)  # after the route: a failed call must not forget stops the app still shows
        reply = _with_warnings(VIA_CLEARED.format(time=route.summary.split(" · ")[0], mi=_miles(route.distance_mi)), route)
        return AgentResponse(status="route_set", transcript=text, category=place.category, reply=reply, place=place,
                             route=route, parking=approach.for_route(place, route), source=self.source)

    def _stop_meant(self, req: AgentRequest, sess: dict, query: str,
                    stops: list[Place]) -> Optional[Place | list[Place]]:
        """The stop a query that is no name means: by its order ("the last stop"), as the one just added ("that
        stop"), or by its kind ("the truck stop"); a list when several could be meant, None when it means none."""
        q = re.sub(r"^(?:the|my|our)\s+", "", query.strip().rstrip(".!?"), flags=re.I)
        m = STOP_ORDER.match(q) or STOP_ORDER.match(query.strip())
        if m:
            n = {"first": 0, "next": 0, "second": 1, "third": 2, "last": len(stops) - 1}[m.group("ord").lower()]
            return stops[n] if n < len(stops) else None
        if THAT_STOP.match(q) or THAT_STOP.match(query.strip()):
            return self._offered_stop(req, sess) or (stops[0] if len(stops) == 1 else stops)
        for pattern, category in STOP_KIND:
            if pattern.search(query):
                of_kind = [s for s in stops if s.category == category]
                if len(of_kind) == 1:
                    return of_kind[0]
                return of_kind or stops
        return None

    async def _drop_stop(self, req: AgentRequest, sess: dict, stop: Place, stops: list[Place],
                         text: str) -> AgentResponse:
        """One stop off the route, the others kept in their order."""
        place = req.active_place
        kept = [s for s in stops if s.id != stop.id]
        route = await self._route_with_stops(req, kept)
        self._remember_driven(req, sess, route)
        if kept:  # after the route: a failed call must not forget stops the app still shows
            sess["via"] = {"place": place.id, "stops": kept}
        else:
            sess.pop("via", None)
        sess.pop("last_offer", None)
        time_, mi = route.summary.split(" · ")[0], _miles(route.distance_mi)
        rest = (VIA_LEFT if kept else VIA_STRAIGHT).format(time=time_, mi=mi)
        reply = _with_warnings(VIA_SKIPPED.format(name=stop.name.split(",")[0]) + rest, route)
        return AgentResponse(status="route_set", transcript=text, category=place.category, reply=reply, place=place,
                             route=route, parking=approach.for_route(place, route), options=kept, source=self.source)
