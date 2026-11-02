"""Copilot agent: one structured LLM classification per turn, then the backend acts.

intent=find -> Places search + rule-based pick; go -> geocode + truck route; pick -> a place
found earlier in this dialog; chat/bye -> the model's short reply from the same call.
Route answers are templated (one sentence) and parking directions follow from the app.
"""
import asyncio
import json
import logging
import math
import os
import re
from collections import OrderedDict
from typing import Optional

import httpx

from models import MAX_VIA, AgentRequest, AgentResponse, AppAction, Place, RouteInfo, TruckProfile
from services import (approach, breakdown, chains, controls, fuel_index, hos, llm, memory, profile_learn, reviews,
                      timing, tpims, weather)
from services.geo import haversine_km, km_to_mi
from services.geocode import _STATES, chain_query, geocode, is_street, town_query
from services.google_places import TRUCK_STOP_NAME, PlacesUnavailable
from services.parking import cached_parking, check_parking, is_town, listed_in_data
from services.places import get_places_provider
from services.errors import NoTruckRoute
from services.routing import VIA_STOP_S, get_routing_provider
from services.flags import on
from services.speech_text import fit_reply
from services.timing import stage

logger = logging.getLogger("truckbuddy.agent")

NOT_UNDERSTOOD = "Sorry, I didn't catch that. Try again."
ROUTE_DOWN = "I couldn't build a truck route there right now. Try again in a minute."
NO_TRUCK_ROUTE = ("I can't find a truck route there from here. Try another destination, or an address on a main "
                  "road nearby.")
PLACES_DOWN = "Place search is down right now. Give me an address instead."
LLM_DOWN = "Give me a second, I lost my train of thought. Say that again."
SOMETHING_BROKE = "Something went wrong on my end. Try again in a minute."
CLASSIFY_TIMEOUT_S = 20


class RouteFailed(Exception):
    pass


def _error(text: str, reply: str, source: str) -> "AgentResponse":
    """Infrastructure trouble: a distinct spoken line + a toast, never "didn't catch that"."""
    return AgentResponse(status="error", transcript=text, reply=reply, message=reply, source=source)


def _route_failed(text: str, e: "RouteFailed", source: str) -> "AgentResponse":
    """Routing is down -> "try again in a minute"; the router found no truck route -> say so, retrying won't help."""
    logger.warning("agent routing failed: %s", e)
    no_route = isinstance(e.__cause__, NoTruckRoute)
    timing.note("error", "no_truck_route" if no_route else "route_down")
    return _error(text, NO_TRUCK_ROUTE if no_route else ROUTE_DOWN, source)


def _nearest_index(geometry: list, lat: float, lng: float) -> int:
    """Vertex of the route closest to a point (planar, good enough to order stops along the way)."""
    kx = max(0.2, abs(math.cos(math.radians(lat))))
    best, at = float("inf"), 0
    for i, p in enumerate(geometry):
        d = (p[0] - lat) ** 2 + ((p[1] - lng) * kx) ** 2
        if d < best:
            best, at = d, i
    return at


def _profile_key(t: TruckProfile) -> tuple:
    """What changes the truck route: alternatives planned for another truck are not offered."""
    return (t.height_in, t.trailer_ft, t.weight_lb, t.hazmat, t.avoid_tolls, t.avoid_ferries_tunnels)


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
PARK_WORDS = re.compile(r"\b(park|parking|overnight|shut ?down|lay ?over|sleep in (?:the|my) (?:truck|cab|sleeper))\b", re.I)
BED_WORDS = re.compile(r"\b(hotel|motel|room|bed|shower|inn|lodge)\b", re.I)
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
ALT_ORIGIN_KM = 8.0  # cached alternatives were planned from here; further on, "another way" plans from the truck
PASSED_KM = 1.6  # a stop this close to the truck is being approached, never counted as passed
REACHED_KM = 0.15  # ...unless the truck is at it (parked in its lot): then it is made, and the next plan leaves it out
WEATHER_BUDGET_S = 2.0  # NWS lookups on a new route may add at most this much to the reply
DETOUR_BUDGET_S = 3.5  # the alternatives' weather, measured from the start; past it the detour line is skipped
PRECHECK = 1  # nearest candidates satellite-checked in the background (each = one vision call)
FAVORITE_EXTRA_MI = 5.0
TRUCK_TAGS = ("truck parking", "53 ft", "truck lot", "hgv")


def is_fuel_stop(p: Place) -> bool:
    """A truck_stop result with no truck-stop name and a satellite "unlikely": just a gas station."""
    return p.category == "truck_stop" and not TRUCK_STOP_NAME.search(p.name) and _verdict(p) == "unlikely"
CATEGORIES = CORE_CATEGORIES + EXTRA_CATEGORIES  # all the classifier may name; enabled_categories() gates

SYSTEM = """You are TruckBuddy, a hands-free voice copilot for a long-haul truck driver who is driving.
Each message starts with the truck (height, trailer, weight, hazmat) and the driver's preferences; use them.

The driver's words come from speech recognition in a noisy cab, so they may be misheard. Read them by what
makes sense for a trucker and act on the corrected meaning, e.g. "I need a few" = "I need fuel",
"the stars are out" / "start the ride" = "start the route", "rest a ride" = "rest area", "diesel" heard as
"these all". Only fix words that clearly don't fit; never invent a destination. If you can't tell, use "unclear".

Classify the driver's LAST utterance and answer with JSON only, no other text:
{{"intent": "find"|"go"|"pick"|"alternative"|"add_stop"|"clear_stops"|"reviews"|"weather"|"hos"|"memory"|"parking"|"control"|"chat"|"bye"|"unclear", "category": "truck_stop"|"hotel"|"rest_area"|"walmart"|"cat_scale"|"truck_wash"|"weigh_station"|null, "near": string|null, "query": string|null, "when": string|null, "place_index": integer|null, "actions": array|null, "reply": string|null}}

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
- pick: the driver refers to a place found earlier in this conversation ("the second one", "the Pilot", "not that one", "something else", "another one", "something closer", "the cheapest diesel": lowest "diesel $" in the list). place_index = the number of the best matching place in "Places found earlier" (1, 2, 3...), never the current route. These are about PLACES even while a route is active and alternative routes exist: "not that one" / "something else" / "the second one" are never alternative and never go ("nothing else" / "that's all" / "I'm good" are bye, not pick). If no earlier place fits, use find with the same category instead.
- alternative: a different ROAD to the SAME destination, only when the driver talks about the road, way or route itself ("another way", "take the other way", "different route", "is there a faster way", "can we skip the grade", "go around that", "go around the storm"). Never a new place; a rejected place ("not that one") is pick. Only meaningful while a route is active; otherwise answer as chat.
- add_stop: a stop ON THE WAY to the current destination, keeping that destination ("stop at the Pilot in Fernley on the way", "swing by Love's first", "add a fuel stop", "cheapest diesel on my route", "fuel on the way", "I need a rest area before that"). query = the place or address named (digits for numbers), else category = the kind of stop (truck_stop for fuel/food/showers, rest_area, hotel); never both null. Only while a route is active; with no route it's a plain find or go.
- clear_stops: drop the stops added on the way ("skip the stops", "forget the stop", "no stops, straight there").
- reviews: what other drivers say about a place ("what do drivers say about it", "is it any good", "reviews for the Pilot", "how are the showers there"). place_index = the place asked about from "Places found earlier", or null for the current route / the last one mentioned.
- memory: what the copilot remembers about the driver ("what do you know about me", "what have you learned about me") -> query = "show"; or erasing it ("forget that", "forget what you know about me", "stop remembering") -> query = "forget".
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


FIT_STATUSES = ("chat", "no_results", "not_understood")  # free text; route/options/confirm lines stay whole
FAST_SENTENCES = 1
SLOW_SENTENCES = 3


def fast_mps() -> float:
    try:
        return float(os.environ.get("REPLY_FAST_MPS", "25"))  # 25 m/s = 56 mph
    except ValueError:
        return 25.0


def at_speed(req: AgentRequest) -> bool:
    return req.speed_mps is not None and req.speed_mps >= fast_mps()


def _fit_for_speed(req: AgentRequest, res: AgentResponse) -> None:
    """At highway speed a free-text reply is cut to one sentence; parked, up to three (REPLY_FIT=0 turns it off)."""
    if not on("REPLY_FIT") or req.speed_mps is None or res.status not in FIT_STATUSES:
        return
    res.reply = fit_reply(res.reply, FAST_SENTENCES if at_speed(req) else SLOW_SENTENCES)


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
MEMORY_FIRST_TURN_S = 0.3  # how long the first turn of a dialog waits for long-term memory
MEMORY_LOAD_S = 3.0  # after this the memory read is abandoned for good
FACTS_EVERY_TURNS = 5  # how often a dialog checks whether the learned facts are due for a refresh
REJECTIONS_TO_LEARN = 3  # "not that one" on the same chain this many times in a dialog -> avoided


def stt_confirm_below() -> float:
    """Transcripts below this confidence (exp(mean logprob)) get a confirmation question. Env STT_CONFIRM_BELOW."""
    try:
        return float(os.environ.get("STT_CONFIRM_BELOW", "0.6"))
    except ValueError:
        return 0.6


def _facts(req: AgentRequest) -> Optional[dict]:
    sess = _SESSIONS.get(req.session_id or "")
    return sess.get("facts") if sess else None


def _background_task(t: asyncio.Task) -> None:
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


def _background(coro) -> None:
    _background_task(asyncio.create_task(coro))


def _adopt_past(sess: dict) -> None:
    """A memory read that outlived the first turn lands in the session as soon as it is done."""
    task = sess.get("past_task")
    if task is not None and task.done():
        sess.pop("past_task", None)
        if not task.cancelled() and task.exception() is None:
            sess.update(task.result())


def _street(address: Optional[str]) -> str:
    """Landmark for speech: the street/exit without the house number, or ''."""
    if not address:
        return ""
    first = address.split(",")[0].strip()
    first = re.sub(r"^\d+[\w-]*\s+", "", first)  # drop the house number
    return first if first and not first[0].isdigit() else ""


def _speak_street(s: str) -> str:
    """'N Pima Rd' -> 'North Pima Road' (whole words, case-insensitive); a '#123' suite is dropped."""
    s = re.sub(r"\s*#\S+", "", s)
    return " ".join(STREET_WORDS.get(w.lower().rstrip("."), w) for w in s.split())


def _label_parts(place: Place) -> tuple[list[str], Optional[str]]:
    """The comma parts of the place's name and address without ZIP and country, and the two-letter state when one
    of the parts carries it ('Payson, Phoenix, AZ 85086, United States' -> (['Payson', 'Phoenix'], 'AZ'))."""
    parts, state = [], None
    for raw in f"{place.name}, {place.address or ''}".split(","):
        p = raw.strip()
        if not p:
            continue
        m = re.match(r"^([A-Za-z]{2})(?:\s+\d{5}(?:-\d{4})?)?$", p)  # "AZ 85086", "Az"
        if m and m.group(1).lower() in _STATES:
            state = state or m.group(1).upper()
            continue
        if _ZIP_COUNTRY.match(p):
            continue
        if p not in parts:
            parts.append(p)
    return parts, state


def spoken_where(place: Place) -> str:
    """Where the place really is, for speech. A town: 'Payson, Arizona'. A street or an address: 'Payson in
    Phoenix, Arizona', '2828 El Centro Road in Sacramento, California'. A business: 'Cave Creek Az on North Pima
    Road in Scottsdale, Arizona'. A name with nothing after it stays as it is ('Reno')."""
    parts, state = _label_parts(place)
    state_name = _STATES.get((state or "").lower(), "").title() if state else ""
    if is_town(place):
        head = parts[0] if parts else place.name.split(",")[0]
        return f"{head}, {state_name}" if state_name else head
    if not parts:
        return place.name.split(",")[0]
    head, rest = re.sub(r"\s*#\S+", "", parts[0]).strip(), parts[1:]  # "Pilot Travel Center #123" -> no number
    if not rest:
        return head
    town = rest[-1]
    street = _speak_street(_street(rest[0]) or rest[0]) if len(rest) > 1 else ""  # no house number on the street
    # a street-type title ("Payson", "2828 El Centro Rd") reads with its words spelled out too
    if is_street(place) or head[:1].isdigit():
        head = _speak_street(head)
    where = f"{head} on {street} in {town}" if street else f"{head} in {town}"
    return f"{where}, {state_name}" if state_name else where


def _tokens(s: str) -> list[str]:
    words = re.findall(r"[a-z0-9']+", s.lower())
    return [w for w in words if w not in ("the", "a", "an")]


def _names_match(query: str, place: Place) -> bool:
    """The letters agree: every word of what was said (before the first comma) is in the place's name, or the
    place's own first part is wholly in what was said. 'Payson' ~ 'Payson, AZ' yes; 'Payson, AZ' ~ 'Cave Creek, Az' no."""
    said = _tokens(query.split(",")[0])
    name = _tokens(place.name)
    first = _tokens(place.name.split(",")[0])
    if not said or not name:
        return False
    return all(w in name for w in said) or (bool(first) and all(w in said for w in first))


def dest_check(query: str, place: Place) -> Optional[str]:
    """Why this geocode should be read back before routing, or None when it is plainly what was said."""
    far = (place.distance_mi or 0) > DEST_FAR_MI
    same = _names_match(query, place)
    if chain_query(query) and is_town(place):
        return "chain_to_town"
    if town_query(query) and not is_town(place) and (is_street(place) or far or not same):
        return "town_to_other"
    if far and not same:
        return "far_other_name"
    return None


def arrival_street(place: Place, route: RouteInfo) -> str:
    """The street the driver turns off to get in: the last maneuver's street when the route knows it (the
    same one the entry tip on the card names), else the street of the address."""
    ap = route.approach
    if ap and ap.street:
        return re.sub(r"\s*\([^)]*\)", "", ap.street).strip() or _street(place.address)  # "(US-395-BR)" is for the card
    return _street(place.address)


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
TILL_SAID = ", in effect till "  # the warning line already names the end: the detour line must not repeat it
CHAIN_QUESTION = re.compile(r"\bchain[ -]?(?:control|law|requirement|restriction)s?\b|\bchains?\s+(?:ahead|required|needed|on the (?:pass|route|way)|up ahead)\b|\b(?:any|are there|is there|do I need)\b.*\bchains?\b", re.I)
CHAINS_NO_ROUTE = "Set a route and I'll check Caltrans chain controls along it."
CHAINS_OUTSIDE = "I only have chain controls for California, and this route doesn't go through it."
CHAINS_NONE = "No chain controls on your route right now."
ALT_NO_ROUTE = "Set a route first and I'll look for other ways."
VIA_ADDED = "Added {name}{ahead}. New total {time}, {mi} miles{dwell}."
VIA_DWELL = ", counting {minutes} minutes at {each}"  # HERE adds VIA_STOP_S per stop; the total looked wrong without it (H9)
VIA_NO_ROUTE = "Set a route first, then I can add stops on the way."
VIA_FULL = "I can only add three stops. Say 'clear the stops' to start over."
VIA_DUPLICATE = "{name} is already one of your stops."
VIA_IS_DESTINATION = "That's where we're headed, {name}."
VIA_CLEARED = "Stops cleared. Straight there, {time}, {mi} miles."
VIA_NONE = "There are no stops on this route."
# A stop that is nowhere near the way (the geocoder put "the Pilot in Fernley" in Wells, 300 miles past Reno,
# and the dialog added it without a word, 2026-11-01): ask before the route changes.
VIA_FAR = "{name} is about {extra} out of the way; from here that's {new} instead of {old}. Add it anyway?"
VIA_FAR_NO = "Okay, no stop added."
# A geocode that may not be what the driver meant ("Payson, AZ" -> a street in Phoenix or a business in Cave Creek,
# "Pilot" -> the hamlet of Pilot, NV; stand 2026-11-01): the reply names where the place really is, and when the
# match is far or named differently the copilot asks before the route is built.
DEST_FAR_MI = 50.0
DEST_CHECK = "I found {where}, about {mi} miles away. Is that the one?"
DEST_NO = "Okay, not that one. Say the town and state, or type the address."
DEST_ONLY = "That's the only match I have for {said}. Try the town with its state, or type the address."
DEST_YES = re.compile(r"^\s*(?:yes|yeah|yep|yup|sure|ok(?:ay)?|correct|right|that's (?:it|right|the one)|that one|"
                      r"go ahead|do it|take me there|fine)\b", re.I)
STREET_WORDS = {"st": "Street", "ave": "Avenue", "rd": "Road", "blvd": "Boulevard", "dr": "Drive", "hwy": "Highway",
                "fwy": "Freeway", "pkwy": "Parkway", "ln": "Lane", "ct": "Court", "pl": "Place", "trl": "Trail",
                "cir": "Circle", "n": "North", "s": "South", "e": "East", "w": "West", "ne": "Northeast",
                "nw": "Northwest", "se": "Southeast", "sw": "Southwest"}
VIA_FAR_YES = re.compile(r"^\s*(?:yes|yeah|yep|yup|sure|ok(?:ay)?|add it|go ahead|do it|please do|fine)\b", re.I)
STOP_DETOUR_MIN_S = 45 * 60  # a stop costing more than this beyond its own 30 minutes...
STOP_DETOUR_FRACTION = 0.5  # ...and more than half the trip so far is asked about first
WHICH_PLACE = "Which place? Find one or set a route first."
# Low-confidence transcripts of a destination or a place name get one "Did you say ...?" before anything is acted on.
CONFIRM = "Did you say {what}?"
CONFIRM_NO = "Okay, say it again."
YES = re.compile(r"^\s*(?:yes|yeah|yep|yup|correct|right|that's right|that's it|sure|affirmative)\b", re.I)
NO = re.compile(r"^\s*(?:no|nope|nah|wrong|that's wrong|not that|negative)\b", re.I)


def _minutes_said(m: int) -> str:
    h, mm = divmod(int(m), 60)
    return f"{h} h {mm} min" if h and mm else (f"{h} h" if h else f"{mm} min")


def _alt_reply(route: RouteInfo, current: Optional[RouteInfo] = None) -> str:
    """"Here's another way via I-80 and CA-89, 4 h 33 min, 204 miles, about 1 h 42 min longer.": the difference
    to the route the driver is on is what decides, so it is said when it is five minutes or more."""
    via = f" via {' and '.join(route.labels[:2])}" if route.labels else ""
    tolls = f", about ${route.tolls_usd:,.0f} in tolls" if route.tolls_usd else ""
    diff = ""
    if current is not None:
        d = route.duration_min - current.duration_min
        if abs(d) >= 5:
            diff = f", about {_minutes_said(abs(d))} {'longer' if d > 0 else 'faster'}"
        # The weather is why most drivers ask: say when the other way misses it, or runs into it. Only
        # a completed lookup counts as clear; an unknown road gets no claim of being clear. Weather the new
        # road is known to have is said whatever we know about the old one (QA review 2026-11-01).
        on_current = {a.id for a in current.weather}
        new_weather = [a for a in route.weather if a.id not in on_current]
        if current.weather and route.weather_known and not route.weather:
            diff += f", and it misses the {current.weather[0].event}"
        elif new_weather:
            diff += f", but it runs into a {new_weather[0].event}"
    return ALT_REPLY.format(via=via, time=route.summary.split(" · ")[0], mi=_miles(route.distance_mi), tolls=tolls + diff)
_turns: dict = {"n": 0}  # rotation when there's no dialog session


def _notice(raw: str) -> str:
    low = raw.lower()
    return next((say for key, say in NOTICES if key in low), "Heads up, check this route for restrictions.")


RESTRICTED_SPEECH = ("Heads up, I couldn't find a legal truck route to there. {line} Don't take this one blind: "
                     "check the signs, or give me another destination.")
LEGAL_SPEECH = "Heads up: {line}"


def _route_warning(route: RouteInfo) -> str:
    """One spoken heads-up for a new route. A route that breaks the truck's restrictions (the router found
    no compliant one) comes first and in full, with the limit and the truck's own number; then a closure;
    then a router notice; nothing when there's none of those."""
    closed = next((i for i in route.incidents if i.closed), None)
    closure = CLOSURE.format(description=closed.description) if closed else ""
    if route.restricted and route.notices:
        line = route.notices[0]
        said = (LEGAL_SPEECH if route.restricted == "legal" else RESTRICTED_SPEECH).format(line=line)
        return f"{said} {closure}" if closure else said  # a closure on top of a restriction is still said
    if closure:
        return closure
    return _notice(route.notices[0]) if route.notices else ""


def _diesel_note(p: Place) -> str:
    """Price tag for the model's place list, so "the cheapest diesel" resolves to a place_index."""
    if p.diesel_price is None:
        return ""
    return f", diesel ${p.diesel_price:.2f}" + ("" if p.diesel_kind == "truck" else " (car pumps)")


PRICE_WORDS = re.compile(r"\b(cheap(?:est|er)?|price|prices|cost|how much)\b", re.I)
NO_PRICE = " I don't have a diesel price for it."
NO_PRICES_KEEP = "I don't have diesel prices for any of these, so I can't say which is cheapest. {name} stays as your stop."
# A fuel/rest stop asked for "on my route" while a route is active is a stop on the way, never a new destination.
ON_THE_WAY = re.compile(r"\b(?:on|along) (?:my|the|our) (?:route|way)\b|\ben route\b|\bon the way\b"
                        r"|\bbefore (?:I|we) (?:get|arrive)\b", re.I)
STOP_CATEGORIES = {"truck_stop", "rest_area", "cat_scale", "truck_wash", "weigh_station", "walmart"}


def price_caveat(text: str, place: Optional[Place]) -> str:
    """The driver asked about the price and the place has none: say so instead of going quiet about it."""
    if not place or place.category != "truck_stop" or place.diesel_price is not None:
        return ""
    return NO_PRICE if PRICE_WORDS.search(text or "") else ""


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
    out += body.format(**vals) + _diesel_sentence(place, regional) + tpims.sentence(place)
    warning = _route_warning(route)
    return f"{out} {warning}" if warning else out


# "Not that one" and friends: the driver turns a PLACE down. With a route active the classifier sometimes
# reads it as "another road" (it sees "N alternative routes not yet offered"); the words decide instead.
REJECT_PLACE = re.compile(
    r"\b(?:not (?:that|this) one|something else|somewhere else|another one|a different one|different one|"
    r"(?:the )?next one|not there|no,? not that)\b", re.I)
ROAD_WORDS = re.compile(r"\b(?:way|road|route|highway|freeway|interstate|detour|grade|pass|toll)s?\b", re.I)
# "The second one", "number two", "the last one": a reference into the list, never something to geocode
# (HERE once turned "the second one" into W 21st Pl, The Dalles, OR, 598 miles out).
LIST_REF = re.compile(
    r"^(?:the |that |this |number |no\.? ?|#)?"
    r"(?P<ord>first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th|one|two|three|four|five|1|2|3|4|5|last|closest|nearest)"
    r"(?: one| place| stop| option)?\.?$", re.I)
ORDINALS = {"first": 1, "1st": 1, "one": 1, "1": 1, "second": 2, "2nd": 2, "two": 2, "2": 2, "third": 3, "3rd": 3,
            "three": 3, "3": 3, "fourth": 4, "4th": 4, "four": 4, "4": 4, "fifth": 5, "5th": 5, "five": 5, "5": 5}
# A bare pronoun or deictic the model turned into a destination: nothing to geocode either.
NOT_A_PLACE = re.compile(r"^(?:that|this|it|there|here|that one|this one|the other one|other one)\.?$", re.I)
# "Nothing else" is the end of the conversation; next to "something else" the model can read it as a pick.
BYE_PHRASE = re.compile(r"^(?:no,? )?(?:nothing else|that'?s (?:all|it)|i'?m (?:good|all set)|all set|no thanks?)[.!]?$", re.I)


def list_reference(query: str, places: list[Place], current_id: Optional[str]) -> Optional[int]:
    """1-based index into `places` for "the second one" & co, counted over the places the driver can see
    (the current route excluded, as on the options list); None when the query is not a reference."""
    m = LIST_REF.match((query or "").strip())
    if not m:
        return None
    shown = [p for p in places if p.id != current_id] or list(places)
    if not shown:
        return None
    word = m.group("ord").lower()
    if word == "last":
        chosen = shown[-1]
    elif word in ("closest", "nearest"):
        chosen = min(shown, key=lambda p: p.distance_mi if p.distance_mi is not None else 1e9)
    else:
        n = ORDINALS.get(word)
        if not n or n > len(shown):
            return None
        chosen = shown[n - 1]
    return places.index(chosen) + 1


_NAME_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(s: str) -> set[str]:
    """Lowercase words, apostrophes dropped, a plural "s" trimmed: the model said "Roady's Truck Stops"."""
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in _NAME_TOKEN.findall((s or "").lower().replace("'", ""))}


def named_place(query: str, places: list[Place]) -> Optional[Place]:
    """The listed place the driver named ("the 49er", "the Pilot"), or None. Every word of the query has to
    be in the place name, and at least one of them must not be a word of the address (the town): "take me
    to Sacramento" never matches "Sacramento 49er Travel Plaza", "the 49er" does. Geocoding "49er" gave a
    street in Placerville (QA 2026-10-31, H9)."""
    words = _tokens(re.sub(r"^\s*(?:the|a|an)\s+", "", query or "", flags=re.I))
    if not words:
        return None
    for p in places:
        name = _tokens(p.name)
        if words <= name and words - _tokens(p.address or ""):
            return p
    return None


NAME_LIKE = re.compile(r"^(?:the\s+)?[A-Za-z0-9&'.\- ]{2,40}$")


def name_like(query: str) -> bool:
    """"the 49er", "Roady's", "Pilot": a short name without a house number or a street word, the kind of query
    that is a truck stop's name, not an address."""
    q = (query or "").strip()
    if not NAME_LIKE.match(q) or len(q.split()) > 3:
        return False
    if re.match(r"^\d+\s", q) or re.search(r"\b(?:st|street|ave|avenue|rd|road|blvd|hwy|highway|dr|drive|way|exit|mile)\b", q, re.I):
        return False
    return True


GENERIC_NAME_WORDS = {"truck", "stop", "stops", "travel", "center", "centre", "plaza", "inn", "motel", "hotel",
                      "rest", "area", "the", "and", "of", "at", "north", "south", "east", "west", "northbound",
                      "southbound", "eastbound", "westbound", "station", "fuel", "gas", "diesel"}


def named_in_text(text: str, places: list[Place]) -> Optional[Place]:
    """The listed place whose own word the driver said ("stop at the 49er on the way" -> the 49er), or None.
    A word counts when it is the place's, not its town's and not a generic ("truck stop", "travel plaza")."""
    said = _tokens(text)
    for p in places:
        own = {w for w in _tokens(p.name) - _tokens(p.address or "") - GENERIC_NAME_WORDS if len(w) >= 3}
        if own & said:
            return p
    return None


def reject_or_reference_guard(intent: str, d: dict, text: str, places: list[Place],
                              active: Optional[Place]) -> tuple[str, dict]:
    """Rewrites two classifier mistakes before anything acts on them:
    - alternative + a rejection phrase (and no road word) -> pick without an index (= the next place);
    - go + a list reference ("the second one", place_index given) -> pick with that index;
    - go + a bare pronoun -> not_understood instead of a geocode of "that".
    Everything else passes through untouched."""
    current_id = active.id if active else None
    if intent == "pick" and BYE_PHRASE.match(text.strip()):
        logger.info("agent pick %r is a goodbye -> bye", text)
        return "bye", {**d, "intent": "bye", "place_index": None}
    if intent == "alternative" and places and REJECT_PLACE.search(text) and not ROAD_WORDS.search(text):
        logger.info("agent alternative %r reads as a rejected place -> pick", text)
        return "pick", {**d, "intent": "pick", "place_index": None}
    if intent == "go":
        query = str(d.get("query") or "").strip()
        ref = list_reference(query, places, current_id) if places else None
        if ref is None and places and LIST_REF.match(text.strip()):
            ref = list_reference(text.strip(), places, current_id)
        if ref is not None:
            logger.info("agent go %r is a list reference -> pick %s", query or text, ref)
            return "pick", {**d, "intent": "pick", "place_index": ref}
        idx = d.get("place_index")
        if places and isinstance(idx, int) and 1 <= idx <= len(places) and (not query or LIST_REF.match(query) or NOT_A_PLACE.match(query)):
            logger.info("agent go with place_index=%s and no real destination -> pick", idx)
            return "pick", {**d, "intent": "pick", "place_index": idx}
        if LIST_REF.match(query) or NOT_A_PLACE.match(query):
            logger.info("agent go %r is not a place; nothing to pick from", query)
            return ("pick", {**d, "intent": "pick", "place_index": None}) if places else ("not_understood", d)
    return intent, d


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


def _chain_of(name: str) -> Optional[str]:
    """The truck stop chain in a place name, spelled the usual way ("Love's", "Pilot", "TA"), or None."""
    m = TRUCK_STOP_NAME.search(name or "")
    if not m:
        return None
    raw = m.group(0).lower()
    return CHAIN_SPELLING.get(raw, raw.title())


CHAIN_SPELLING = {"love's": "Love's", "loves": "Love's", "ta": "TA", "flying j": "Flying J", "sapp bros": "Sapp Bros",
                  "roady's": "Roady's", "roadys": "Roady's"}


def choose_place(found: list[Place], truck: TruckProfile, exclude: set[str],
                 facts: Optional[dict] = None) -> Optional[Place]:
    """Favorite chain (from the profile or learned) within +5 mi of the nearest, then truck parking
    listed/likely, then the closest. Chains the driver has rejected before come last; "unlikely" parking
    only if nothing else is left."""
    pool = [p for p in found if p.id not in exclude] or found
    if not pool:
        return None
    avoided = [c.lower() for c in (facts or {}).get("chains_avoided") or []]
    if avoided:
        kept = [p for p in pool if (_chain_of(p.name) or "").lower() not in avoided]
        pool = kept or pool
    ok = [p for p in pool if not is_fuel_stop(p) and _verdict(p) != "unlikely"] or \
        [p for p in pool if not is_fuel_stop(p)] or pool
    dist = lambda p: p.distance_mi or 0.0  # noqa: E731
    nearest = min(dist(p) for p in ok)
    liked = list(truck.favorite_chains) + list((facts or {}).get("chains_liked") or [])
    chains = [re.compile(rf"\b{re.escape(c)}\b", re.I) for c in liked if c.strip()]
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


# The system prompt is byte-identical for every driver and every turn, so OpenAI's prompt cache serves it
# (0.1x input price on the cached prefix, faster first token). Everything that varies, the truck, the
# preferences and the conversation context, travels in the user message, in that order.
SYSTEM_STATIC = SYSTEM.format(controls=controls.PROMPT, parking=approach.PROMPT, breakdown=breakdown.PROMPT)
PROMPT_VERSION = 5  # bump when SYSTEM changes, so the cache key and the eval baseline move together
PROMPT_CACHE_KEY = f"agent-v{PROMPT_VERSION}"


def prompt_messages(text: str, context: str, truck: TruckProfile) -> tuple[str, str]:
    """(system, user) for the classifier."""
    user = f"Truck: {truck.label}. Driver preferences: {truck.prefs_label}.\n[{context}]\nDriver said: {text}"
    return SYSTEM_STATIC, user


async def classify(text: str, context: str, truck: TruckProfile) -> dict:
    system, user = prompt_messages(text, context, truck)
    with stage("llm_round_1"):
        res = await llm.complete_full(system, user, MODEL[1], cache_key=PROMPT_CACHE_KEY)
    return _parse(res.text)


class LlmAgentProvider:
    source = MODEL[1]

    async def handle(self, req: AgentRequest, driver: Optional[str] = None) -> AgentResponse:
        if driver and not req.session_id:
            req.session_id = f"{driver}:solo"[:64]  # typed turns outside a dialog still use memory
        if driver and req.session_id and req.session_id not in _SESSIONS:
            # A new dialog starts with what the driver told the copilot in earlier ones. The read runs as a
            # task: a warm database answers well within MEMORY_FIRST_TURN_S, a cold or slow one no longer
            # holds the first reply, the result is picked up by whichever turn comes next.
            sess = _session(req.session_id)
            task = asyncio.create_task(self._load_past(driver))
            _background_task(task)
            done, _ = await asyncio.wait({task}, timeout=MEMORY_FIRST_TURN_S)
            if done:
                sess.update(task.result())
            else:
                sess["past_task"] = task
        elif req.session_id in _SESSIONS:
            _adopt_past(_SESSIONS[req.session_id])
        res = await self._handle(req)
        _fit_for_speed(req, res)
        if res.status == "chat" and GOODBYE.search(req.text or ""):
            res.end_conversation = True
        if driver and res.status != "not_understood":  # saved in the background: the reply (and TTS) never waits on the DB
            memory.add_later(driver, [("driver", (req.text or "").strip()[:500]), ("copilot", res.reply[:500])])
            sess_now = _session(req.session_id) if req.session_id else None
            if sess_now is not None:
                sess_now["driver"] = driver
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
            out["facts"] = stored["facts"]
        return out

    def _context(self, req: AgentRequest, sess: dict) -> str:
        _adopt_past(sess)
        context = f"Driver location: {req.lat:.5f}, {req.lng:.5f}."
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

    async def _cheapest_without_prices(self, req: AgentRequest, sess: dict, text: str) -> AgentResponse:
        """No place in the list has a diesel price: keep the current stop and say so; with no current stop
        the closest one is the honest pick, with the usual "no price" caveat."""
        if req.active_place:
            logger.info("agent pick %r with no diesel prices in the list -> keeping %s", text, req.active_place.name)
            return AgentResponse(status="chat", transcript=text, source=self.source,
                                 reply=NO_PRICES_KEEP.format(name=req.active_place.name))
        nearest = min(sess["places"], key=lambda p: p.distance_mi or 0)
        logger.info("agent pick %r with no diesel prices in the list -> the closest, %s", text, nearest.name)
        return await self._route_to(req, nearest, sess["places"], text)

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
            self._regional_note(place), self._route_weather(route, _SESSIONS.get(req.session_id or "")),
            self._route_chains(route))
        reply = _route_reply(place, route, n, req.truck_profile.driver_name, regional) + price_caveat(text, place)
        sess_now = _SESSIONS.get(req.session_id or "")
        learned = sess_now.pop("say_learned", None) if sess_now else None
        for line in (chain_line, weather_line, learned):
            if line:
                reply = f"{reply} {line}"
        return AgentResponse(status="route_set", transcript=text, category=place.category, reply=reply,
                             place=place, route=route, parking=approach.best(place),
                             options=others, source=self.source)

    async def _chains_question(self, req: AgentRequest, sess: dict, text: str) -> AgentResponse:
        """Chain controls along the route the driver is on, from Caltrans; without a route, say what is needed."""
        cached = sess.get("alternatives") or {}
        route = cached.get("current") if req.active_place and cached.get("place") == req.active_place.id else None
        if route is None:
            return AgentResponse(status="chat", transcript=text, reply=CHAINS_NO_ROUTE, source=self.source)
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
    async def _refresh_facts(driver: str, sess: dict) -> None:
        facts = await profile_learn.refresh_if_due(driver)
        if facts is not None:
            sess["facts"] = facts

    @staticmethod
    async def _note_rejection(sess: dict, place: Place) -> None:
        """Three "not that one" on the same chain in one dialog: remember to steer clear of it."""
        chain = _chain_of(place.name)
        if not chain:
            return
        counts = sess.setdefault("rejected_chains", {})
        counts[chain] = counts.get(chain, 0) + 1
        avoided = [c.lower() for c in (sess.get("facts") or {}).get("chains_avoided") or []]
        if counts[chain] == REJECTIONS_TO_LEARN and chain.lower() not in avoided and on("PROFILE_LEARN"):
            sess["facts"] = await profile_learn.learn_avoid(sess.get("driver"), sess.get("facts") or {}, chain)
            sess["say_learned"] = profile_learn.LEARNED_AVOID.format(chain=chain).strip()

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
        line = profile_learn.facts_line(sess.get("facts"))
        prefs = profile_learn.prefs_line(req.truck_profile)
        if line:
            reply = profile_learn.KNOW.format(facts=line) + (f" {prefs}" if prefs else "")
        else:
            # No facts yet (they need ~5 turns) but earlier lines exist: "what's my dog's name?" is answered
            # from them, as it was before facts existed (live test test_long_term_recall_across_sessions).
            _adopt_past(sess)
            reply = await profile_learn.recall(text, sess.get("past") or []) or prefs or profile_learn.KNOW_NOTHING
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
            sess["alternatives"] = {"place": place.id, "routes": list(route.alternatives), "i": 0,
                                    "current": route.model_copy(update={"alternatives": []}),
                                    "origin": (req.lat, req.lng), "profile": _profile_key(req.truck_profile)}
            sess.pop("via", None)

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

    def _stops(self, req: AgentRequest, sess: dict) -> list[Place]:
        """Stops still ahead on the way to the active destination in this dialog (empty for any other route). A
        stop the truck has driven past is dropped: re-planning through it sent the driver back to it."""
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
        """Stops in the order the road meets them (by the nearest vertex of the current line, else by distance
        from the truck): "swing by Love's first" after a Fernley stop used to drive to Fernley and back."""
        geometry = self._current_geometry(req, sess)
        if geometry:
            return sorted(stops, key=lambda s: (_nearest_index(geometry, s.lat, s.lng),
                                                haversine_km(s.lat, s.lng, req.lat, req.lng)))
        return sorted(stops, key=lambda s: haversine_km(s.lat, s.lng, req.lat, req.lng))

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
        picked = pick_place(d, sess["places"])
        if picked:
            return picked
        query = str(d.get("query") or "").strip()
        category = d.get("category") if d.get("category") in CATEGORIES else None
        named = named_place(query, sess["places"]) if query else None
        if named:
            logger.info("add_stop query %r names a listed place -> %s", query, named.name)
            return named
        if query:
            if TRUCK_STOP_NAME.search(query) and not re.search(r"\d", query):
                # "the Pilot", "Love's in Auburn": a chain name is a truck stop near the driver before it is an
                # address (the geocoder once answered with the hamlet of Pilot, NV, 300 miles past the destination)
                stop = await self._truck_stop_named(req, query)
                if stop:
                    return stop
            with stage("tools"):
                matches = await geocode(with_city(query, sess.get("city")), req.lat, req.lng)
            if not matches or is_street(matches[0]):
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
        category = category or "truck_stop"
        with stage("tools"):
            res = await get_places_provider().search(category, req.lat, req.lng)
        found = sorted(res.places, key=lambda p: p.distance_mi or 0)
        return choose_place(found, req.truck_profile, {req.active_place.id} if req.active_place else set(),
                            sess.get("facts"))

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
        stops = self._stops(req, sess)
        if len(stops) >= MAX_VIA:
            return AgentResponse(status="chat", transcript=text, reply=VIA_FULL, source=self.source)
        stop = await self._resolve_stop(req, d, sess, text)
        if not stop:
            what = str(d.get("query") or CATEGORY_WORD.get(str(d.get("category")), "a stop")).strip()
            return AgentResponse(status="chat", transcript=text, reply=heard_not_found(what), source=self.source)
        if stop.id == place.id or haversine_km(stop.lat, stop.lng, place.lat, place.lng) < 0.15:
            # "stop at the 49er" while going to the 49er: the model echoed the destination's address and the
            # geocode of it came back as a new place (QA 2026-10-31, H9)
            return AgentResponse(status="chat", transcript=text, source=self.source,
                                 reply=VIA_IS_DESTINATION.format(name=place.name.split(",")[0]))
        if any(s.id == stop.id for s in stops):  # "stop at the 49er" when it already is one (QA 2026-10-31, H9)
            return AgentResponse(status="chat", transcript=text, source=self.source,
                                 reply=VIA_DUPLICATE.format(name=stop.name.split(",")[0]))
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
                                   "no": {"reply": VIA_FAR_NO}, "yes": VIA_FAR_YES}
                timing.note("stop_guard", "asked")
                logger.info("add_stop %s is %.0f min out of the way: asking first", stop.name, extra / 60)
                return AgentResponse(status="confirm", transcript=text, source=self.source,
                                     reply=VIA_FAR.format(name=stop.name.split(",")[0], extra=_minutes_said(round(extra / 60)),
                                                          new=route.summary.split(" · ")[0], old=_minutes_said(round(old / 60))))
        self._remember_driven(req, sess, route)
        sess["via"] = {"place": place.id, "stops": stops}
        ahead = f", about {_miles(stop.distance_mi)} miles ahead" if stop.distance_mi else ""
        dwell = VIA_DWELL.format(minutes=VIA_STOP_S // 60, each="the stop" if len(stops) == 1 else "each stop")
        reply = VIA_ADDED.format(name=stop.name.split(",")[0], ahead=ahead, time=route.summary.split(" · ")[0],
                                 mi=_miles(route.distance_mi), dwell=dwell) + price_caveat(text, stop)
        return AgentResponse(status="route_set", transcript=text, category=place.category, reply=reply, place=place,
                             route=route, parking=approach.best(place), options=stops, source=self.source)

    async def _clear_stops(self, req: AgentRequest, sess: dict, text: str) -> AgentResponse:
        place = req.active_place
        if not place or not self._stops(req, sess):
            return AgentResponse(status="chat", transcript=text, reply=VIA_NONE, source=self.source)
        route = await self._route_with_stops(req, [])
        self._remember_driven(req, sess, route)
        sess.pop("via", None)  # after the route: a failed call must not forget stops the app still shows
        reply = VIA_CLEARED.format(time=route.summary.split(" · ")[0], mi=_miles(route.distance_mi))
        return AgentResponse(status="route_set", transcript=text, category=place.category, reply=reply, place=place,
                             route=route, parking=approach.best(place), source=self.source)

    async def _alternative(self, req: AgentRequest, sess: dict, text: str) -> AgentResponse:
        """Next unused alternative route to the active destination; fetched fresh when the dialog has none."""
        place = req.active_place
        if not place:
            return AgentResponse(status="chat", transcript=text, reply=ALT_NO_ROUTE, source=self.source)
        cached = sess.get("alternatives")
        if self._alternatives_stale(cached, req, place):
            stops = self._stops(req, sess)
            with stage("tools"):
                try:
                    fresh = await get_routing_provider().route(req.lat, req.lng, *approach.target(place),
                                                               req.truck_profile, alternatives=ALTERNATIVES_FRESH,
                                                               via=[[s.lat, s.lng] for s in stops])
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
        if prev is not None and all(r is not prev for r in cached["routes"]):
            cached["routes"].append(prev)
        cached["current"] = route
        route.alternatives = [r.model_copy(update={"alternatives": []}) for r in cached["routes"] if r is not route]
        reply = _alt_reply(route, prev)
        warning = _route_warning(route)  # a closure or a restriction on the other way was never said before
        if warning:
            reply = f"{reply} {warning}"
        return AgentResponse(status="route_set", transcript=text, category=place.category,
                             reply=reply, place=place, route=route, parking=approach.best(place),
                             source=self.source)

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
        chosen = choose_place(found, req.truck_profile, exclude, _facts(req))
        return await self._route_to(req, chosen, found, text)

    async def _handle(self, req: AgentRequest) -> AgentResponse:
        text = (req.text or "").strip()
        if not re.search(r"[a-zA-Z]{2,}", text):
            return _not_understood(text, self.source)
        sess = _session(req.session_id)
        bd = await breakdown.handle(req, sess, text, self.source)
        if bd:
            return bd
        pending = sess.pop("pending", None)
        if pending:  # "Did you say Reno?" was asked last turn
            kind = pending.get("kind")  # confirm | stop_guard | ...: the answer goes to the turn log and trip events
            if YES.match(text) or (pending.get("yes") is not None and pending["yes"].match(text)):
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
                    sess.setdefault("dest_declined", set()).add(branch["declined"])
                if branch:  # a question with a real "no" path (e.g. "chains on board?")
                    if branch.get("then_pending"):
                        sess["pending"] = branch["then_pending"]
                    acts = [AppAction(type=a["type"], value=a.get("value")) for a in branch.get("actions") or []]
                    return AgentResponse(status="chat", transcript=text, reply=branch["reply"], actions=acts,
                                         source=self.source)
                return AgentResponse(status="chat", transcript=text, reply=CONFIRM_NO, source=self.source)
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
            logger.info("agent find hotel for %r -> truck_stop", text)
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
                logger.info("agent %s %s %r with a route active -> add_stop", intent, category, text)
                d = {**d, "intent": "add_stop", "category": category}
                if intent == "pick" and PRICE_WORDS.search(text) and all(p.diesel_price is None for p in sess["places"]):
                    d["place_index"] = None
                intent = "add_stop"
        # The model's word is not the last word on two things the driver says a lot (QA 2026-10-30, H4):
        # a rejected place read as "another road", and a reference into the list read as an address.
        intent, d = reject_or_reference_guard(intent, d, text, sess["places"], req.active_place)
        if intent == "not_understood":
            return _not_understood(text, self.source)
        # "The cheapest diesel" over a list with no prices: the model still names one, and the destination
        # changed for no reason the driver could see (QA 2026-10-31, H8). No prices, no switch.
        if intent == "pick" and d.get("place_index") is not None and PRICE_WORDS.search(text) and sess["places"] \
                and all(p.category == "truck_stop" and p.diesel_price is None for p in sess["places"]):
            return await self._cheapest_without_prices(req, sess, text)
        # Current pick: the active route, or the last place chosen in this dialog.
        last = req.active_place or (sess["places"][0] if sess["places"] else None)
        current = last.id if last else None
        exclude = {current} if current else set()
        if current and (intent == "pick" or (intent == "find" and last and d.get("category") == last.category)):
            # "not that one" / "something else": never offer an already rejected place again in this dialog.
            rejected = sess.setdefault("rejected", set())
            if current not in rejected and last is not None:
                await self._note_rejection(sess, last)
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
            if intent == "memory":
                return await self._memory_intent(req, sess, d, text)
            if intent == "pick":
                place = pick_place(d, sess["places"])
                # A named pick ("the second one", "the Pilot") may go back to a place turned down earlier: the
                # driver changed their mind, and "want me to go back to one of them?" is what we offer. Only
                # a bare rejection skips the rejected set.
                explicit = d.get("place_index") is not None
                if place and place.id != current and (explicit or place.id not in exclude):
                    if explicit:
                        sess.get("rejected", set()).discard(place.id)
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
                if place.id in sess.get("dest_declined", set()):  # said no to this one already in this dialog
                    return AgentResponse(status="chat", transcript=text, source=self.source,
                                         reply=DEST_ONLY.format(said=str(d["query"]).strip()))
                reason = dest_check(query, place)
                if reason and not confirmed:
                    # "yes" re-runs this turn with the place stashed (no second geocode); "no" remembers the refusal
                    sess["pending"] = {"text": text, "kind": "dest_check",
                                       "d": {**d, "place": place.model_dump(), "city": city_of(query, place)},
                                       "no": {"reply": DEST_NO, "declined": place.id}, "yes": DEST_YES}
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
