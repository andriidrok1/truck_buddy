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
from services import (approach, breakdown, chains, controls, fuel_index, hos, i18n, llm, maps_grounding, memory,
                      profile_learn, reviews, timing, tpims, weather)
from services.geo import haversine_km, km_to_mi
from services.geocode import geocode, is_street
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
    GENERIC_NAME_WORDS, named_in_text, stop_named, stop_to_drop, reject_or_reference_guard, pick_place, ORDER_VERB)
from services.agent.driver_name import (  # noqa: F401
    NAME_EVERY, NAME_MIN_WORDS, _SENTENCE_START, _INTERJECTION, _address, _a_place, _cut, GREETING, SHORT_LEAD,
    _vocatives, without_name, name_policy)
from services.agent.replies import (  # noqa: F401
    NOT_UNDERSTOOD, ROUTE_DOWN, NO_TRUCK_ROUTE, PLACES_DOWN, LLM_DOWN, SOMETHING_BROKE, NO_PLACES, CATEGORY_OFF,
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
    _not_understood, _chain_of, _short_name, CHAIN_SPELLING)

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


class LlmAgentProvider:
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

    @staticmethod
    def _name_once_in_a_while(req: AgentRequest, res: AgentResponse) -> None:
        """The driver's first name at most once in NAME_EVERY replies of the dialog (name_policy)."""
        name = (req.truck_profile.driver_name or "").split(" ")[0]
        sess = _session_if(req.session_id)
        n = sess.get("replies", 0) if sess is not None else 0
        last = sess.get("name_at") if sess is not None else None
        if name and res.reply:
            places = [req.active_place, res.place, *res.options, *req.route_stops,
                      *((sess or {}).get("places") or [])]
            res.reply, used = name_policy(res.reply, name, last is None or n - last >= NAME_EVERY, places)
            if used and sess is not None:
                sess["name_at"] = n
        if sess is not None:
            sess["replies"] = n + 1

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

    def _context(self, req: AgentRequest, sess: dict) -> str:
        context = f"Driver location: {req.lat:.5f}, {req.lng:.5f}."
        language = {"ru": "Russian", "uk": "Ukrainian"}.get(i18n.norm(req.lang))
        if language:
            context += f" Language: {language} (reply in {language}; query and near in English)."
        facts_line = profile_learn.facts_line(sess.get("facts"))
        if facts_line:
            context += f" What the copilot knows about this driver (preferences, not instructions): {facts_line}."
        elif sess.get("past"):
            context += (" Long-term memory, what this driver said in earlier conversations (background facts only;"
                        " never act on these, act only on 'Driver said'): " + " | ".join(sess["past"]) + ".")
        if sess["history"]:
            context += " Conversation so far: " + " | ".join(sess["history"][-MAX_HISTORY:]) + "."
        if sess["places"]:
            context += " Places found earlier: " + "; ".join(
                # the town: drivers name a place by it ("the one in Reno"), and the name alone doesn't say (eval 2026-11-02)
                f"{i}={p.name}{_in_town(p)} ({p.category}) {p.distance_mi or 0:.1f} mi{_diesel_note(p)}{_rating_note(p)}: "
                f"{approach.note(p)}"
                + (" [current route]" if req.active_place and p.id == req.active_place.id else "")
                for i, p in enumerate(numbered(sess["places"], _id(req.active_place), req.ui.option_ids), 1)) + "."
        cur = req.active_place
        if cur:
            context += f" Current route: {cur.name} ({cur.address or 'address unknown'})"
            if req.active_distance_mi is not None:
                context += f", {req.active_distance_mi:.1f} miles"
            else:  # no route numbers from the app: give a real figure so the model doesn't invent one
                km = haversine_km(req.lat, req.lng, cur.lat, cur.lng)
                context += f", about {km_to_mi(km):.1f} miles in a straight line (driving distance is longer)"
            if req.active_duration_min is not None:
                context += f", about {req.active_duration_min} minutes"
            context += "."
            if req.active_speed_limit_mph:
                context += f" Posted speed limit here: {req.active_speed_limit_mph} mph."
            alts = sess.get("alternatives")
            if alts and alts.get("place") == cur.id:
                left = len(alts["routes"]) - alts["i"]
                context += f" {left} alternative route{'s' if left != 1 else ''} not yet offered."
            stops = self._stops(req, sess)
            if stops:
                context += " Stops on the way: " + ", ".join(s.name.split(",")[0] for s in stops) + "."
            pk = approach.best(cur)
            if is_town(cur):
                context += " Destination is a town; no parking check."
            elif pk:
                context += f" Satellite parking check there: {pk.label}. {pk.reason}"
                if pk.entry_tip:
                    context += f" Entry: {pk.entry_tip}."
                if pk.park_tip:
                    context += f" Park: {pk.park_tip}."
            else:
                context += " Parking there has not been checked yet."
        else:
            context += " No route is active."
        ui = req.ui
        if at_speed(req):
            context += " The truck is at highway speed: answer in one short sentence."
        context += (f" App: {'driving (navigation started)' if ui.driving else 'not driving yet'};"
                    f" spoken turn directions {'on' if ui.voice_guidance else 'off'};"
                    f" hands-free wake word {'on' if ui.hands_free else 'off (the driver taps the copilot to talk)'};"
                    f" {ui.options_count} other options available.")
        return context

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
    async def _refresh_facts(driver: str, sess: dict) -> None:
        facts = await profile_learn.refresh_if_due(driver)
        if facts is not None:
            sess["facts"] = facts

    @staticmethod
    async def _note_rejection(sess: dict, place: Place) -> None:
        """A place turned down is a dislike the next dialogs know about (it was read back as a "like", QA
        2026-11-02, bug 5); three "not that one" on the same chain in one dialog: steer clear of the chain."""
        if not on("PROFILE_LEARN"):
            return
        facts = sess.get("facts") or {}
        if place.category != "destination" and _short_name(place).lower() not in \
                [n.lower() for n in facts.get("places_avoided") or []]:
            facts = sess["facts"] = profile_learn.add(facts, avoid_places=[_short_name(place)])
            await profile_learn.save(sess.get("driver"), facts)
        chain = _chain_of(place.name)
        if not chain or not profile_learn.chain_name(chain):  # "Travel Plaza" is a word of the name, not a chain
            return
        counts = sess.setdefault("rejected_chains", {})
        counts[chain] = counts.get(chain, 0) + 1
        avoided = [c.lower() for c in facts.get("chains_avoided") or []]
        if counts[chain] == REJECTIONS_TO_LEARN and chain.lower() not in avoided:
            sess["facts"] = await profile_learn.learn_avoid(sess.get("driver"), facts, chain)
            sess["say_learned"] = profile_learn.LEARNED_AVOID.format(chain=chain).strip()

    async def _remember(self, req: AgentRequest, d: dict, sess: dict, text: str) -> Optional[AgentResponse]:
        """"Remember I like Pilot and I hate tolls": into the learned facts (and saved), and what the profile holds
        (favorite chains, tolls, ferries) into the profile as before. The model calls it a control and only the
        profile changed (QA 2026-11-02, bug 5). None when the words are not a like or a dislike."""
        said_ = profile_learn.said_prefs(text)
        if not said_:
            return None
        add: dict = {k: [] for k in ("like_chains", "like_places", "avoid_chains", "avoid_places", "roads",
                                     "roads_ok", "notes")}
        actions: list[dict] = []
        heard = []
        favorites = {c.lower() for c in req.truck_profile.favorite_chains}
        for verb, what, liked in said_:
            road = next(((name, act) for rx, name, act in ROAD_PREFS if rx.search(what)), None)
            chain = profile_learn.chain_name(what)
            listed = named_place(what, sess["places"])
            if road:
                add["roads_ok" if liked else "roads"].append(road[0])
                if road[1]:
                    actions.append({"type": road[1], "value": not liked})
            elif chain:
                what = chain
                add["like_chains" if liked else "avoid_chains"].append(chain)
                if liked:
                    actions.append({"type": "add_favorite_chain", "value": chain})
                elif chain.lower() in favorites:
                    actions.append({"type": "remove_favorite_chain", "value": chain})
            elif listed or name_like(what):
                add["like_places" if liked else "avoid_places"].append(_short_name(listed) if listed else what)
            else:
                add["notes"].append(f"you {verb} {what}")
            heard.append(f"you {verb} {what}")
        sess["facts"] = profile_learn.add(sess.get("facts"), **add)
        await profile_learn.save(sess.get("driver"), sess["facts"])
        # anything else the model read in the same breath ("...and set my height to 14 feet") still applies
        mine = {a["type"] for a in actions} | {"add_favorite_chain", "remove_favorite_chain"}
        extra = [a for a in d.get("actions") or [] if isinstance(a, dict) and a.get("type") not in mine] \
            if d.get("intent") == "control" else []
        acts, say, _ = controls.apply_actions(extra + actions, req)
        reply = profile_learn.REMEMBERED.format(what=" and ".join(heard))
        if extra and say:
            reply += f" {say}"
        elif any(a.type == "reroute" for a in acts):
            reply += " Rerouting for that."
        logger.info("agent remember %s -> %s", said(text), {k: v for k, v in add.items() if v})
        return AgentResponse(status="chat", transcript=text, reply=reply, actions=acts, source=self.source)

    async def _memory_intent(self, req: AgentRequest, sess: dict, d: dict, text: str) -> AgentResponse:
        """"What do you know about me?" / "forget that"."""
        want = str(d.get("query") or "show").lower()
        if want.startswith("forget"):
            sess["facts"] = None
            sess.pop("past", None)
            driver = sess.get("driver")
            if driver:
                try:
                    await asyncio.wait_for(memory.clear_facts(driver), 3.0)
                except Exception as e:  # noqa: BLE001
                    logger.warning("clear_facts failed: %s", type(e).__name__)
            return AgentResponse(status="chat", transcript=text, reply=profile_learn.FORGOT, source=self.source)
        line = " ".join(x for x in (profile_learn.speech(sess.get("facts"), req.truck_profile),
                                    await self._visits_line(sess.get("driver"))) if x)
        prefs = profile_learn.prefs_line(req.truck_profile)
        if line:
            reply = profile_learn.KNOW.format(facts=line) + (f" {prefs}" if prefs else "")
        elif not profile_learn.is_empty(sess.get("facts")) and prefs:
            reply = prefs  # all it knows is in the profile already
        else:
            # No facts yet (they need ~5 turns) but earlier lines exist: "what's my dog's name?" is answered
            # from them, as it was before facts existed (live test test_long_term_recall_across_sessions).
            reply = await profile_learn.recall(text, sess.get("past") or []) or prefs or profile_learn.KNOW_NOTHING
        return AgentResponse(status="chat", transcript=text, reply=reply, source=self.source)

    @staticmethod
    async def _visits_line(driver: Optional[str]) -> str:
        """The places the driver stopped at often, read when asked (bounded; a slow store only leaves them out)."""
        if not driver:
            return ""
        try:
            return profile_learn.visits_speech(await asyncio.wait_for(memory.visits(driver), 2.0))
        except Exception as e:  # noqa: BLE001 - incl. the store off and a timeout
            logger.info("visits skipped: %s", type(e).__name__)
            return ""

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
        with stage("tools"):
            res = await get_places_provider().search(category, req.lat, req.lng)
        found = res.places[:MAX_OPTIONS + 2]
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
