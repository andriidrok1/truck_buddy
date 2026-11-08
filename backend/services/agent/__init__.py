"""Copilot agent: one structured LLM classification per turn, then the backend acts.

intent=find -> Places search + rule-based pick; go -> geocode + truck route; pick -> a place
found earlier in this dialog; chat/bye -> the model's short reply from the same call.
Route answers are templated (one sentence) and parking directions follow from the app.
"""
import asyncio
import logging
import os
import re
from typing import Callable, Optional

import httpx

from models import MAX_VIA, AgentRequest, AgentResponse, AppAction, MapsSource, Place, RouteInfo, TruckProfile
from services import (approach, breakdown, chains, controls, fuel_index, hos, llm, maps_grounding, memory,
                      profile_learn, reviews, timing, tpims, weather)
from services.geo import haversine_km, km_to_mi
from services.geocode import geocode, is_street, where
from services.google_places import TRUCK_STOP_NAME, PlacesUnavailable
from services.parking import cached_parking, check_parking, is_town, listed_in_data
from services.places import get_places_provider
from services import session_store
from services.dialog_state import DialogState
from services.routing import VIA_STOP_S, get_routing_provider
from services.flags import on
from services.logs import said
from services.speech_text import fit_reply, hours_minutes as _minutes_said
from services.timing import stage
# The agent's parts live in submodules; everything they define stays importable from services.agent.
from services.agent.prompt import (  # noqa: F401
    SYSTEM, SYSTEM_STATIC, PROMPT_VERSION, PROMPT_CACHE_KEY, prompt_messages)
from services.agent.phrases import (  # noqa: F401
    _CONTROL_PHRASES, control_phrase, PARK_WORDS, BED_WORDS, _STREET_ONLY, _ZIP_COUNTRY, with_city, city_of, GOODBYE,
    CHAIN_QUESTION, YES_RU_UK, NO_RU_UK, DEST_YES, VIA_FAR_YES, YES_PATTERNS, YES, NO, ANSWER_FILLER, bare_answer,
    PRICE_WORDS, CHEAPEST, FUEL_WORDS, CHEAPEST_ASK, ELSEWHERE, NOT_ELSEWHERE, CHEAPEST_NEAR_MI, asks_cheapest,
    list_is_near, ON_THE_WAY, STOP_CATEGORIES, REJECT_PLACE, DROP_ORDER, TAKE_OFF, NOT_A_STOP_OBJECT, ALL_STOPS,
    STOP_ORDER, THAT_STOP, STOP_KIND, ANOTHER, ROAD_WORDS, LIST_REF, ORDINALS, NOT_A_PLACE, REMEMBER, ROAD_PREFS,
    BYE_PHRASE, numbered, list_reference, _NAME_TOKEN, _name_tokens, named_place, NAME_LIKE, name_like,
    GENERIC_NAME_WORDS, named_in_text, stop_named, stop_to_drop, reject_or_reference_guard, pick_place, ORDER_VERB,
    WHERE_AM_I, PRICE_AT)
from services.agent.driver_name import (  # noqa: F401
    NAME_EVERY, NAME_MIN_WORDS, _SENTENCE_START, _INTERJECTION, _address, _a_place, _cut, GREETING, SHORT_LEAD,
    _vocatives, without_name, name_policy)
from services.agent.replies import (  # noqa: F401
    NOT_UNDERSTOOD, ROUTE_DOWN, NO_TRUCK_ROUTE, PLACES_DOWN, LLM_DOWN, SOMETHING_BROKE, NO_PLACES, CATEGORY_OFF, NO_CHAIN_NEARBY,
    WHERE_ON, WHERE_IN, WHERE_UNKNOWN, NO_PRICE_AT,
    NO_WALMART, ALL_REJECTED, heard_not_found, _COUNTRY, _town, _in_town, _street, _speak_street, _label_parts,
    spoken_where, _tokens, _names_match, dest_check, arrival_street, _miles, TEMPLATES, NOTICES, CLOSURE, ALT_REPLY,
    NO_ALT, TILL_SAID, CHAINS_NO_ROUTE, CHAINS_OUTSIDE, CHAINS_NONE, CHAINS_DOWN, ALT_NO_ROUTE, VIA_ADDED, VIA_DWELL,
    VIA_NO_ROUTE, VIA_FULL, VIA_DUPLICATE, VIA_IS_DESTINATION, VIA_STRAIGHT, VIA_CLEARED, VIA_NONE, VIA_NONE_AHEAD,
    VIA_NO_OTHER, VIA_REJECTED, VIA_DROPPED, VIA_LEFT, VIA_SKIPPED, VIA_NOT_A_STOP, VIA_DEST_NOT_A_STOP, VIA_WHICH,
    VIA_FAR, VIA_FAR_NO, DEST_FAR_MI, DEST_CHECK, DEST_NO, DEST_ONLY, STREET_WORDS, WHICH_PLACE, CONFIRM, CONFIRM_NO,
    _alt_reply, _notice, RESTRICTED_SPEECH, LEGAL_SPEECH, _route_warning, _with_warnings, _diesel_note, NO_PRICE,
    NO_PRICES, NO_PRICES_OFFER, NO_PRICES_KEEP, ALREADY_CHEAPEST, price_caveat, _diesel_sentence, _rating_note)
from services.agent.common import (  # noqa: F401
    RouteFailed, _error, _route_failed, _nearest_index, _route_key, _profile_key, CATEGORY_WORD, CORE_CATEGORIES,
    EXTRA_CATEGORIES, PARKING_CATEGORIES, enabled_categories, ALTERNATIVES_FRESH, ALT_ORIGIN_KM, PASSED_KM,
    REACHED_KM, CATEGORIES, MAX_HISTORY, _SESSIONS, FIT_STATUSES, FAST_SENTENCES, SLOW_SENTENCES, fast_mps, at_speed,
    _fit_for_speed, _session, _session_if, REJECTIONS_TO_LEARN, _facts, STOP_DETOUR_MIN_S, STOP_DETOUR_FRACTION, _id,
    _not_understood, _chain_of, _short_name, CHAIN_SPELLING, NAMED_CHAINS, chain_named)
from services.agent.context import ContextMixin
from services.agent.learning import LearningMixin
from services.agent.stops import StopsMixin

logger = logging.getLogger("truckbuddy.agent")

CLASSIFY_TIMEOUT_S = 20

MODEL = ("openai", "gpt-5.4-mini")
MAX_OPTIONS = 4
WEATHER_BUDGET_S = 2.0  # NWS lookups on a new route may add at most this much to the reply
DETOUR_BUDGET_S = 3.5  # the alternatives' weather, measured from the start; past it the detour line is skipped
PRECHECK = 1  # nearest candidates satellite-checked in the background (each = one vision call)
FAVORITE_EXTRA_MI = 5.0
TRUCK_TAGS = ("truck parking", "53 ft", "truck lot", "hgv")


def is_fuel_stop(p: Place) -> bool:
    """A truck_stop result with no truck-stop name and a satellite "unlikely": just a gas station."""
    return p.category == "truck_stop" and not TRUCK_STOP_NAME.search(p.name) and _verdict(p) == "unlikely"


PAST_TURNS = 80  # long-term memory fed to a new dialog (the driver's lines among the last 80 stored turns)


_tasks: set = set()
MEMORY_FIRST_TURN_S = 0.3  # how long the first turn of a dialog waits for long-term memory
MEMORY_LOAD_S = 3.0  # after this the memory read is abandoned for good
FACTS_EVERY_TURNS = 5  # how often a dialog checks whether the learned facts are due for a refresh


def stt_confirm_below() -> float:
    """Transcripts below this confidence (exp(mean logprob)) get a confirmation question. Env STT_CONFIRM_BELOW."""
    try:
        return float(os.environ.get("STT_CONFIRM_BELOW", "0.6"))
    except ValueError:
        return 0.6


def _background_task(t: asyncio.Task) -> None:
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


def _background(coro) -> None:
    _background_task(asyncio.create_task(coro))


_turns: dict = {"n": 0}  # rotation when there's no dialog session


def _route_reply(place: Place, route: RouteInfo, n: int = 0, driver: Optional[str] = None,
                 regional: str = "") -> str:
    """Conversational intro + one line with the place, a landmark and the distance; then a notice, if any."""
    return _with_warnings(_route_head(place, route, n, driver, regional), route)


def _route_head(place: Place, route: RouteInfo, n: int = 0, driver: Optional[str] = None, regional: str = "") -> str:
    """Conversational intro + one line with the place, a landmark and the distance. The driver's name is used
    about every third answer. `regional` = EIA comparison for the diesel price."""
    kind = "fuel_stop" if is_fuel_stop(place) else place.category
    intro, body = TEMPLATES[kind][n % len(TEMPLATES[kind])]
    street = arrival_street(place, route)
    # a geocoded place that is not a town is named with where it is ("Payson in Phoenix, Arizona"), so a street
    # or a business of the right name in the wrong place is heard before the truck turns
    dest = spoken_where(place) if place.category == "destination" and not is_town(place) else place.name.split(",")[0]
    name = place.name
    if kind == "cat_scale":  # the data names them "CAT Scale at Maverik #654": the template says "CAT scale at" already
        name = re.sub(r"^CAT\s+Scales?\s+(?:at\s+)?", "", name, flags=re.I) or name
    vals = {"name": name, "dest": dest, "mi": _miles(route.distance_mi), "time": route.summary.split(" · ")[0],
            "off": f", right off {street}" if street else "", "up": f", just up {street}" if street else ""}
    intro = intro.format(**vals)
    first_name = (driver or "").split(" ")[0]
    out = f"{intro}, {first_name}. " if first_name and n % 3 == 2 else f"{intro}. "
    return out + body.format(**vals) + _diesel_sentence(place, regional) + tpims.sentence(place)


def _verdict(p: Place) -> Optional[str]:
    c = cached_parking(p)
    return c.verdict if c else ("listed" if listed_in_data(p) else None)


def choose_place(found: list[Place], truck: TruckProfile, exclude: set[str],
                 facts: Optional[dict] = None) -> Optional[Place]:
    """Favorite chain (from the profile or learned) within +5 mi of the nearest, then truck parking
    listed/likely, then the closest. Chains the driver has rejected before come last; "unlikely" parking
    only if nothing else is left."""
    pool = [p for p in found if p.id not in exclude] or found
    if not pool:
        return None
    avoided = [c.lower() for c in (facts or {}).get("chains_avoided") or []]
    skipped = {n.lower() for n in (facts or {}).get("places_avoided") or []}  # turned down in an earlier dialog
    if avoided or skipped:
        kept = [p for p in pool if (_chain_of(p.name) or "").lower() not in avoided
                and _short_name(p).lower() not in skipped]
        pool = kept or pool
    ok = [p for p in pool if not is_fuel_stop(p) and _verdict(p) != "unlikely"] or \
        [p for p in pool if not is_fuel_stop(p)] or pool
    dist = lambda p: p.distance_mi or 0.0  # noqa: E731
    nearest = min(dist(p) for p in ok)
    liked = list(truck.favorite_chains) + list((facts or {}).get("chains_liked") or []) + \
        list((facts or {}).get("places_liked") or [])
    chains = [re.compile(rf"\b{re.escape(c)}\b", re.I) for c in liked if c.strip()]
    fav = [p for p in ok if any(rx.search(p.name) for rx in chains) and dist(p) <= nearest + FAVORITE_EXTRA_MI]
    if fav:
        return min(fav, key=dist)
    good = [p for p in ok if _verdict(p) in ("listed", "likely") or any(w in t.lower() for t in p.tags for w in TRUCK_TAGS)
            or (p.category == "truck_stop" and TRUCK_STOP_NAME.search(p.name))]
    return min(good or ok, key=dist)


async def classify(text: str, context: str, truck: TruckProfile) -> dict:
    system, user = prompt_messages(text, context, truck)
    with stage("llm_round_1"):
        res = await llm.complete_full(system, user, MODEL[1], cache_key=PROMPT_CACHE_KEY)
    return llm.json_object(res.text)


# Memory reads that outlived the first turn of a dialog, per process (an asyncio.Task cannot live in the stored
# DialogState). The next turn in this process adopts the result; another worker simply reads again.
_past_tasks: dict[str, asyncio.Task] = {}


class LlmAgentProvider(ContextMixin, StopsMixin, LearningMixin):
    source = MODEL[1]

    async def handle(self, req: AgentRequest, driver: Optional[str] = None) -> AgentResponse:
        if driver and not req.session_id:
            req.session_id = f"{driver}:solo"[:64]  # typed turns outside a dialog still use memory
        sid = req.session_id
        store = session_store.get_store()
        state = await store.load(sid) if sid else DialogState()
        token = session_store.enter(sid, state)
        try:
            return await self._turn(req, driver, state)
        finally:
            session_store.leave(token)
            if sid:
                try:
                    await store.save(sid, state)
                except Exception as e:  # noqa: BLE001 - the reply stands; the next turn starts from what was saved before
                    logger.warning("dialog state save failed for %s: %s", sid, e)

    async def _turn(self, req: AgentRequest, driver: Optional[str], sess: DialogState) -> AgentResponse:
        if driver and req.session_id:
            # before the intent: "forget that" as the first turn of a dialog must know whose facts to erase
            # (QA 2026-11-02, bug 4: the row stayed when the driver was set only after the turn)
            sess["driver"] = driver
        if driver and req.session_id and not sess.past_loaded:
            await self._adopt_past(driver, req.session_id, sess)
        res = await self._handle(req)
        self._name_once_in_a_while(req, res)
        _fit_for_speed(req, res)
        if res.status == "chat" and GOODBYE.search(req.text or ""):
            res.end_conversation = True
        if driver and res.status != "not_understood":  # saved in the background: the reply (and TTS) never waits on the DB
            memory.add_later(driver, [("driver", (req.text or "").strip()[:500]), ("copilot", res.reply[:500])])
            sess_now = _session(req.session_id) if req.session_id else None
            if sess_now is not None:
                sess_now["turns"] = sess_now.get("turns", 0) + 1
                if sess_now["turns"] % FACTS_EVERY_TURNS == 0:
                    _background(self._refresh_facts(driver, sess_now))
        if req.session_id:
            sess = _session(req.session_id)
            if req.text:
                sess["history"].append(f"Driver: {req.text.strip()[:200]}")
            sess["history"].append(f"Copilot: {res.reply[:200]}")
            del sess["history"][:-MAX_HISTORY * 2]
            if res.place:
                sess["places"] = ([res.place] + list(res.options))[:MAX_OPTIONS + 1]
        return res

    async def _adopt_past(self, driver: str, sid: str, sess: DialogState) -> None:
        """A new dialog starts with what the driver told the copilot in earlier ones. A warm database answers well
        within MEMORY_FIRST_TURN_S; a cold or slow one no longer holds the first reply: the read goes on in the
        background and lands on whichever later turn finds it done (past_loaded says it did)."""
        task = _past_tasks.get(sid)
        if task is None or task.cancelled():
            task = asyncio.create_task(self._load_past(driver))
            _background_task(task)
            _past_tasks[sid] = task
        done, _ = await asyncio.wait({task}, timeout=MEMORY_FIRST_TURN_S)
        if not done:
            return
        _past_tasks.pop(sid, None)
        if task.exception() is None:
            sess.update(task.result())
        sess.past_loaded = True

    @staticmethod
    async def _load_past(driver: str) -> dict:
        """{"past": the driver's own lines from earlier dialogs, "facts": learned preferences or None};
        empty when the database is down or slow."""
        out: dict = {"past": [], "facts": None}
        try:
            past, stored = await asyncio.wait_for(
                asyncio.gather(memory.recent(driver, PAST_TURNS), memory.get_facts(driver), return_exceptions=True),
                MEMORY_LOAD_S)
        except Exception:  # noqa: BLE001 - a slow or down database must never surface to the driver
            logger.warning("memory load failed or timed out")
            return out
        if isinstance(past, list):
            out["past"] = [t["text"][:200] for t in past if t["role"] == "driver"]
        if isinstance(stored, dict) and not profile_learn.is_empty(stored.get("facts")):
            out["facts"] = profile_learn.tidy(stored["facts"])
        return out

    async def _chain_nearby(self, req: AgentRequest, chain: str) -> list[Place]:
        """Truck stops of the named chain, nearest first, within reach (places.NAMED_MAX_KM on our own data; a
        provider without a name search gets its usual nearby results filtered)."""
        provider = get_places_provider()
        is_chain = lambda name: _chain_of(name) == chain  # noqa: E731
        try:
            with stage("tools"):
                if hasattr(provider, "search_named"):
                    found = await provider.search_named("truck_stop", is_chain, req.lat, req.lng)
                else:
                    found = [p for p in (await provider.search("truck_stop", req.lat, req.lng)).places
                             if is_chain(p.name)]
        except Exception as e:  # noqa: BLE001 - the plain search answers instead
            logger.info("chain search skipped: %s", e)
            return []
        logger.info("agent find chain %s -> %d", chain, len(found))
        return found[:MAX_OPTIONS + 2]

    async def _truck_stop_named(self, req: AgentRequest, query: str) -> Optional[Place]:
        """The truck stop near the driver whose name the query is ("the 49er", "Roady's"), or None."""
        if not name_like(query) or "truck_stop" not in enabled_categories():
            return None
        try:
            with stage("tools"):
                res = await get_places_provider().search("truck_stop", req.lat, req.lng)
        except Exception as e:  # noqa: BLE001 - the geocode result stands
            logger.info("truck stop by name skipped: %s", e)
            return None
        found = named_place(query, res.places)
        if found:
            logger.info("agent %r is a truck stop nearby -> %s", query, found.name)
        return found

    async def _cheapest_without_prices(self, req: AgentRequest, sess: dict, text: str,
                                       ask: bool = False) -> AgentResponse:
        """No place in the list has a diesel price: say so and name the closest. The current stop stays; another
        closest one is offered ("yes" routes there). A pick with no route routes to the closest with the usual
        "no price" caveat; a question (`ask`) only offers it."""
        nearest = min(sess["places"], key=lambda p: p.distance_mi or 0)
        name = nearest.name.split(",")[0]
        if req.active_place and nearest.id == req.active_place.id:
            return AgentResponse(status="chat", transcript=text, source=self.source,
                                 reply=NO_PRICES_KEEP.format(name=name))
        if req.active_place or ask:
            logger.info("agent cheapest %s with no diesel prices in the list -> offering %s", said(text), nearest.name)
            sess["pending"] = {"text": text, "kind": "cheapest_offer", "yes": "dest_yes", "no": {"reply": "Okay."},
                               "d": {"intent": "go", "place": nearest.model_dump()}}
            # a question (status confirm): never cut to its first sentence at highway speed
            return AgentResponse(status="confirm", transcript=text, source=self.source,
                                 reply=NO_PRICES_OFFER.format(name=name, mi=_miles(nearest.distance_mi or 0)))
        logger.info("agent pick %s with no diesel prices in the list -> the closest, %s", said(text), nearest.name)
        return await self._route_to(req, nearest, sess["places"], text)

    async def _route_to(self, req: AgentRequest, place: Place, found: list[Place], text: str,
                        head: Optional[Callable[[RouteInfo], str]] = None) -> AgentResponse:
        """Route to a place and the answer about it; `head` words the first line instead of the templates (a place
        near a point the driver named), the warnings after it stay the same."""
        with stage("tools"):
            try:
                route = await get_routing_provider().route(req.lat, req.lng, *approach.target(place), req.truck_profile)
            except Exception as e:  # noqa: BLE001
                raise RouteFailed(f"{type(e).__name__}: {e}") from e
        if place.category in PARKING_CATEGORIES:
            _background(check_parking(place, route.approach))  # destinations too; entry tip relative to the approach
        others = [p for p in found if p.id != place.id][:MAX_OPTIONS]
        counter = _session_if(req.session_id) or _turns
        n = counter.get("n", 0)
        counter["n"] = n + 1
        self._remember_alternatives(req, place, route)
        regional, weather_line, chain_line = await asyncio.gather(
            self._regional_note(place), self._route_weather(route, _session_if(req.session_id)),
            self._route_chains(route))
        first = head(route) if head else (_route_head(place, route, n, req.truck_profile.driver_name, regional)
                                          + price_caveat(text, place))
        sess_now = _session_if(req.session_id)
        learned = sess_now.pop("say_learned", None) if sess_now is not None else None
        reply = _with_warnings(first, route, chain_line, weather_line, learned)
        return AgentResponse(status="route_set", transcript=text, category=place.category, reply=reply,
                             place=place, route=route, parking=approach.for_route(place, route),
                             options=others, source=self.source)

    async def _price_at(self, req: AgentRequest, d: dict, sess: dict, text: str) -> AgentResponse:
        """"How much is diesel there?": the price at the place being talked about, or that there is none."""
        place = pick_place(d, sess["places"], _id(req.active_place), req.ui.option_ids) or req.active_place or \
            (sess["places"][0] if sess["places"] else None)
        timing.note("intent", "price")
        if not place:
            return AgentResponse(status="chat", transcript=text, reply=WHICH_PLACE, source=self.source)
        said_ = _diesel_sentence(place, await self._regional_note(place)).strip()
        reply = said_ or NO_PRICE_AT.format(name=_short_name(place))
        return AgentResponse(status="chat", transcript=text, reply=reply, place=place, source=self.source)

    async def _where_am_i(self, req: AgentRequest, text: str) -> AgentResponse:
        """The road and town the truck is at, read from a reverse geocode."""
        with stage("tools"):
            w = await where(req.lat, req.lng)
        if not w or not w.get("city") or not w.get("state"):
            reply = WHERE_UNKNOWN
        elif w.get("street"):
            reply = WHERE_ON.format(**w)
        else:
            reply = WHERE_IN.format(**w)
        timing.note("intent", "where")
        return AgentResponse(status="chat", transcript=text, reply=reply, source=self.source)

    async def _chains_question(self, req: AgentRequest, sess: dict, text: str) -> AgentResponse:
        """Chain controls along the route the driver is on, from Caltrans; without a route, say what is needed."""
        if not req.active_place:
            return AgentResponse(status="chat", transcript=text, reply=CHAINS_NO_ROUTE, source=self.source)
        cached = sess.get("alternatives") or {}
        route = cached.get("current") if cached.get("place") == req.active_place.id else None
        if route is None:
            # The app restarted and resumed the trip: a new dialog with no route of its own (QA 2026-11-02, bug
            # 7: "Set a route..." during live navigation). The route the app drives is rebuilt from the request
            # (its handle, else a plan through its stops) and kept as the one being driven.
            try:
                route = await self._route_from_request(req, sess)
            except RouteFailed as e:
                logger.warning("chain question: no route to check: %s", e)
                return AgentResponse(status="chat", transcript=text, reply=CHAINS_DOWN, source=self.source)
            self._remember_driven(req, sess, route)
        if not chains.touches_california(route.geometry):
            return AgentResponse(status="chat", transcript=text, reply=CHAINS_OUTSIDE, source=self.source)
        line = await self._route_chains(route)
        return AgentResponse(status="chat", transcript=text, reply=line or CHAINS_NONE, source=self.source)

    @staticmethod
    async def _route_chains(route: RouteInfo) -> str:
        """Caltrans chain controls on a new route (California only), spoken once; bounded like the weather."""
        try:
            route.chain_controls = await asyncio.wait_for(chains.for_route(route.geometry), WEATHER_BUDGET_S)
        except Exception as e:  # noqa: BLE001
            logger.info("route chain controls skipped: %s", e)
            route.chain_controls = []
        return chains.speech(route.chain_controls)

    @staticmethod
    async def _route_weather(route: RouteInfo, sess: Optional[dict] = None) -> str:
        """NWS alerts along a new route and its alternatives, attached to each and spoken once, timed against the
        ETA ("you'd hit it around 4 pm"); bounded so it never delays the reply. When another truck route misses
        the weather, it is offered and moved to the front of "another way", so "take the other way" gets it."""
        try:
            alts_known = await weather.for_route(route, WEATHER_BUDGET_S, DETOUR_BUDGET_S)
        except Exception as e:  # noqa: BLE001 - never the route itself
            logger.info("route weather failed: %s", e)
            alts_known = False
            route.weather = []
            for alt in route.alternatives:
                alt.weather = []
        line = weather.speech(route.weather, route)
        if not line:
            return weather.later_speech(route)  # nothing on the way now; a storm after the truck is through still gets a word
        detour = weather.detour_speech(route, alts_known=alts_known, said_till=TILL_SAID in line)
        best = weather.better_route(route) if alts_known else None
        cached = (sess or {}).get("alternatives")
        if cached and cached.get("current") is not None:
            cur = cached["current"]  # the copy was taken before the lookup
            cur.weather, cur.weather_known, cur.weather_later = list(route.weather), route.weather_known, list(route.weather_later)
        if best is not None and cached and any(r is best for r in cached.get("routes", [])):
            cached["routes"] = [best] + [r for r in cached["routes"] if r is not best]
            # The Routes sheet shows route.alternatives: its first row is the one "take the other way" picks.
            route.alternatives = [best] + [r for r in route.alternatives if r is not best]
            logger.info("route weather: %s misses it, offered first", " and ".join(best.labels) or "alternative")
        return f"{line} {detour}" if detour else line

    async def _weather(self, req: AgentRequest, d: dict, sess: dict, text: str) -> AgentResponse:
        """"Weather in Reno at 3 pm" / "what's the weather like ahead": hourly forecast at a place and time."""
        query = str(d.get("query") or "").strip()
        if query:
            with stage("tools"):
                matches = await geocode(with_city(query, sess.get("city")), req.lat, req.lng)
            if not matches:
                return AgentResponse(status="chat", transcript=text, reply=heard_not_found(query), source=self.source)
            name, lat, lng = matches[0].name.split(",")[0], matches[0].lat, matches[0].lng
        elif req.active_place and re.search(r"\b(there|ahead|at the destination|when I get)\b", text, re.I):
            name, lat, lng = req.active_place.name.split(",")[0], req.active_place.lat, req.active_place.lng
        else:
            name, lat, lng = "Here", req.lat, req.lng
        with stage("tools"):
            try:
                reply = await asyncio.wait_for(weather.forecast_speech(name, lat, lng, d.get("when")), 8.0)
            except Exception as e:  # noqa: BLE001
                logger.info("weather answer failed: %s", e)
                reply = weather.NO_FORECAST.format(place=name)
        return AgentResponse(status="chat", transcript=text, reply=reply, source=self.source)

    async def _hos(self, req: AgentRequest, text: str) -> AgentResponse:
        """"How many hours do I have?" from the driver's Motive account (the driver's own key per request, or the
        fleet's stored key with the driver's Motive user id; server.motive_auth)."""
        if not hos.enabled() or not req.motive_key:
            return AgentResponse(status="chat", transcript=text, reply=hos.CONNECT, source=self.source)
        with stage("tools"):
            try:
                clocks = await asyncio.wait_for(hos.available_time(req.motive_key, req.motive_user), 8.0)
                reply = hos.speech(clocks)
            except hos.HosAuthError:
                reply = hos.BAD_KEY
            except Exception as e:  # noqa: BLE001 - incl. timeout and HosDown
                logger.info("hos lookup failed: %s", type(e).__name__)  # never the key, never the body
                reply = hos.DOWN
        return AgentResponse(status="chat", transcript=text, reply=reply, source=self.source)

    @staticmethod
    async def _regional_note(place: Place) -> str:
        """EIA comparison for the diesel price of a truck stop; bounded so it can never slow the reply down."""
        if place.diesel_price is None or place.category != "truck_stop":
            return ""
        try:
            return await asyncio.wait_for(fuel_index.regional_note(place.lat, place.lng, place.diesel_price), 1.5)
        except Exception as e:  # noqa: BLE001 - incl. timeout
            logger.info("regional diesel note skipped: %s", e)
            return ""

    async def _reviews(self, req: AgentRequest, d: dict, sess: dict, text: str) -> AgentResponse:
        """"What do drivers say about it?": the spoken gist of the place's Google reviews."""
        place = pick_place(d, sess["places"], _id(req.active_place), req.ui.option_ids) or req.active_place or (sess["places"][0] if sess["places"] else None)
        if not place:
            return AgentResponse(status="chat", transcript=text, reply=WHICH_PLACE, source=self.source)
        if not place.reviews:  # the app's copy may be older than the search; refresh from the provider cache
            fresh = await get_places_provider().get(place.id, req.lat, req.lng)
            if fresh and fresh.reviews:
                place = fresh
        with stage("tools"):
            grounded = await maps_grounding.drivers_say(place)
            if grounded:
                # Google Maps grounded: the exact text, nothing added, its links under it in the app.
                return AgentResponse(status="chat", transcript=text, reply=grounded.text, place=place,
                                     maps_sources=[MapsSource(**s) for s in grounded.sources], source=self.source)
            reply = await reviews.speech(place)
        return AgentResponse(status="chat", transcript=text, reply=reply, place=place, source=self.source)

    async def _resolve_stop(self, req: AgentRequest, d: dict, sess: dict, text: str = "") -> Optional[Place]:
        """The place the driver wants to stop at: a place offered earlier (nudges), a listed place the driver
        named, a named place/address, else the best place of a category."""
        if isinstance(d.get("place"), dict):
            try:
                return Place(**d["place"])
            except Exception:  # noqa: BLE001 - a malformed offer falls through to the normal path
                pass
        # "Stop at the 49er on the way": the driver's word beats the model's index (it pointed at the next
        # place once the 49er was already a stop) and beats a geocode ("49er" is a street in Placerville).
        # The destination itself may come back here: _add_stop answers "that's where we're headed".
        said = named_in_text(text, sess["places"])
        if said:
            logger.info("add_stop: the driver named %s", said.name)
            return said
        picked = pick_place(d, sess["places"], _id(req.active_place), req.ui.option_ids)
        if picked:
            return picked
        query = str(d.get("query") or "").strip()
        category = d.get("category") if d.get("category") in CATEGORIES else None
        named = named_place(query, sess["places"]) if query else None
        if named:
            logger.info("add_stop query %r names a listed place -> %s", query, named.name)
            return named
        # "the Pilot in Fernley": the model may put the town in `near` and only "Pilot" in `query`. The town
        # goes into the search, and a chain name is no longer looked up next to the truck (eval 2026-11-02:
        # the Pilot in Sacramento was added instead of the one in Fernley).
        near = str(d.get("near") or "").strip()
        if query and near and near.split(",")[0].strip().lower() not in query.lower():
            query = f"{query}, {near}"
        if query:
            if not near and TRUCK_STOP_NAME.search(query) and not re.search(r"\d", query):
                # "the Pilot", "Love's in Auburn": a chain name is a truck stop near the driver before it is an
                # address (the geocoder once answered with the hamlet of Pilot, NV, 300 miles past the destination)
                stop = await self._truck_stop_named(req, query)
                if stop:
                    return stop
            with stage("tools"):
                matches = await geocode(with_city(query, sess.get("city")), req.lat, req.lng)
            if (not matches or is_street(matches[0])) and not near:
                stop = await self._truck_stop_named(req, query)
                if stop:
                    return stop
            if matches:
                return matches[0]
            if not category:
                return None
            # "cheapest diesel on my route" came back as the query: not a place name. The category search
            # is what the driver meant; a real name that doesn't geocode still ends in "can't find it".
            logger.info("add_stop query %r is not a place; searching %s instead", query, category)
        return await self._stop_search(req, sess, category or "truck_stop",
                                       {req.active_place.id} if req.active_place else set())

    async def _stop_search(self, req: AgentRequest, sess: dict, category: str, exclude: set[str]) -> Optional[Place]:
        """The best place of a category for a stop on the way, among those ahead of the truck; the excluded ones
        only if nothing else is left."""
        with stage("tools"):
            res = await get_places_provider().search(category, req.lat, req.lng)
        found = sorted(self._ahead(req, sess, res.places), key=lambda p: p.distance_mi or 0)
        return choose_place(found, req.truck_profile, exclude, sess.get("facts"))

    async def _route_from_request(self, req: AgentRequest, sess: dict) -> RouteInfo:
        """The route the app is driving when the dialog has none: by its HERE handle (the same road; HERE falls
        back to a fresh plan when the handle has expired), else planned from the truck through the stops."""
        via = self._via_now(req, sess)
        with stage("tools"):
            try:
                if req.route_handle:
                    route = await get_routing_provider().reroute(req.route_handle, req.lat, req.lng, None,
                                                                 *approach.target(req.active_place), req.truck_profile,
                                                                 via=via)
                else:
                    route = await get_routing_provider().route(req.lat, req.lng, *approach.target(req.active_place),
                                                               req.truck_profile, alternatives=0, via=via)
            except Exception as e:  # noqa: BLE001
                raise RouteFailed(f"{type(e).__name__}: {e}") from e
        route.alternatives = []
        return route

    async def _route_with_stops(self, req: AgentRequest, stops: list[Place]) -> RouteInfo:
        with stage("tools"):
            try:
                route = await get_routing_provider().route(req.lat, req.lng, *approach.target(req.active_place),
                                                           req.truck_profile, alternatives=0,
                                                           via=[[s.lat, s.lng] for s in stops])
            except Exception as e:  # noqa: BLE001
                raise RouteFailed(f"{type(e).__name__}: {e}") from e
        route.alternatives = []
        return route

    async def _add_stop(self, req: AgentRequest, d: dict, sess: dict, text: str) -> AgentResponse:
        place = req.active_place
        if not place:
            # "add a fuel stop" with nothing to add it to: the model sometimes says add_stop anyway (eval
            # h_stop_no_route). A category is a plain find, a named place a plain go; only nothing is "set a route".
            category = d.get("category") if d.get("category") in CATEGORIES else None
            query = str(d.get("query") or "").strip()
            if query:
                with stage("tools"):
                    matches = await geocode(with_city(query, sess.get("city")), req.lat, req.lng)
                if matches:
                    return await self._route_to(req, matches[0], [], text)
                if not category:
                    return AgentResponse(status="chat", transcript=text, reply=heard_not_found(query), source=self.source)
            if category:
                return await self._find(req, category, text, set())
            return AgentResponse(status="chat", transcript=text, reply=VIA_NO_ROUTE, source=self.source)
        # a stop turned down ("not that one") is replaced, not kept beside the new one
        stops = [s for s in self._stops(req, sess) if s.id != d.get("_drop")]
        if len(stops) >= MAX_VIA:
            return AgentResponse(status="chat", transcript=text, reply=VIA_FULL, source=self.source)
        stop = await self._resolve_stop(req, d, sess, text)
        if not stop:
            if not str(d.get("query") or "").strip():  # a category with nothing of it ahead
                what = CATEGORY_WORD.get(str(d.get("category")), "truck stop")
                return AgentResponse(status="chat", transcript=text, reply=VIA_NONE_AHEAD.format(what=what),
                                     source=self.source)
            what = str(d.get("query") or CATEGORY_WORD.get(str(d.get("category")), "a stop")).strip()
            return AgentResponse(status="chat", transcript=text, reply=heard_not_found(what), source=self.source)
        if stop.id == place.id or haversine_km(stop.lat, stop.lng, place.lat, place.lng) < 0.15:
            # "stop at the 49er" while going to the 49er: the model echoed the destination's address and the
            # geocode of it came back as a new place (QA 2026-10-31, H9)
            return AgentResponse(status="chat", transcript=text, source=self.source,
                                 reply=VIA_IS_DESTINATION.format(name=place.name.split(",")[0]))
        if any(s.id == stop.id for s in stops):
            # "stop at the 49er" when it already is one (QA 2026-10-31, H9); "yeah add that one" said again added
            # a different stop nobody asked for (review 2026-11-02): only "another" looks further
            if named_in_text(text, [stop]) or not ANOTHER.search(text):
                return AgentResponse(status="chat", transcript=text, source=self.source,
                                     reply=VIA_DUPLICATE.format(name=stop.name.split(",")[0]))
            # "add another truck stop": the model's "truck stop" or its index landed on a stop already on the route
            # and the copilot said so instead of looking (QA 2026-11-02). Another one of the kind, never these.
            category = d.get("category") if d.get("category") in CATEGORIES else stop.category
            taken = {place.id, *(s.id for s in stops)}
            stop = await self._stop_search(req, sess, category, taken)
            if stop is None or stop.id in taken:
                return AgentResponse(status="chat", transcript=text, source=self.source,
                                     reply=VIA_NO_OTHER.format(what=CATEGORY_WORD.get(category, "stop")))
        before = len(stops)
        stops = self._order_stops(req, sess, stops + [stop])
        route = await self._route_with_stops(req, stops)
        if not d.get("_confirmed"):
            far = self._stop_detour(req, sess, len(stops) - before, route)
            if far:
                extra, old = far
                # "yes" re-runs this turn with the resolved stop (no second geocode); "no" adds nothing.
                sess["pending"] = {"text": text, "kind": "stop_guard",
                                   "d": {**d, "_confirmed": True, "place": stop.model_dump()},
                                   "no": {"reply": VIA_FAR_NO}, "yes": "via_far_yes"}
                timing.note("stop_guard", "asked")
                logger.info("add_stop %s is %.0f min out of the way: asking first", stop.name, extra / 60)
                return AgentResponse(status="confirm", transcript=text, source=self.source,
                                     reply=VIA_FAR.format(name=stop.name.split(",")[0], extra=_minutes_said(round(extra / 60)),
                                                          new=route.summary.split(" · ")[0], old=_minutes_said(round(old / 60))))
        self._remember_driven(req, sess, route)
        sess["via"] = {"place": place.id, "stops": stops}
        sess["last_offer"] = {"kind": "stop", "id": stop.id, "place": place.id}
        ahead = f", about {_miles(stop.distance_mi)} miles ahead" if stop.distance_mi else ""
        dwell = VIA_DWELL.format(minutes=VIA_STOP_S // 60, each="the stop" if len(stops) == 1 else "each stop")
        reply = _with_warnings(VIA_ADDED.format(name=stop.name.split(",")[0], ahead=ahead, time=route.summary.split(" · ")[0],
                                                mi=_miles(route.distance_mi), dwell=dwell) + price_caveat(text, stop), route)
        return AgentResponse(status="route_set", transcript=text, category=place.category, reply=reply, place=place,
                             route=route, parking=approach.for_route(place, route), options=stops, source=self.source)

    async def _alternative(self, req: AgentRequest, sess: dict, text: str) -> AgentResponse:
        """Next unused alternative route to the active destination; fetched fresh when the dialog has none."""
        place = req.active_place
        if not place:
            return AgentResponse(status="chat", transcript=text, reply=ALT_NO_ROUTE, source=self.source)
        cached = sess.get("alternatives")
        if self._alternatives_stale(cached, req, place):
            with stage("tools"):
                try:
                    fresh = await get_routing_provider().route(req.lat, req.lng, *approach.target(place),
                                                               req.truck_profile, alternatives=ALTERNATIVES_FRESH,
                                                               via=self._via_now(req, sess))
                except Exception as e:  # noqa: BLE001
                    raise RouteFailed(f"{type(e).__name__}: {e}") from e
            # The fastest way from here is the one to come back to; the diff is spoken against it.
            cached = sess["alternatives"] = {"place": place.id, "routes": list(fresh.alternatives), "i": 0,
                                             "current": fresh.model_copy(update={"alternatives": []}),
                                             "origin": (req.lat, req.lng), "profile": _profile_key(req.truck_profile)}
        i = cached["i"]
        if i >= len(cached["routes"]):
            return AgentResponse(status="chat", transcript=text, reply=NO_ALT, source=self.source)
        route = cached["routes"][i]
        cached["i"] = i + 1
        # The route the driver leaves goes to the end of the list: "another way" can come back to it, and
        # the Routes sheet keeps every other way. Before, taking the alternative lost the faster route
        # (QA 2026-10-31, H12).
        prev = cached.get("current")
        if prev is not None and all(_route_key(r) != _route_key(prev) for r in cached["routes"]):
            cached["routes"].append(prev)
        cached["current"] = route
        sess.pop("last_offer", None)  # the last thing offered is this road now, not a stop
        route.alternatives = [r.model_copy(update={"alternatives": []}) for r in cached["routes"] if r is not route]
        reply = _with_warnings(_alt_reply(route, prev), route)  # a closure or a restriction on the other way is new
        return AgentResponse(status="route_set", transcript=text, category=place.category,
                             reply=reply, place=place, route=route, parking=approach.for_route(place, route),
                             source=self.source)

    def _parking(self, req: AgentRequest, d: dict, sess: dict, text: str) -> AgentResponse:
        """Cached verdict -> answer now; otherwise "Let me look" and the app fetches /parking/answer."""
        place = pick_place(d, sess["places"], _id(req.active_place), req.ui.option_ids) or req.active_place or (sess["places"][0] if sess["places"] else None)
        if not place:
            return AgentResponse(status="chat", transcript=text, reply="Which place? Find one or set a route first.",
                                 source=self.source)
        if is_town(place):
            return AgentResponse(status="chat", transcript=text, source=self.source,
                                 reply="That's a whole town, so there's no parking check. Ask me for a truck stop "
                                       "there or give me an address and I'll check it.")
        pk = approach.best(place)
        if pk and (pk.relative or not approach.has_approach(place) or not pk.entry_tip):
            return AgentResponse(status="chat", transcript=text, reply=approach.speech(pk, approach.asked(text)),
                                 source=self.source)
        _background(check_parking(place))
        return AgentResponse(status="chat", transcript=text, reply=approach.LOOKING, source=self.source,
                             actions=[AppAction(type="parking_followup", value=place.id)])

    @staticmethod
    def _near_followup(d: dict, sess: dict, text: str, last: Optional[Place], category: str) -> Optional[str]:
        """The point to search around: the model's "near", else the saved one for "there" / a new category."""
        near = str(d.get("near") or "").strip()
        if near:
            return near
        if sess.get("near") and re.search(r"\b(?:there|near (?:it|that)|same (?:place|area))\b|^\s*and\s+a\b",
                                          text, re.I):
            return sess["near"]["query"]  # "and a hotel there"
        return None

    async def _find_near(self, req: AgentRequest, category: str, near: str, text: str, exclude: set[str],
                         sess: dict) -> AgentResponse:
        """Places around a point the driver named (delivery, shipper, town); route from the driver."""
        if category not in enabled_categories():
            return AgentResponse(status="chat", transcript=text, category=category, source=self.source,
                                 reply=CATEGORY_OFF.format(what=CATEGORY_WORD.get(category, "that")))
        query = with_city(near, sess.get("city"))
        with stage("tools"):
            matches = await geocode(query, req.lat, req.lng)
        logger.info("agent find near=%r (llm %r) -> %s", query, near, matches[0].name if matches else "no match")
        if not matches:
            return AgentResponse(status="chat", transcript=text, reply=heard_not_found(query), source=self.source)
        point = matches[0]
        sess["city"] = city_of(query, point) or sess.get("city")
        sess["near"] = {"query": query, "lat": point.lat, "lng": point.lng}
        with stage("tools"):
            res = await get_places_provider().search(category, point.lat, point.lng)
        found = [p.model_copy() for p in sorted(res.places, key=lambda p: p.distance_mi or 0)[:MAX_OPTIONS + 2]]
        swapped = False
        if not found and category == "rest_area":  # "park near X" read as rest area: truck stops park trucks too
            category = "truck_stop"
            swapped = True
            with stage("tools"):
                res = await get_places_provider().search(category, point.lat, point.lng)
            found = [p.model_copy() for p in sorted(res.places, key=lambda p: p.distance_mi or 0)[:MAX_OPTIONS + 2]]
        if not found:
            msg = f"I couldn't find a {CATEGORY_WORD[category]} near {query.split(',')[0]}"
            return AgentResponse(status="no_results", transcript=text, category=category, reply=f"{msg}.",
                                 message=msg, source=self.source)
        for p in found[:PRECHECK]:
            _background(check_parking(p))
        chosen = choose_place(found, req.truck_profile, exclude, _facts(req))
        from_point = chosen.distance_mi or 0.0
        # The app's distances are from the driver: recompute them for the list on screen.
        for p in found:
            km = haversine_km(req.lat, req.lng, p.lat, p.lng)
            p.distance_km, p.distance_mi = round(km, 2), round(km_to_mi(km), 1)
        where = "your delivery" if re.search(r"\b(?:deliver|drop|consignee|receiver)", text, re.I) else (
            "the shipper" if re.search(r"\bshipper\b", text, re.I) else query.split(",")[0])
        away = "right at" if from_point < 0.3 else f"{_miles(from_point)} miles from"
        swap = f"No rest area near {query.split(',')[0]}, so here's a truck stop. " if swapped else ""

        def head(route: RouteInfo) -> str:
            return f"{swap}{chosen.name}, {away} {where}, {_miles(route.distance_mi)} miles from you."
        return await self._route_to(req, chosen, found, text, head)

    async def _instead_of_walmart(self, req: AgentRequest, text: str) -> Optional[AgentResponse]:
        """No Walmart around: the closest truck stop is offered (a rest area too when the driver means the night),
        "yes" routes there. "Find a Walmart" ended at "I couldn't find one nearby" (QA 2026-11-02). None when there
        is nothing of those either."""
        kinds = ("truck_stop", "rest_area") if PARK_WORDS.search(text) else ("truck_stop",)
        found: list[Place] = []
        for kind in kinds:
            if kind in enabled_categories():
                with stage("tools"):
                    found += (await get_places_provider().search(kind, req.lat, req.lng)).places
        if not found:
            return None
        nearest = min(found, key=lambda p: p.distance_mi or 0)
        logger.info("agent no Walmart for %s -> offering %s", said(text), nearest.name)
        _session(req.session_id)["pending"] = {"text": text, "kind": "walmart_instead", "yes": "dest_yes",
                                               "no": {"reply": "Okay."},
                                               "d": {"intent": "go", "place": nearest.model_dump()}}
        return AgentResponse(status="confirm", transcript=text, category="walmart", source=self.source,
                             reply=NO_WALMART.format(what=CATEGORY_WORD[nearest.category],
                                                     name=nearest.name.split(",")[0],
                                                     mi=_miles(nearest.distance_mi or 0)))

    async def _find(self, req: AgentRequest, category: str, text: str, exclude: set[str]) -> AgentResponse:
        if category not in enabled_categories():
            return AgentResponse(status="chat", transcript=text, category=category, source=self.source,
                                 reply=CATEGORY_OFF.format(what=CATEGORY_WORD.get(category, "that")))
        chain = chain_named(text) if category == "truck_stop" else None
        if chain:
            named = await self._chain_nearby(req, chain)
            if named:
                chosen = choose_place(named, req.truck_profile, exclude, _facts(req))
                return await self._route_to(req, chosen, named, text)
        with stage("tools"):
            res = await get_places_provider().search(category, req.lat, req.lng)
        found = res.places[:MAX_OPTIONS + 2]
        if chain and found:
            # No such chain within reach: say so, then the usual nearest truck stop.
            chosen = choose_place(found, req.truck_profile, exclude, _facts(req))
            out = await self._route_to(req, chosen, found, text)
            out.reply = f"{NO_CHAIN_NEARBY.format(chain=chain)} {out.reply}"
            return out
        if not found and category == "walmart":
            offer = await self._instead_of_walmart(req, text)
            if offer:
                return offer
        if not found:
            msg = res.message or NO_PLACES
            return AgentResponse(status="no_results", transcript=text, category=category,
                                 reply=f"{msg.rstrip('.')}.", message=msg, source=self.source)
        if category in PARKING_CATEGORIES:
            for p in found[:PRECHECK]:
                _background(check_parking(p))  # warm satellite checks; never wait for them here
        if all(p.id in exclude for p in found):
            # Everything was turned down: offer them again (the driver can now pick or search anew).
            if (s_ := _session_if(req.session_id)) is not None:
                s_.pop("rejected", None)
            return AgentResponse(status="chat", transcript=text, category=category, reply=ALL_REJECTED,
                                 source=self.source)
        chosen = choose_place(found, req.truck_profile, exclude, _facts(req))
        return await self._route_to(req, chosen, found, text)

    async def _handle(self, req: AgentRequest) -> AgentResponse:
        text = (req.text or "").strip()
        if not re.search(r"[^\W\d_]{2,}", text):  # two letters of any script: a Russian request is words, not noise
            return _not_understood(text, self.source)
        sess = _session(req.session_id)
        bd = await breakdown.handle(req, sess, text, self.source)
        if bd:
            return bd
        pending = sess.pop("pending", None)
        if pending:  # "Did you say Reno?" was asked last turn
            kind = pending.get("kind")  # confirm | stop_guard | ...: the answer goes to the turn log and trip events
            extra_yes = YES_PATTERNS.get(pending.get("yes") or "")
            if bare_answer(text, YES) or (extra_yes is not None and bare_answer(text, extra_yes)):
                d = pending["d"]
                timing.note("intent", str(d.get("intent")))
                if kind:
                    timing.note(kind, "accepted")
                return await self._dispatch(req, sess, pending["text"], d, confirmed=True)
            if NO.match(text):
                if kind:
                    timing.note(kind, "declined")
                branch = pending.get("no")
                if branch and branch.get("declined"):
                    declined = sess.setdefault("dest_declined", [])
                    if branch["declined"] not in declined:
                        declined.append(branch["declined"])
                if bare_answer(text, NO):
                    if branch:  # a question with a real "no" path (e.g. "chains on board?")
                        if branch.get("then_pending"):
                            sess["pending"] = branch["then_pending"]
                        acts = [AppAction(type=a["type"], value=a.get("value")) for a in branch.get("actions") or []]
                        return AgentResponse(status="chat", transcript=text, reply=branch["reply"], actions=acts,
                                             source=self.source)
                    return AgentResponse(status="chat", transcript=text, reply=CONFIRM_NO, source=self.source)
                # "no, take me to Fresno": the refusal is noted above, the new request is classified below
            # anything else: the driver moved on, classify it as a fresh utterance
        try:
            d = await asyncio.wait_for(classify(text, self._context(req, sess), req.truck_profile),
                                       timeout=CLASSIFY_TIMEOUT_S)
        except Exception as e:  # noqa: BLE001 - incl. timeout
            logger.warning("agent LLM failed: %s: %s", type(e).__name__, e)
            timing.note("error", "llm_timeout" if isinstance(e, asyncio.TimeoutError) else "llm_error")
            return _error(text, LLM_DOWN, self.source)

        return await self._dispatch(req, sess, text, d)

    @staticmethod
    def _confirm_needed(req: AgentRequest, d: dict, sess: dict) -> Optional[AgentResponse]:
        """Ask once per dialog when speech recognition was unsure about a place or destination name."""
        if not on("STT_CONFIRM") or req.stt_confidence is None or req.stt_confidence >= stt_confirm_below():
            return None
        if d.get("intent") not in ("go", "find", "add_stop") or sess.get("confirmed_once"):
            return None
        what = str(d.get("query") or d.get("near") or "").strip()
        if not what:
            return None
        sess["pending"] = {"d": d, "text": req.text or "", "kind": "confirm"}
        sess["confirmed_once"] = True
        timing.note("confirm", "asked")
        return AgentResponse(status="confirm", transcript=req.text or "", reply=CONFIRM.format(what=what),
                             source=MODEL[1])

    async def _dispatch(self, req: AgentRequest, sess: dict, text: str, d: dict,
                        confirmed: bool = False) -> AgentResponse:
        """Everything after classification: overrides, the rejected set, and the intent switch."""
        intent = d.get("intent")
        timing.note("intent", str(intent) if intent else "none")
        logger.info("agent intent=%s category=%s query=%r place_index=%s", intent, d.get("category"),
                    d.get("query"), d.get("place_index"))
        ask = None if confirmed else self._confirm_needed(req, d, sess)
        if ask:
            return ask
        reply = str(d.get("reply") or "").strip()
        # A control phrase dressed up as a destination (or echoed only as a reply): run the control.
        ctl = (control_phrase(str(d.get("query") or "")) if intent == "go" else None) or \
            (control_phrase(reply) or control_phrase(text) if intent == "chat" else None)
        if ctl:
            logger.info("agent control phrase %r -> %s", d.get("query") or reply, ctl)
            intent, d = "control", {**d, "intent": "control", "actions": [ctl]}
        # Parking question about a town destination answered as chat: give the town line, never a guess.
        if intent == "chat" and req.active_place and is_town(req.active_place) and re.search(r"\bpark", text, re.I):
            intent = "parking"
        if intent == "find" and d.get("category") == "hotel" and PARK_WORDS.search(text) and not BED_WORDS.search(text):
            # "where can I park tonight" is a truck stop, not a hotel: the model drifts there now and then
            # (stand QA 2026-11-01), and a hotel lot is the wrong answer for a 53-footer.
            logger.info("agent find hotel for %s -> truck_stop", said(text))
            d = {**d, "category": "truck_stop"}
        if intent in ("reviews", "chat", "unclear") and FUEL_WORDS.search(text) and PRICE_AT.search(text) \
                and not CHEAPEST.search(text):
            return await self._price_at(req, d, sess, text)
        if intent in ("chat", "unclear") and WHERE_AM_I.search(text):
            return await self._where_am_i(req, text)
        # "Any chain controls ahead?" is a data question, not small talk: the model answered "there may be"
        # on its own (QA 2026-10-31, H17). Caltrans has the answer for the route the driver is on.
        if intent in ("chat", "unclear", "weather") and CHAIN_QUESTION.search(text) and not re.search(r"\b(on board|carry|have chains|got chains)\b", text, re.I):
            return await self._chains_question(req, sess, text)
        # "Cheapest diesel on my route" with a route set: a stop on the way; the destination stays. With a
        # list on screen the model answers pick instead of find (QA 2026-10-31, H9): same rewrite. A pick on
        # "cheapest" over a list without prices points at nothing, so the stop finder chooses along the route.
        if intent in ("find", "pick") and req.active_place and ON_THE_WAY.search(text):
            category = d.get("category") or (sess["places"][0].category if sess["places"] else None) or "truck_stop"
            if category in STOP_CATEGORIES:
                logger.info("agent %s %s %s with a route active -> add_stop", intent, category, said(text))
                d = {**d, "intent": "add_stop", "category": category}
                if intent == "pick" and PRICE_WORDS.search(text) and all(p.diesel_price is None for p in sess["places"]):
                    d["place_index"] = None
                intent = "add_stop"
        # The model's word is not the last word on two things the driver says a lot (QA 2026-10-30, H4):
        # a rejected place read as "another road", and a reference into the list read as an address.
        intent, d = reject_or_reference_guard(intent, d, text, sess["places"], req.active_place,
                                              req.ui.option_ids)
        if intent == "not_understood":
            return _not_understood(text, self.source)
        if intent not in ("add_stop", "clear_stops") and stop_to_drop(text, self._stops(req, sess), req.active_place):
            # "drop the Pilot stop" read as a pick of the Pilot: the driver named one of the route's stops to leave out
            logger.info("agent %s %s names a stop to drop -> clear_stops", intent, said(text))
            intent = "clear_stops"
        if intent not in ("go", "find", "pick", "add_stop", "alternative") and REMEMBER.search(text):
            remembered = await self._remember(req, d, sess, text)
            if remembered:
                return remembered
        try:
            # "The cheapest diesel" over a list with no prices: the model still names one, and the destination
            # changed for no reason the driver could see (QA 2026-10-31, H8). No prices, no switch.
            # "Where's the cheapest diesel" over the list is arithmetic, whatever the model called it: with a route
            # to Roady's it came back as find and the stop finder went to the next stop, one with no price (stand
            # 2026-11-02). The lowest price wins; with no prices the closest is offered (below).
            # Only a pick, or a find/chat that asks for the cheapest of the list, over a list around the truck
            # (review 2026-11-02): "is diesel cheaper in Oregon" is talk, and a Sacramento list is no answer in Reno.
            cheapest = (bool(sess["places"]) and list_is_near(req.lat, req.lng, sess["places"])
                        and (intent == "pick" and CHEAPEST.search(text)
                             or intent in ("find", "chat") and asks_cheapest(text, sess["places"])))
            if (cheapest and (FUEL_WORDS.search(text) or all(p.category == "truck_stop" for p in sess["places"]))
                    and not (req.active_place and ON_THE_WAY.search(text))):
                priced = [p for p in sess["places"] if p.diesel_price is not None]
                if priced:
                    best = min(priced, key=lambda p: p.diesel_price or 0)
                    if req.active_place and best.id == req.active_place.id:
                        return AgentResponse(status="chat", transcript=text, source=self.source,
                                             reply=ALREADY_CHEAPEST.format(name=best.name.split(",")[0], price=best.diesel_price))
                    logger.info("agent cheapest %s -> %s at %.2f", said(text), best.name, best.diesel_price)
                    return await self._route_to(req, best, sess["places"], text)
                intent = "pick" if intent == "find" else intent
            no_prices = bool(sess["places"]) and all(p.category == "truck_stop" and p.diesel_price is None
                                                     for p in sess["places"])
            if no_prices and ((intent == "pick" and d.get("place_index") is not None and PRICE_WORDS.search(text))
                              or (intent in ("pick", "chat") and cheapest)):
                # inside the try: with no route to keep, routing to the closest one can fail (review 2026-11-02, bug 18)
                return await self._cheapest_without_prices(req, sess, text, ask=intent == "chat")
            # "Not that one" just after a stop was added is about the stop (review 2026-11-02, bug 15).
            offered = self._offered_stop(req, sess) if intent in ("pick", "find") else None
            if offered and (REJECT_PLACE.search(text) or (intent == "pick" and d.get("place_index") is None)):
                return await self._reject_stop(req, sess, offered, text)
            # Current pick: the active route, or the last place chosen in this dialog.
            last = req.active_place or (sess["places"][0] if sess["places"] else None)
            current = last.id if last else None
            exclude = {current} if current else set()
            if current and (intent == "pick" or (intent == "find" and last and d.get("category") == last.category)):
                # "not that one" / "something else": never offer an already rejected place again in this dialog.
                rejected = sess.setdefault("rejected", [])
                if current not in rejected and last is not None:
                    await self._note_rejection(sess, last)
                if current not in rejected:
                    rejected.append(current)
                exclude = set(rejected)
            if intent == "breakdown":
                return await breakdown.from_intent(d, req, sess, text, self.source)
            if intent == "alternative":
                return await self._alternative(req, sess, text)
            if intent == "add_stop":
                return await self._add_stop(req, d, sess, text)
            if intent == "clear_stops":
                return await self._clear_stops(req, sess, text, d)
            if intent == "reviews":
                return await self._reviews(req, d, sess, text)
            if intent == "weather":
                return await self._weather(req, d, sess, text)
            if intent == "hos":
                return await self._hos(req, text)
            if intent == "memory":
                return await self._memory_intent(req, sess, d, text)
            if intent == "pick":
                place = pick_place(d, sess["places"], _id(req.active_place), req.ui.option_ids)
                # A named pick ("the second one", "the Pilot") may go back to a place turned down earlier: the
                # driver changed their mind, and "want me to go back to one of them?" is what we offer. Only
                # a bare rejection skips the rejected set, also when the model gave it an index ("something
                # else" with place_index set went back to Roady's, QA 2026-11-02, bug 6).
                explicit = d.get("place_index") is not None and not REJECT_PLACE.search(text)
                if place and place.id != current and (explicit or place.id not in exclude):
                    if explicit:
                        if place.id in sess.get("rejected", []):
                            sess["rejected"].remove(place.id)
                    return await self._route_to(req, place, sess["places"], text)
                category = d.get("category") or (last.category if last else None)
                if category in CATEGORIES:
                    near = self._near_followup(d, sess, text, last, category)
                    if near:
                        return await self._find_near(req, category, near, text, exclude, sess)
                    return await self._find(req, category, text, exclude)
                return _not_understood(text, self.source)
            if intent == "find" and d.get("category") in CATEGORIES:
                near = self._near_followup(d, sess, text, last, d["category"])
                if near:
                    return await self._find_near(req, d["category"], near, text, exclude, sess)
                return await self._find(req, d["category"], text, exclude)
            if intent == "go" and isinstance(d.get("place"), dict):  # a place offered by a nudge, or confirmed below
                if "city" in d:
                    sess["city"] = d["city"] or sess.get("city")
                return await self._route_to(req, Place(**d["place"]), [], text)
            if intent == "go" and str(d.get("query") or "").strip():
                named = named_place(str(d["query"]), sess["places"])
                if named and named.id != current:  # "take me to the 49er": the one on the list, not a geocode
                    logger.info("agent go %r names a listed place -> %s", d["query"], named.name)
                    return await self._route_to(req, named, sess["places"], text)
                query = with_city(str(d["query"]).strip(), sess.get("city"))
                with stage("tools"):
                    matches = await geocode(query, req.lat, req.lng)
                logger.info("agent go query=%r (llm %r) city_ctx=%r -> %s", query, d.get("query"), sess.get("city"),
                            matches[0].name if matches else "no match")
                # A short name that geocodes to a bare street (or nothing): the truck stop of that name nearby,
                # if there is one. "The 49er" in a fresh dialog went to 49ER Trl, Placerville (QA 2026-10-31, H23).
                if not matches or is_street(matches[0]):
                    stop = await self._truck_stop_named(req, str(d["query"]))
                    if stop:
                        return await self._route_to(req, stop, sess["places"], text)
                if not matches:
                    return AgentResponse(status="chat", transcript=text, reply=heard_not_found(query),
                                         source=self.source)
                place = matches[0]
                if place.id in sess.get("dest_declined", []):  # said no to this one already in this dialog
                    return AgentResponse(status="chat", transcript=text, source=self.source,
                                         reply=DEST_ONLY.format(said=str(d["query"]).strip()))
                reason = dest_check(query, place)
                if reason and not confirmed:
                    # "yes" re-runs this turn with the place stashed (no second geocode); "no" remembers the refusal
                    sess["pending"] = {"text": text, "kind": "dest_check",
                                       "d": {**d, "place": place.model_dump(), "city": city_of(query, place)},
                                       "no": {"reply": DEST_NO, "declined": place.id}, "yes": "dest_yes"}
                    timing.note("dest_check", "asked")
                    logger.info("agent go %r -> %s (%s, %.0f mi): asking first", query, place.name, reason,
                                place.distance_mi or 0)
                    return AgentResponse(status="confirm", transcript=text, source=self.source,
                                         reply=DEST_CHECK.format(where=spoken_where(place), mi=_miles(place.distance_mi or 0)))
                if reason:  # one question per utterance: the STT "Did you say...?" was it; the reply names the place
                    timing.note("dest_check", "spoken")
                sess["city"] = city_of(query, place) or sess.get("city")
                return await self._route_to(req, place, [], text)
        except RouteFailed as e:
            return _route_failed(text, e, self.source)
        except (PlacesUnavailable, httpx.HTTPError) as e:
            logger.warning("agent places/geocode failed: %s: %s", type(e).__name__, e)
            timing.note("error", "places_down")
            return _error(text, PLACES_DOWN, self.source)
        except Exception as e:  # noqa: BLE001
            logger.exception("agent action failed: %s: %s", type(e).__name__, e)
            timing.note("error", "broke")
            return _error(text, SOMETHING_BROKE, self.source)
        if intent == "parking":
            if str(d.get("near") or "").strip():  # "where can I park close to my delivery in X": a search
                try:
                    return await self._find_near(req, "truck_stop", str(d["near"]).strip(), text, exclude, sess)
                except RouteFailed as e:
                    return _route_failed(text, e, self.source)
                except (PlacesUnavailable, httpx.HTTPError) as e:
                    logger.warning("agent places/geocode failed: %s: %s", type(e).__name__, e)
                    timing.note("error", "places_down")
                    return _error(text, PLACES_DOWN, self.source)
            return self._parking(req, d, sess, text)
        if intent == "control":
            acts, say, end = controls.apply_actions(d.get("actions"), req)
            if say:
                return AgentResponse(status="chat", transcript=text, reply=say, actions=acts,
                                     end_conversation=end, source=self.source)
            return _not_understood(text, self.source)
        if intent in ("chat", "bye") and reply:
            if intent == "bye":
                sess.pop("rejected", None)
            return AgentResponse(status="chat", transcript=text, reply=fit_reply(reply),
                                 end_conversation=intent == "bye", source=self.source)
        return _not_understood(text, self.source)


_provider = LlmAgentProvider()


def get_agent_provider() -> LlmAgentProvider:
    return _provider
