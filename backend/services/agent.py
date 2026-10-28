"""Copilot agent: one structured LLM classification per turn, then the backend acts.

intent=find -> Places search + rule-based pick; go -> geocode + truck route; pick -> a place
found earlier in this dialog; chat/bye -> the model's short reply from the same call.
Route answers are templated (one sentence) and parking directions follow from the app.
"""
import asyncio
import json
import logging
import os
import re
from collections import OrderedDict
from typing import Optional

import httpx

from models import MAX_VIA, AgentRequest, AgentResponse, AppAction, Place, RouteInfo, TruckProfile
from services import approach, breakdown, chains, controls, fuel_index, hos, llm, memory, reviews, tpims, weather
from services.geo import haversine_km, km_to_mi
from services.geocode import geocode
from services.google_places import TRUCK_STOP_NAME, PlacesUnavailable
from services.parking import cached_parking, check_parking, is_town, listed_in_data
from services.places import get_places_provider
from services.routing import get_routing_provider
from services.timing import stage

logger = logging.getLogger("truckbuddy.agent")

NOT_UNDERSTOOD = "Sorry, I didn't catch that. Try again."
ROUTE_DOWN = "I couldn't build a truck route there right now. Try again in a minute."
PLACES_DOWN = "Place search is down right now. Give me an address instead."
LLM_DOWN = "Give me a second, I lost my train of thought. Say that again."
SOMETHING_BROKE = "Something went wrong on my end. Try again in a minute."
CLASSIFY_TIMEOUT_S = 20


class RouteFailed(Exception):
    pass


def _error(text: str, reply: str, source: str) -> "AgentResponse":
    """Infrastructure trouble: a distinct spoken line + a toast, never "didn't catch that"."""
    return AgentResponse(status="error", transcript=text, reply=reply, message=reply, source=source)


# Control phrases the model sometimes returns as a destination (or only as a reply).
_CONTROL_PHRASES: list[tuple[re.Pattern, dict]] = [
    (re.compile(r"\b(?:start|begin)\s+(?:the\s+)?(?:route|ride|trip|navigation|driving)\b|\blet'?s\s+go\b|"
                r"\bgo\s+ahead\b|\bthe\s+stars\s+are\s+out\b", re.I), {"type": "start_navigation"}),
    (re.compile(r"\b(?:stop|end|cancel)\s+(?:the\s+)?(?:route|ride|trip|navigation)\b", re.I), {"type": "end_route"}),
    (re.compile(r"\bturn\s+(?:the\s+)?directions\s+off\b|\bturn\s+off\s+(?:the\s+)?directions\b|\bmute\b", re.I),
     {"type": "voice_guidance", "value": False}),
    (re.compile(r"\bturn\s+(?:the\s+)?directions\s+on\b|\bturn\s+on\s+(?:the\s+)?directions\b", re.I),
     {"type": "voice_guidance", "value": True}),
    (re.compile(r"\bre-?center\b", re.I), {"type": "recenter"}),
]


def control_phrase(text: str) -> Optional[dict]:
    """The control a phrase asks for, if it is only a control (no digits, no place-like proper nouns)."""
    if not text or re.search(r"\d", text):
        return None
    for rx, action in _CONTROL_PHRASES:
        m = rx.search(text)
        if not m:
            continue
        rest = (text[:m.start()] + " " + text[m.end():]).strip(" .,!?")
        words = rest.split()
        if len(words) <= 3 and not any(w[0].isupper() for w in words[1:] if w[0].isalpha()):
            return action
    return None
NO_PLACES = "I couldn't find one nearby"
CATEGORY_WORD = {"truck_stop": "truck stop", "hotel": "hotel", "rest_area": "rest area", "walmart": "Walmart",
                 "cat_scale": "CAT scale", "truck_wash": "truck wash", "weigh_station": "weigh station"}
CORE_CATEGORIES = ("truck_stop", "hotel", "rest_area")
EXTRA_CATEGORIES = ("walmart", "cat_scale", "truck_wash", "weigh_station")
# Satellite parking checks only where a truck would park; a scale or a wash is not a place to spend the night.
PARKING_CATEGORIES = {"truck_stop", "hotel", "rest_area", "walmart", "destination"}
CATEGORY_OFF = "I can't look up {what}s yet. Ask me for a truck stop, a hotel or a rest area."


def enabled_categories() -> tuple[str, ...]:
    """Core categories plus whatever CATEGORIES_EXTRA lists (comma-separated, e.g. 'walmart,cat_scale').
    Off by default until the app knows how to draw them."""
    extra = {c.strip() for c in os.environ.get("CATEGORIES_EXTRA", "").split(",") if c.strip()}
    return CORE_CATEGORIES + tuple(c for c in EXTRA_CATEGORIES if c in extra or "all" in extra)
ALL_REJECTED = "That's every one I found nearby. Want me to go back to one of them?"
_STREET_ONLY = re.compile(r"^\s*\d+[a-z]?\s+[\w .'-]+$", re.I)  # "2828 El Centro Rd" (no town after a comma)
_ZIP_COUNTRY = re.compile(r"^\s*(?:[A-Z]{2}(?:\s+\d{5}(?:-\d{4})?)?|\d{5}|United States|USA)\s*$")


def heard_not_found(query: str) -> str:
    """Reads back what was heard and offers typing."""
    return f"I heard {query}, but can't find it. Say it again or type it."


def with_city(query: str, city: Optional[str]) -> str:
    """Street address alone after a turn that named a city: search it in that city."""
    return f"{query}, {city}" if city and _STREET_ONLY.match(query) else query


def city_of(query: str, place: Place) -> Optional[str]:
    """Town named in this turn ("Reno, NV" or "..., Sacramento, CA"), for a follow-up street address."""
    parts = [p.strip() for p in query.split(",") if p.strip()]
    if parts and not re.search(r"\d", parts[0]):  # a town ("Reno, NV"), not a landmark that geocoded elsewhere
        return query if place.name.split(",")[0].strip().lower() == parts[0].lower() else None
    if len(parts) > 1 and re.search(r"\d", parts[0]):
        rest = [p for p in parts[1:] if not _ZIP_COUNTRY.match(p)]
        return ", ".join(rest) or None
    return None
MODEL = ("openai", "gpt-5.4-mini")
MAX_OPTIONS = 4
ALTERNATIVES_FRESH = 2  # when "another way?" comes with no cached alternatives: ask HERE for this many
WEATHER_BUDGET_S = 2.0  # NWS lookups on a new route may add at most this much to the reply
PRECHECK = 1  # nearest candidates satellite-checked in the background (each = one vision call)
FAVORITE_EXTRA_MI = 5.0
TRUCK_TAGS = ("truck parking", "53 ft", "truck lot", "hgv")


def is_fuel_stop(p: Place) -> bool:
    """A truck_stop result with no truck-stop name and a satellite "unlikely": just a gas station."""
    return p.category == "truck_stop" and not TRUCK_STOP_NAME.search(p.name) and _verdict(p) == "unlikely"
CATEGORIES = CORE_CATEGORIES + EXTRA_CATEGORIES  # all the classifier may name; enabled_categories() gates

SYSTEM = """You are TruckBuddy, a hands-free voice copilot for a long-haul truck driver who is driving.
Truck: {truck}.
Driver preferences: {prefs}.

The driver's words come from speech recognition in a noisy cab, so they may be misheard. Read them by what
makes sense for a trucker and act on the corrected meaning, e.g. "I need a few" = "I need fuel",
"the stars are out" / "start the ride" = "start the route", "rest a ride" = "rest area", "diesel" heard as
"these all". Only fix words that clearly don't fit; never invent a destination. If you can't tell, use "unclear".

Classify the driver's LAST utterance and answer with JSON only, no other text:
{{"intent": "find"|"go"|"pick"|"alternative"|"add_stop"|"clear_stops"|"reviews"|"weather"|"hos"|"parking"|"control"|"chat"|"bye"|"unclear", "category": "truck_stop"|"hotel"|"rest_area"|"walmart"|"cat_scale"|"truck_wash"|"weigh_station"|null, "near": string|null, "query": string|null, "when": string|null, "place_index": integer|null, "actions": array|null, "reply": string|null}}

- find: the driver needs a place. Pick the category yourself, never ask:
  truck_stop = fuel, diesel, DEF, food, showers, truck stop;
  hotel = sleep, tired, overnight, bed, motel, hotel, room;
  rest_area = quick break, stretch, restroom, short nap, rest area;
  walmart = a Walmart, "park at a Walmart", overnight in a Walmart lot;
  cat_scale = CAT scale, certified scale, weigh the load or axles;
  truck_wash = truck wash, wash the truck or trailer, Blue Beacon;
  weigh_station = weigh station, scale house, DOT inspection station, port of entry, chicken coop.
  near = the address, town or landmark the driver wants it close to ("find a truck stop near 2828 El Centro Rd" ->
  "2828 El Centro Rd"; "where can I park close to my delivery in Stockton" -> "Stockton, CA"; "staging near the
  shipper on Maritime St" -> "Maritime St"); digits for numbers; null when they mean near themselves/on the way.
  For a follow-up like "and a hotel there", repeat the near point used before.
  "park near X" / "where can I park close to my delivery in X" / "staging near X" = find truck_stop with near X
  (never rest_area or parking for these).
- go: the driver names a destination (city, address, business, landmark: "take me to Reno", "go to 1200 Main St Dallas"). query = the destination, e.g. "Reno, NV". Write numbers as digits ("2828 El Centro Rd, Sacramento"). If the driver gives only a street address and named a town earlier in the conversation, add that town to query.
- pick: the driver refers to a place found earlier in this conversation ("the second one", "the Pilot", "not that one", "something closer", "the cheapest diesel": lowest "diesel $" in the list). place_index = the number of the best matching place in "Places found earlier" (1, 2, 3...), never the current route. If no earlier place fits, use find with the same category instead.
- alternative: a different road to the SAME destination ("another way", "different route", "is there a faster way", "can we skip the grade", "go around that"). Never a new place. Only meaningful while a route is active; otherwise answer as chat.
- add_stop: a stop ON THE WAY to the current destination, keeping that destination ("stop at the Pilot in Fernley on the way", "swing by Love's first", "add a fuel stop", "I need a rest area before that"). query = the place or address named (digits for numbers), else category = the kind of stop (truck_stop for fuel/food/showers, rest_area, hotel); never both null. Only while a route is active; with no route it's a plain find or go.
- clear_stops: drop the stops added on the way ("skip the stops", "forget the stop", "no stops, straight there").
- reviews: what other drivers say about a place ("what do drivers say about it", "is it any good", "reviews for the Pilot", "how are the showers there"). place_index = the place asked about from "Places found earlier", or null for the current route / the last one mentioned.
- hos: the driver's hours of service ("how many hours do I have", "how much drive time is left", "when do I have to stop", "where's my clock at", "how long till my break"). No other fields.
- weather: the weather or wind at a place and time ("weather in Reno at 3pm", "how's the wind over Donner tonight", "is it snowing ahead", "what's it like at the destination"). query = the place named (null for here, or for the destination when they say there/ahead), when = the time words as said ("3pm", "tonight", "tomorrow morning", null for now).
{controls}
{parking}
{breakdown}
- chat: a question or remark that is not a place request ("how far is it?", "where do I park?", "thanks"). reply = a brief answer using the context. For parking/entry questions use only the satellite parking check; if there is none, say you haven't checked parking there yet. Never invent parking details.
- bye: goodbye or done ("bye", "that's all", "no, I'm good", "that's it"). reply = one short friendly line.
- unclear: only a sound or noise label (siren, beeping, music) or unintelligible.
reply is only for chat and bye: English, spoken style, no markdown, at most 2 short sentences, never repeat yourself.
"""

MAX_HISTORY = 8
PAST_TURNS = 80  # long-term memory fed to a new dialog (the driver's lines among the last 80 stored turns)
_SESSIONS: "OrderedDict[str, dict]" = OrderedDict()
GOODBYE = re.compile(r"\b(bye|goodbye|that's all|that is all|that's it|i'm good|im good|no thanks|nothing else|see you)\b", re.I)


def _session(sid: Optional[str]) -> dict:
    """Short-lived conversation memory (per dialog, in process)."""
    if not sid:
        return {"history": [], "places": []}
    if sid not in _SESSIONS:
        _SESSIONS[sid] = {"history": [], "places": []}
        while len(_SESSIONS) > 200:
            _SESSIONS.popitem(last=False)
    _SESSIONS.move_to_end(sid)
    return _SESSIONS[sid]


_tasks: set = set()


def _background(coro) -> None:
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


def _street(address: Optional[str]) -> str:
    """Landmark for speech: the street/exit without the house number, or ''."""
    if not address:
        return ""
    first = address.split(",")[0].strip()
    first = re.sub(r"^\d+[\w-]*\s+", "", first)  # drop the house number
    return first if first and not first[0].isdigit() else ""


def _miles(mi: float) -> str:
    return f"{mi:.1f}".removesuffix(".0") if mi < 10 else f"{mi:,.0f}"


# (intro, body): the short intro is the first spoken part; variants rotate per dialog.
TEMPLATES = {
    "truck_stop": [
        ("Alright, got one", "{name}{off}, about {mi} miles out."),
        ("Okay", "{name} is about {mi} miles ahead{up}."),
        ("Here's a good one", "{name}{off}, {mi} miles away."),
        ("Found you a stop", "{name}{up}, about {mi} miles."),
    ],
    "hotel": [
        ("Alright, found you a bed", "{name}{off}, about {mi} miles out."),
        ("Okay", "{name} is about {mi} miles ahead{up}."),
        ("Here's a good one for the night", "{name}{off}, {mi} miles away."),
        ("Got a place for you", "{name}{up}, about {mi} miles."),
    ],
    "rest_area": [
        ("Alright, got a spot to stretch", "{name}{off}, about {mi} miles out."),
        ("Okay", "{name} is about {mi} miles ahead{up}."),
        ("Here's a good one", "{name}{off}, {mi} miles away."),
        ("Found you a break", "{name}{up}, about {mi} miles."),
    ],
    "destination": [
        ("Alright", "Route to {dest} is set, {time}, about {mi} miles."),
        ("Okay, we're headed to {dest}", "That's {time}, {mi} miles."),
        ("Got it", "{dest} it is, {time} and {mi} miles."),
    ],
    "walmart": [
        ("Found a Walmart", "{name}{off}, about {mi} miles out. Big lot, but overnight is up to the store manager."),
        ("Okay", "{name} is about {mi} miles ahead{up}. Ask inside before you stay the night."),
        ("Here's one", "{name}{off}, {mi} miles away. Check with the store about parking overnight."),
    ],
    "cat_scale": [
        ("Got a scale for you", "CAT scale at {name}{off}, about {mi} miles out."),
        ("Okay", "Nearest CAT scale is at {name}, about {mi} miles ahead{up}."),
        ("Here's one", "CAT scale at {name}{off}, {mi} miles away."),
    ],
    "truck_wash": [
        ("Found a wash", "{name}{off}, about {mi} miles out."),
        ("Okay", "{name} is about {mi} miles ahead{up}."),
        ("Here's one", "{name}{off}, {mi} miles away."),
    ],
    "weigh_station": [
        ("Got one", "{name}{off}, about {mi} miles out. Open or closed, watch the sign."),
        ("Okay", "{name} is about {mi} miles ahead{up}. I can't see if it's open."),
        ("Here's the nearest", "{name}{off}, {mi} miles away. Check the sign for open or closed."),
    ],
    # truck_stop results that are really gas stations: honest wording.
    "fuel_stop": [
        ("Closest I've got is a fuel stop", "{name}{off}, about {mi} miles out, might be tight for a rig."),
        ("Okay, it's a fuel stop, not a truck stop", "{name} is about {mi} miles ahead{up}."),
    ],
}
NOTICES = (
    ("toll", "Heads up, couldn't dodge all the tolls on this one."),
    ("restricted", "Heads up, part of this route might be tight for your rig, so watch the signs."),
    ("ferry", "Heads up, there's a ferry on this one."),
)
CLOSURE = "Heads up, there's a road closure on this route: {description}."
ALT_REPLY = "Here's another way{via}, {time}, {mi} miles{tolls}."
NO_ALT = "That's the only truck route I've got."
ALT_NO_ROUTE = "Set a route first and I'll look for other ways."
VIA_ADDED = "Added {name}. New total {time}, {mi} miles."
VIA_NO_ROUTE = "Set a route first, then I can add stops on the way."
VIA_FULL = "I can only add three stops. Say 'clear the stops' to start over."
VIA_CLEARED = "Stops cleared. Straight there, {time}, {mi} miles."
VIA_NONE = "There are no stops on this route."
WHICH_PLACE = "Which place? Find one or set a route first."


def _alt_reply(route: RouteInfo) -> str:
    via = f" via {' and '.join(route.labels[:2])}" if route.labels else ""
    tolls = f", about ${route.tolls_usd:,.0f} in tolls" if route.tolls_usd else ""
    return ALT_REPLY.format(via=via, time=route.summary.split(" · ")[0], mi=_miles(route.distance_mi), tolls=tolls)
_turns: dict = {"n": 0}  # rotation when there's no dialog session


def _notice(raw: str) -> str:
    low = raw.lower()
    return next((say for key, say in NOTICES if key in low), "Heads up, check this route for restrictions.")


def _route_warning(route: RouteInfo) -> str:
    """One spoken heads-up for a new route: a closure beats a router notice; nothing when there's neither."""
    closed = next((i for i in route.incidents if i.closed), None)
    if closed:
        return CLOSURE.format(description=closed.description)
    return _notice(route.notices[0]) if route.notices else ""


def _diesel_note(p: Place) -> str:
    """Price tag for the model's place list, so "the cheapest diesel" resolves to a place_index."""
    if p.diesel_price is None:
        return ""
    return f", diesel ${p.diesel_price:.2f}" + ("" if p.diesel_kind == "truck" else " (car pumps)")


def _diesel_sentence(place: Place, regional: str = "") -> str:
    """Spoken price for a fuel-type place (+ the regional comparison when known); nothing without a fresh price."""
    if place.diesel_price is None or place.category != "truck_stop":
        return ""
    if place.diesel_kind == "truck":
        return f" Diesel's ${place.diesel_price:.2f} there.{regional}"
    return f" Diesel's ${place.diesel_price:.2f} at the car pumps, truck lanes may differ.{regional}"


def _rating_note(p: Place) -> str:
    """For the model's place list: ", 4.3 stars (120 reviews)" so "is it any good?" has something to go on."""
    if p.rating is None:
        return ""
    return f", {p.rating:.1f} stars" + (f" ({p.rating_count} reviews)" if p.rating_count else "")


def _route_reply(place: Place, route: RouteInfo, n: int = 0, driver: Optional[str] = None,
                 regional: str = "") -> str:
    """Conversational intro + one line with the place, a landmark and the distance; then a notice, if any.
    The driver's name is used about every third answer. `regional` = EIA comparison for the diesel price."""
    kind = "fuel_stop" if is_fuel_stop(place) else place.category
    intro, body = TEMPLATES[kind][n % len(TEMPLATES[kind])]
    street = _street(place.address)
    dest = place.name.split(",")[0]
    vals = {"name": place.name, "dest": dest, "mi": _miles(route.distance_mi), "time": route.summary.split(" · ")[0],
            "off": f", right off {street}" if street else "", "up": f", just up {street}" if street else ""}
    intro = intro.format(**vals)
    first_name = (driver or "").split(" ")[0]
    out = f"{intro}, {first_name}. " if first_name and n % 3 == 2 else f"{intro}. "
    out += body.format(**vals) + _diesel_sentence(place, regional) + tpims.sentence(place)
    warning = _route_warning(route)
    return f"{out} {warning}" if warning else out


def pick_place(d: dict, places: list[Place]) -> Optional[Place]:
    """Resolves the model's 1-based place_index into a place found earlier."""
    try:
        i = int(d.get("place_index"))
    except (TypeError, ValueError):
        return None
    return places[i - 1] if 1 <= i <= len(places) else None


def _not_understood(text: str, source: str) -> AgentResponse:
    return AgentResponse(status="not_understood", transcript=text, reply=NOT_UNDERSTOOD,
                         message=NOT_UNDERSTOOD, source=source)


def _verdict(p: Place) -> Optional[str]:
    c = cached_parking(p)
    return c.verdict if c else ("listed" if listed_in_data(p) else None)


def choose_place(found: list[Place], truck: TruckProfile, exclude: set[str]) -> Optional[Place]:
    """Favorite chain within +5 mi of the nearest, then truck parking listed/likely, then the closest.
    "unlikely" parking only if nothing else is left."""
    pool = [p for p in found if p.id not in exclude] or found
    if not pool:
        return None
    ok = [p for p in pool if not is_fuel_stop(p) and _verdict(p) != "unlikely"] or \
        [p for p in pool if not is_fuel_stop(p)] or pool
    dist = lambda p: p.distance_mi or 0.0  # noqa: E731
    nearest = min(dist(p) for p in ok)
    chains = [re.compile(rf"\b{re.escape(c)}\b", re.I) for c in truck.favorite_chains if c.strip()]
    fav = [p for p in ok if any(rx.search(p.name) for rx in chains) and dist(p) <= nearest + FAVORITE_EXTRA_MI]
    if fav:
        return min(fav, key=dist)
    good = [p for p in ok if _verdict(p) in ("listed", "likely") or any(w in t.lower() for t in p.tags for w in TRUCK_TAGS)
            or (p.category == "truck_stop" and TRUCK_STOP_NAME.search(p.name))]
    return min(good or ok, key=dist)


def _parse(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


async def classify(text: str, context: str, truck: TruckProfile) -> dict:
    system = SYSTEM.format(truck=truck.label, prefs=truck.prefs_label, controls=controls.PROMPT,
                           parking=approach.PROMPT, breakdown=breakdown.PROMPT)
    with stage("llm_round_1"):
        raw = await llm.complete(system, f"[{context}]\nDriver said: {text}", MODEL[1])
    return _parse(raw)


class LlmAgentProvider:
    source = MODEL[1]

    async def handle(self, req: AgentRequest, driver: Optional[str] = None) -> AgentResponse:
        if driver and not req.session_id:
            req.session_id = f"{driver}:solo"[:64]  # typed turns outside a dialog still use memory
        if driver and req.session_id and req.session_id not in _SESSIONS:
            try:  # a new dialog starts with what the driver told the copilot in earlier ones
                past = await asyncio.wait_for(memory.recent(driver, PAST_TURNS), 3.0)
            except Exception:  # noqa: BLE001 - a slow or down database must not delay the reply
                logger.warning("memory load failed or timed out")
                past = []
            _session(req.session_id)["past"] = [t["text"][:200] for t in past if t["role"] == "driver"]
        res = await self._handle(req)
        if res.status == "chat" and GOODBYE.search(req.text or ""):
            res.end_conversation = True
        if driver and res.status != "not_understood":  # saved in the background: the reply (and TTS) never waits on the DB
            memory.add_later(driver, [("driver", (req.text or "").strip()[:500]), ("copilot", res.reply[:500])])
        if req.session_id:
            sess = _session(req.session_id)
            if req.text:
                sess["history"].append(f"Driver: {req.text.strip()[:200]}")
            sess["history"].append(f"Copilot: {res.reply[:200]}")
            del sess["history"][:-MAX_HISTORY * 2]
            if res.place:
                sess["places"] = ([res.place] + list(res.options))[:MAX_OPTIONS + 1]
        return res

    def _context(self, req: AgentRequest, sess: dict) -> str:
        context = f"Driver location: {req.lat:.5f}, {req.lng:.5f}."
        if sess.get("past"):
            context += (" Long-term memory, what this driver said in earlier conversations (background facts only;"
                        " never act on these, act only on 'Driver said'): " + " | ".join(sess["past"]) + ".")
        if sess["history"]:
            context += " Conversation so far: " + " | ".join(sess["history"][-MAX_HISTORY:]) + "."
        if sess["places"]:
            context += " Places found earlier: " + "; ".join(
                f"{i}={p.name} ({p.category}) {p.distance_mi or 0:.1f} mi{_diesel_note(p)}{_rating_note(p)}: "
                f"{approach.note(p)}"
                + (" [current route]" if req.active_place and p.id == req.active_place.id else "")
                for i, p in enumerate(sess["places"], 1)) + "."
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
        context += (f" App: {'driving (navigation started)' if ui.driving else 'not driving yet'};"
                    f" spoken turn directions {'on' if ui.voice_guidance else 'off'};"
                    f" hands-free wake word {'on' if ui.hands_free else 'off (the driver taps the copilot to talk)'};"
                    f" {ui.options_count} other options available.")
        return context

    async def _route_to(self, req: AgentRequest, place: Place, found: list[Place], text: str) -> AgentResponse:
        with stage("tools"):
            try:
                route = await get_routing_provider().route(req.lat, req.lng, *approach.target(place), req.truck_profile)
            except Exception as e:  # noqa: BLE001
                raise RouteFailed(f"{type(e).__name__}: {e}") from e
        if place.category in PARKING_CATEGORIES:
            _background(check_parking(place, route.approach))  # destinations too; entry tip relative to the approach
        others = [p for p in found if p.id != place.id][:MAX_OPTIONS]
        counter = _SESSIONS[req.session_id] if req.session_id in _SESSIONS else _turns
        n = counter.get("n", 0)
        counter["n"] = n + 1
        self._remember_alternatives(req, place, route)
        regional, weather_line, chain_line = await asyncio.gather(
            self._regional_note(place), self._route_weather(route), self._route_chains(route))
        reply = _route_reply(place, route, n, req.truck_profile.driver_name, regional)
        for line in (chain_line, weather_line):
            if line:
                reply = f"{reply} {line}"
        return AgentResponse(status="route_set", transcript=text, category=place.category, reply=reply,
                             place=place, route=route, parking=approach.best(place),
                             options=others, source=self.source)

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
    async def _route_weather(route: RouteInfo) -> str:
        """NWS alerts along a new route, attached to it and spoken once; bounded so it never delays the reply."""
        try:
            route.weather = await asyncio.wait_for(weather.alerts_along(route.geometry), WEATHER_BUDGET_S)
        except Exception as e:  # noqa: BLE001 - incl. timeout
            logger.info("route weather skipped: %s", e)
            route.weather = []
        return weather.speech(route.weather)

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
        """"How many hours do I have?" from the driver's Motive account (key comes per request, never stored)."""
        if not hos.enabled() or not req.motive_key:
            return AgentResponse(status="chat", transcript=text, reply=hos.CONNECT, source=self.source)
        with stage("tools"):
            try:
                clocks = await asyncio.wait_for(hos.available_time(req.motive_key), 8.0)
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
        place = pick_place(d, sess["places"]) or req.active_place or (sess["places"][0] if sess["places"] else None)
        if not place:
            return AgentResponse(status="chat", transcript=text, reply=WHICH_PLACE, source=self.source)
        if not place.reviews:  # the app's copy may be older than the search; refresh from the provider cache
            fresh = await get_places_provider().get(place.id, req.lat, req.lng)
            if fresh and fresh.reviews:
                place = fresh
        with stage("tools"):
            reply = await reviews.speech(place)
        return AgentResponse(status="chat", transcript=text, reply=reply, place=place, source=self.source)

    @staticmethod
    def _remember_alternatives(req: AgentRequest, place: Place, route: RouteInfo) -> None:
        """A new primary route: its alternatives serve "another way?" later; any stops of the old route are gone."""
        if req.session_id in _SESSIONS:
            sess = _SESSIONS[req.session_id]
            sess["alternatives"] = {"place": place.id, "routes": list(route.alternatives), "i": 0}
            sess.pop("via", None)

    def _stops(self, req: AgentRequest, sess: dict) -> list[Place]:
        """Stops added on the way to the active destination in this dialog (empty for any other route)."""
        via = sess.get("via")
        if not via or not req.active_place or via.get("place") != req.active_place.id:
            return []
        return list(via["stops"])

    async def _resolve_stop(self, req: AgentRequest, d: dict, sess: dict) -> Optional[Place]:
        """The place the driver wants to stop at: a named place/address, else the best place of a category."""
        query = str(d.get("query") or "").strip()
        if query:
            with stage("tools"):
                matches = await geocode(with_city(query, sess.get("city")), req.lat, req.lng)
            return matches[0] if matches else None
        category = d.get("category") if d.get("category") in CATEGORIES else "truck_stop"
        with stage("tools"):
            res = await get_places_provider().search(category, req.lat, req.lng)
        found = sorted(res.places, key=lambda p: p.distance_mi or 0)
        return choose_place(found, req.truck_profile, {req.active_place.id} if req.active_place else set())

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
            return AgentResponse(status="chat", transcript=text, reply=VIA_NO_ROUTE, source=self.source)
        stops = self._stops(req, sess)
        if len(stops) >= MAX_VIA:
            return AgentResponse(status="chat", transcript=text, reply=VIA_FULL, source=self.source)
        stop = await self._resolve_stop(req, d, sess)
        if not stop:
            what = str(d.get("query") or CATEGORY_WORD.get(str(d.get("category")), "a stop")).strip()
            return AgentResponse(status="chat", transcript=text, reply=heard_not_found(what), source=self.source)
        stops = stops + [stop]
        route = await self._route_with_stops(req, stops)
        sess["via"] = {"place": place.id, "stops": stops}
        reply = VIA_ADDED.format(name=stop.name.split(",")[0], time=route.summary.split(" · ")[0],
                                 mi=_miles(route.distance_mi))
        return AgentResponse(status="route_set", transcript=text, category=place.category, reply=reply, place=place,
                             route=route, parking=approach.best(place), options=stops, source=self.source)

    async def _clear_stops(self, req: AgentRequest, sess: dict, text: str) -> AgentResponse:
        place = req.active_place
        if not place or not self._stops(req, sess):
            return AgentResponse(status="chat", transcript=text, reply=VIA_NONE, source=self.source)
        sess.pop("via", None)
        route = await self._route_with_stops(req, [])
        reply = VIA_CLEARED.format(time=route.summary.split(" · ")[0], mi=_miles(route.distance_mi))
        return AgentResponse(status="route_set", transcript=text, category=place.category, reply=reply, place=place,
                             route=route, parking=approach.best(place), source=self.source)

    async def _alternative(self, req: AgentRequest, sess: dict, text: str) -> AgentResponse:
        """Next unused alternative route to the active destination; fetched fresh when the dialog has none."""
        place = req.active_place
        if not place:
            return AgentResponse(status="chat", transcript=text, reply=ALT_NO_ROUTE, source=self.source)
        cached = sess.get("alternatives")
        if not cached or cached.get("place") != place.id:
            with stage("tools"):
                try:
                    fresh = await get_routing_provider().route(req.lat, req.lng, *approach.target(place),
                                                               req.truck_profile, alternatives=ALTERNATIVES_FRESH)
                except Exception as e:  # noqa: BLE001
                    raise RouteFailed(f"{type(e).__name__}: {e}") from e
            cached = sess["alternatives"] = {"place": place.id, "routes": list(fresh.alternatives), "i": 0}
        i = cached["i"]
        if i >= len(cached["routes"]):
            return AgentResponse(status="chat", transcript=text, reply=NO_ALT, source=self.source)
        route = cached["routes"][i]
        cached["i"] = i + 1
        route.alternatives = []
        return AgentResponse(status="route_set", transcript=text, category=place.category, reply=_alt_reply(route),
                             place=place, route=route, parking=approach.best(place), source=self.source)

    def _parking(self, req: AgentRequest, d: dict, sess: dict, text: str) -> AgentResponse:
        """Cached verdict -> answer now; otherwise "Let me look" and the app fetches /parking/answer."""
        place = pick_place(d, sess["places"]) or req.active_place or (sess["places"][0] if sess["places"] else None)
        if not place:
            return AgentResponse(status="chat", transcript=text, reply="Which place? Find one or set a route first.",
                                 source=self.source)
        if is_town(place):
            return AgentResponse(status="chat", transcript=text, source=self.source,
                                 reply="That's a whole town, so there's no parking check. Ask me for a truck stop "
                                       "there or give me an address and I'll check it.")
        pk = approach.best(place)
        if pk and (pk.relative or not approach.has_approach(place) or not pk.entry_tip):
            return AgentResponse(status="chat", transcript=text, reply=approach.speech(pk), source=self.source)
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
        chosen = choose_place(found, req.truck_profile, exclude)
        from_point = chosen.distance_mi or 0.0
        # The app's distances are from the driver: recompute them for the list on screen.
        for p in found:
            km = haversine_km(req.lat, req.lng, p.lat, p.lng)
            p.distance_km, p.distance_mi = round(km, 2), round(km_to_mi(km), 1)
        res = await self._route_to(req, chosen, found, text)
        where = "your delivery" if re.search(r"\b(?:deliver|drop|consignee|receiver)", text, re.I) else (
            "the shipper" if re.search(r"\bshipper\b", text, re.I) else query.split(",")[0])
        away = "right at" if from_point < 0.3 else f"{_miles(from_point)} miles from"
        res.reply = f"{chosen.name}, {away} {where}, {_miles(res.route.distance_mi)} miles from you."
        if swapped:
            res.reply = f"No rest area near {query.split(',')[0]}, so here's a truck stop. {res.reply}"
        return res

    async def _find(self, req: AgentRequest, category: str, text: str, exclude: set[str]) -> AgentResponse:
        if category not in enabled_categories():
            return AgentResponse(status="chat", transcript=text, category=category, source=self.source,
                                 reply=CATEGORY_OFF.format(what=CATEGORY_WORD.get(category, "that")))
        with stage("tools"):
            res = await get_places_provider().search(category, req.lat, req.lng)
        found = res.places[:MAX_OPTIONS + 2]
        if not found:
            msg = res.message or NO_PLACES
            return AgentResponse(status="no_results", transcript=text, category=category,
                                 reply=f"{msg.rstrip('.')}.", message=msg, source=self.source)
        if category in PARKING_CATEGORIES:
            for p in found[:PRECHECK]:
                _background(check_parking(p))  # warm satellite checks; never wait for them here
        if all(p.id in exclude for p in found):
            # Everything was turned down: offer them again (the driver can now pick or search anew).
            _SESSIONS.get(req.session_id or "", {}).pop("rejected", None)
            return AgentResponse(status="chat", transcript=text, category=category, reply=ALL_REJECTED,
                                 source=self.source)
        chosen = choose_place(found, req.truck_profile, exclude)
        return await self._route_to(req, chosen, found, text)

    async def _handle(self, req: AgentRequest) -> AgentResponse:
        text = (req.text or "").strip()
        if not re.search(r"[a-zA-Z]{2,}", text):
            return _not_understood(text, self.source)
        sess = _session(req.session_id)
        bd = await breakdown.handle(req, sess, text, self.source)
        if bd:
            return bd
        try:
            d = await asyncio.wait_for(classify(text, self._context(req, sess), req.truck_profile),
                                       timeout=CLASSIFY_TIMEOUT_S)
        except Exception as e:  # noqa: BLE001 - incl. timeout
            logger.warning("agent LLM failed: %s: %s", type(e).__name__, e)
            return _error(text, LLM_DOWN, self.source)

        intent = d.get("intent")
        logger.info("agent intent=%s category=%s query=%r place_index=%s", intent, d.get("category"),
                    d.get("query"), d.get("place_index"))
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
        # Current pick: the active route, or the last place chosen in this dialog.
        last = req.active_place or (sess["places"][0] if sess["places"] else None)
        current = last.id if last else None
        exclude = {current} if current else set()
        if current and (intent == "pick" or (intent == "find" and last and d.get("category") == last.category)):
            # "not that one" / "something else": never offer an already rejected place again in this dialog.
            rejected = sess.setdefault("rejected", set())
            rejected.add(current)
            exclude = set(rejected)
        try:
            if intent == "breakdown":
                return await breakdown.from_intent(d, req, sess, text, self.source)
            if intent == "alternative":
                return await self._alternative(req, sess, text)
            if intent == "add_stop":
                return await self._add_stop(req, d, sess, text)
            if intent == "clear_stops":
                return await self._clear_stops(req, sess, text)
            if intent == "reviews":
                return await self._reviews(req, d, sess, text)
            if intent == "weather":
                return await self._weather(req, d, sess, text)
            if intent == "hos":
                return await self._hos(req, text)
            if intent == "pick":
                place = pick_place(d, sess["places"])
                if place and place.id not in exclude:
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
            if intent == "go" and str(d.get("query") or "").strip():
                query = with_city(str(d["query"]).strip(), sess.get("city"))
                with stage("tools"):
                    matches = await geocode(query, req.lat, req.lng)
                logger.info("agent go query=%r (llm %r) city_ctx=%r -> %s", query, d.get("query"), sess.get("city"),
                            matches[0].name if matches else "no match")
                if not matches:
                    return AgentResponse(status="chat", transcript=text, reply=heard_not_found(query),
                                         source=self.source)
                sess["city"] = city_of(query, matches[0]) or sess.get("city")
                return await self._route_to(req, matches[0], [], text)
        except RouteFailed as e:
            logger.warning("agent routing failed: %s", e)
            return _error(text, ROUTE_DOWN, self.source)
        except (PlacesUnavailable, httpx.HTTPError) as e:
            logger.warning("agent places/geocode failed: %s: %s", type(e).__name__, e)
            return _error(text, PLACES_DOWN, self.source)
        except Exception as e:  # noqa: BLE001
            logger.exception("agent action failed: %s: %s", type(e).__name__, e)
            return _error(text, SOMETHING_BROKE, self.source)
        if intent == "parking":
            if str(d.get("near") or "").strip():  # "where can I park close to my delivery in X": a search
                try:
                    return await self._find_near(req, "truck_stop", str(d["near"]).strip(), text, exclude, sess)
                except RouteFailed as e:
                    logger.warning("agent routing failed: %s", e)
                    return _error(text, ROUTE_DOWN, self.source)
                except (PlacesUnavailable, httpx.HTTPError) as e:
                    logger.warning("agent places/geocode failed: %s: %s", type(e).__name__, e)
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
            return AgentResponse(status="chat", transcript=text, reply=reply, end_conversation=intent == "bye",
                                 source=self.source)
        return _not_understood(text, self.source)


_provider = LlmAgentProvider()


def get_agent_provider() -> LlmAgentProvider:
    return _provider
