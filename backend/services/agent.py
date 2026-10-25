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
import uuid
from collections import OrderedDict
from typing import Optional

from emergentintegrations.llm.chat import LlmChat, UserMessage

from models import AgentRequest, AgentResponse, Place, RouteInfo, TruckProfile
from services.geocode import geocode
from services.parking import cached_parking, check_parking, listed_in_data
from services.places import get_places_provider
from services.routing import get_routing_provider
from services.timing import stage

logger = logging.getLogger("truckbuddy.agent")

NOT_UNDERSTOOD = "Sorry, I didn't catch that. Try again."
NO_PLACES = "I couldn't find one nearby"
NO_ADDRESS = "I couldn't find that place. Can you say the address again?"
MODEL = ("openai", "gpt-5.4-mini")
MAX_OPTIONS = 4
PRECHECK = 3  # nearest candidates satellite-checked in the background
FAVORITE_EXTRA_MI = 5.0
TRUCK_TAGS = ("truck parking", "53 ft", "truck lot", "hgv")
CATEGORIES = ("truck_stop", "hotel", "rest_area")

SYSTEM = """You are TruckBuddy, a hands-free voice copilot for a long-haul truck driver who is driving.
Truck: {truck}.
Driver preferences: {prefs}.

Classify the driver's LAST utterance and answer with JSON only, no other text:
{{"intent": "find"|"go"|"pick"|"chat"|"bye"|"unclear", "category": "truck_stop"|"hotel"|"rest_area"|null, "query": string|null, "place_index": integer|null, "reply": string|null}}

- find: the driver needs a place. Pick the category yourself, never ask:
  truck_stop = fuel, diesel, DEF, food, showers, truck stop;
  hotel = sleep, tired, overnight, bed, motel, hotel, room;
  rest_area = quick break, stretch, restroom, short nap, rest area.
- go: the driver names a destination (city, address, business, landmark: "take me to Reno", "go to 1200 Main St Dallas"). query = the destination, e.g. "Reno, NV".
- pick: the driver refers to a place found earlier in this conversation ("the second one", "the Pilot", "not that one", "something closer"). place_index = the number of the best matching place in "Places found earlier" (1, 2, 3...), never the current route. If no earlier place fits, use find with the same category instead.
- chat: a question or remark that is not a place request ("how far is it?", "where do I park?", "thanks"). reply = a brief answer using the context. For parking/entry questions use only the satellite parking check; if there is none, say you haven't checked parking there yet. Never invent parking details.
- bye: goodbye or done ("bye", "that's all", "no, I'm good", "that's it"). reply = one short friendly line.
- unclear: only a sound or noise label (siren, beeping, music) or unintelligible.
reply is only for chat and bye: English, spoken style, no markdown, at most 2 short sentences, never repeat yourself.
"""

MAX_HISTORY = 8
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
    return f"{mi:.1f}" if mi < 10 else f"{mi:,.0f}"


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
}
NOTICES = (
    ("toll", "Heads up, couldn't dodge all the tolls on this one."),
    ("restricted", "Heads up, part of this route might be tight for your rig, so watch the signs."),
    ("ferry", "Heads up, there's a ferry on this one."),
)
_turns: dict = {"n": 0}  # rotation when there's no dialog session


def _notice(raw: str) -> str:
    low = raw.lower()
    return next((say for key, say in NOTICES if key in low), "Heads up, check this route for restrictions.")


def _route_reply(place: Place, route: RouteInfo, n: int = 0, driver: Optional[str] = None) -> str:
    """Conversational intro + one line with the place, a landmark and the distance; then a notice, if any.
    The driver's name is used about every third answer."""
    intro, body = TEMPLATES[place.category][n % len(TEMPLATES[place.category])]
    street = _street(place.address)
    dest = place.name.split(",")[0]
    vals = {"name": place.name, "dest": dest, "mi": _miles(route.distance_mi), "time": route.summary.split(" · ")[0],
            "off": f", right off {street}" if street else "", "up": f", just up {street}" if street else ""}
    intro = intro.format(**vals)
    first_name = (driver or "").split(" ")[0]
    out = f"{intro}, {first_name}. " if first_name and n % 3 == 2 else f"{intro}. "
    out += body.format(**vals)
    return f"{out} {_notice(route.notices[0])}" if route.notices else out


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
    ok = [p for p in pool if _verdict(p) != "unlikely"] or pool
    dist = lambda p: p.distance_mi or 0.0  # noqa: E731
    nearest = min(dist(p) for p in ok)
    chains = [re.compile(rf"\b{re.escape(c)}\b", re.I) for c in truck.favorite_chains if c.strip()]
    fav = [p for p in ok if any(rx.search(p.name) for rx in chains) and dist(p) <= nearest + FAVORITE_EXTRA_MI]
    if fav:
        return min(fav, key=dist)
    good = [p for p in ok if _verdict(p) in ("listed", "likely") or any(w in t.lower() for t in p.tags for w in TRUCK_TAGS)]
    return min(good or ok, key=dist)


def _parse(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


async def classify(text: str, context: str, truck: TruckProfile) -> dict:
    chat = LlmChat(api_key=os.environ["EMERGENT_LLM_KEY"], session_id=f"turn-{uuid.uuid4()}",
                   system_message=SYSTEM.format(truck=truck.label, prefs=truck.prefs_label)).with_model(*MODEL)
    with stage("llm_round_1"):
        raw = await chat.send_message(UserMessage(text=f"[{context}]\nDriver said: {text}"))
    return _parse(raw)


class LlmAgentProvider:
    source = MODEL[1]

    async def handle(self, req: AgentRequest) -> AgentResponse:
        res = await self._handle(req)
        if res.status == "chat" and GOODBYE.search(req.text or ""):
            res.end_conversation = True
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
        if sess["history"]:
            context += " Conversation so far: " + " | ".join(sess["history"][-MAX_HISTORY:]) + "."
        if sess["places"]:
            context += " Places found earlier: " + "; ".join(
                f"{i}={p.name} ({p.category}) {p.distance_mi or 0:.1f} mi"
                + (" [current route]" if req.active_place and p.id == req.active_place.id else "")
                for i, p in enumerate(sess["places"], 1)) + "."
        cur = req.active_place
        if cur:
            context += f" Current route: {cur.name} ({cur.address or 'address unknown'})"
            if req.active_distance_mi is not None:
                context += f", {req.active_distance_mi:.1f} miles"
            if req.active_duration_min is not None:
                context += f", about {req.active_duration_min} minutes"
            context += "."
            pk = cached_parking(cur) if cur.category != "destination" else None
            if pk:
                context += f" Satellite parking check there: {pk.label}. {pk.reason}"
                if pk.entry_tip:
                    context += f" Entry: {pk.entry_tip}."
                if pk.park_tip:
                    context += f" Park: {pk.park_tip}."
            elif cur.category != "destination":
                context += " Parking there has not been checked yet."
        else:
            context += " No route is active."
        return context

    async def _route_to(self, req: AgentRequest, place: Place, found: list[Place], text: str) -> AgentResponse:
        with stage("tools"):
            route = await get_routing_provider().route(req.lat, req.lng, place.lat, place.lng, req.truck_profile)
        others = [p for p in found if p.id != place.id][:MAX_OPTIONS]
        counter = _SESSIONS[req.session_id] if req.session_id in _SESSIONS else _turns
        n = counter.get("n", 0)
        counter["n"] = n + 1
        return AgentResponse(status="route_set", transcript=text, category=place.category,
                             reply=_route_reply(place, route, n, req.truck_profile.driver_name), place=place, route=route,
                             parking=None if place.category == "destination" else cached_parking(place),
                             options=others, source=self.source)

    async def _find(self, req: AgentRequest, category: str, text: str, exclude: set[str]) -> AgentResponse:
        with stage("tools"):
            res = await get_places_provider().search(category, req.lat, req.lng)
        found = res.places[:MAX_OPTIONS + 2]
        if not found:
            return AgentResponse(status="no_results", transcript=text, category=category,
                                 reply=f"{NO_PLACES}.", message=NO_PLACES, source=self.source)
        for p in found[:PRECHECK]:
            _background(check_parking(p))  # warm satellite checks; never wait for them here
        chosen = choose_place(found, req.truck_profile, exclude)
        return await self._route_to(req, chosen, found, text)

    async def _handle(self, req: AgentRequest) -> AgentResponse:
        text = (req.text or "").strip()
        if not re.search(r"[a-zA-Z]{2,}", text):
            return _not_understood(text, self.source)
        sess = _session(req.session_id)
        try:
            d = await classify(text, self._context(req, sess), req.truck_profile)
        except Exception:
            logger.exception("agent LLM failed")
            return _not_understood(text, self.source)

        intent = d.get("intent")
        reply = str(d.get("reply") or "").strip()
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
            if intent == "pick":
                place = pick_place(d, sess["places"])
                if place and place.id != current:
                    return await self._route_to(req, place, sess["places"], text)
                category = d.get("category") or (last.category if last else None)
                if category in CATEGORIES:
                    return await self._find(req, category, text, exclude)
                return _not_understood(text, self.source)
            if intent == "find" and d.get("category") in CATEGORIES:
                return await self._find(req, d["category"], text, exclude)
            if intent == "go" and str(d.get("query") or "").strip():
                with stage("tools"):
                    matches = await geocode(str(d["query"]).strip(), req.lat, req.lng)
                if not matches:
                    return AgentResponse(status="chat", transcript=text, reply=NO_ADDRESS, source=self.source)
                return await self._route_to(req, matches[0], [], text)
        except Exception:
            logger.exception("agent action failed")
            return _not_understood(text, self.source)
        if intent in ("chat", "bye") and reply:
            return AgentResponse(status="chat", transcript=text, reply=reply, end_conversation=intent == "bye",
                                 source=self.source)
        return _not_understood(text, self.source)


_provider = LlmAgentProvider()


def get_agent_provider() -> LlmAgentProvider:
    return _provider
