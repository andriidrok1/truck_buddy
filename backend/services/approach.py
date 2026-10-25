"""Approach-aware parking: which way the driver arrives, entrance side relative to the driver,
answers to "how do I get in / where do I park / will I fit", and routing to the entrance (E).

- from_route(): heading over the last 300 m, the route point 100 m before the finish and the
  street of the last HERE maneuver.
- side(): right/left of the entrance E by geometry (sign of the 2D cross product), no vision call.
- relative(): "On your {side}, off {street}, {landmarks}"; landmarks = the base entry tip
  reworded by a small text model (compass and left/right words removed). < 1 s, cached.
- answer(): spoken parking answer, waiting up to WAIT_S for a check in flight.
"""
import asyncio
import logging
import math
import os
import re
import uuid
from collections import OrderedDict
from typing import Literal, Optional

from emergentintegrations.llm.chat import LlmChat, UserMessage

from models import Approach, ParkingCheck, Place, RouteInfo

logger = logging.getLogger("truckbuddy.approach")

SPAN_M = 300.0  # heading is measured over the last 300 m
POINT_M = 100.0  # approach point: 100 m before the finish
WAIT_S = 15.0
LANDMARK_TIMEOUT_S = 3.0
CACHE_MAX = 500
COMPASS = ["north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"]
LOOKING = "Let me look at the satellite."
STILL = "Still looking at the satellite, I'll tell you when I have it."
_STREET = re.compile(r"\b(?:onto|on)\s+(.+?)(?:\.\s|\.?$|,| toward)", re.I)
# Words that only make sense from the satellite's point of view (north-up), not the driver's.
_DIRECTIONAL = re.compile(
    r"\b(north|south|east|west)(?:ern)?(?:[- ]?(?:east|west)(?:ern)?)?(?:-side| side)?\b|\b(right|left)(?:-hand)?\b", re.I)

PROMPT = """- parking: a question about getting into or parking at a place ("how do I get in?", "where do I park?", "will I fit?", "is there truck parking?"). place_index = the number of that place in "Places found earlier", or null for the current route."""


class _LRU(OrderedDict):
    def put(self, k, v):
        self[k] = v
        self.move_to_end(k)
        while len(self) > CACHE_MAX:
            self.popitem(last=False)


_last: _LRU = _LRU()  # place key -> approach of the latest route to it
_landmarks: _LRU = _LRU()  # base entry tip -> landmark phrase ("" = none)
_tasks: set = set()


def background(coro) -> None:
    """Fire-and-forget that keeps a reference so the task isn't garbage-collected."""
    t = asyncio.ensure_future(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


def _pkey(p: Place) -> str:
    return f"{p.lat:.5f},{p.lng:.5f}"


def _m(a: list, b: list) -> float:
    dlat, dlng = math.radians(b[0] - a[0]), math.radians(b[1] - a[1])
    s = math.sin(dlat / 2) ** 2 + math.cos(math.radians(a[0])) * math.cos(math.radians(b[0])) * math.sin(dlng / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(s))


def _bearing(a: list, b: list) -> float:
    la1, la2, dl = math.radians(a[0]), math.radians(b[0]), math.radians(b[1] - a[1])
    y = math.sin(dl) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _back(g: list, meters: float) -> tuple[int, float]:
    """Index of the route point `meters` before the finish and the distance actually walked."""
    i, dist = len(g) - 1, 0.0
    while i > 0 and dist < meters:
        dist += _m(g[i - 1], g[i])
        i -= 1
    return i, dist


def from_route(route: RouteInfo) -> Optional[Approach]:
    g = route.geometry
    if len(g) < 2:
        return None
    i, dist = _back(g, SPAN_M)
    if dist < 15:
        return None
    b = _bearing(g[i], g[-1])
    p = g[_back(g, POINT_M)[0]]
    street = None
    for m in reversed(route.maneuvers):
        if m.action == "arrive":
            continue
        hit = _STREET.search(m.instruction)
        if hit:
            street = hit.group(1).strip()[:60]
            break
    return Approach(bearing=round(b) % 360, compass=COMPASS[round(((b + 180) % 360) / 45) % 8], street=street,
                    lat=p[0], lng=p[1])


def side(ap: Approach, e_lat: float, e_lng: float) -> Optional[Literal["right", "left"]]:
    """Side of E for a driver at the approach point moving along `bearing`.

    Local plane, x = east, y = north (right-handed, north up). heading h = (sin b, cos b),
    v = E - approach point. cross = h.x*v.y - h.y*v.x: negative = clockwise from the heading = right.
    """
    if ap.lat is None or ap.lng is None:
        return None
    b = math.radians(ap.bearing)
    hx, hy = math.sin(b), math.cos(b)
    vx = (e_lng - ap.lng) * 111320 * math.cos(math.radians(ap.lat))
    vy = (e_lat - ap.lat) * 111320
    cross = hx * vy - hy * vx
    if abs(cross) < 1.0:  # dead ahead: don't guess
        return None
    return "right" if cross < 0 else "left"


def remember(place: Place, ap: Optional[Approach]) -> None:
    if ap:
        _last.put(_pkey(place), ap)


def has_approach(place: Place) -> bool:
    """A route to this place was built (its entry tip will be phrased relative to the driver)."""
    return _pkey(place) in _last


def _entry(c: Optional[ParkingCheck]):
    return next((s for s in (c.spots if c else []) if s.kind == "entry"), None)


def best(place: Place) -> Optional[ParkingCheck]:
    """Cached check; entry tip relative to the driver when the approach and E are known (sync, cached text)."""
    from services.parking import cached_parking
    base = cached_parking(place)
    ap = _last.get(_pkey(place))
    if not base or not ap or not base.entry_tip or base.entry_tip not in _landmarks:
        return base
    return _phrase(base, ap, _landmarks[base.entry_tip])


def target(place: Place) -> tuple[float, float]:
    """Route end: the truck entrance (E) when the satellite check found one, else the place."""
    e = _entry(best(place))
    return (e.lat, e.lng) if e else (place.lat, place.lng)


def note(place: Place) -> str:
    """Context line for the agent: verdict / Entry / Park, or "not checked yet"."""
    c = best(place)
    if not c:
        return "parking not checked yet"
    out = c.verdict
    if c.entry_tip:
        out += f"; Entry: {c.entry_tip}"
    if c.park_tip:
        out += f"; Park: {c.park_tip}"
    return out


def _clean(text: str) -> str:
    t = _DIRECTIONAL.sub("", text)
    t = re.sub(r"\s{2,}", " ", re.sub(r"\s+([,.])", r"\1", t)).strip(" ,.;-")
    return t[:1].lower() + t[1:] if t else ""


async def landmarks(tip: str) -> str:
    """'Use the wide north driveway, just after the landscaped island' -> 'just after the landscaped island'."""
    if tip in _landmarks:
        return _landmarks[tip]
    out = ""
    try:
        chat = LlmChat(api_key=os.environ["EMERGENT_LLM_KEY"], session_id=f"landmarks-{uuid.uuid4()}",
                       system_message="You shorten driving notes.").with_model("openai", "gpt-5.4-mini")
        raw = await asyncio.wait_for(chat.send_message(UserMessage(text=(
            "From this truck entrance note keep only the landmarks, as a short phrase (max 10 words) that "
            "continues a sentence, e.g. 'just after the landscaped island' or 'past the fuel canopy'. "
            "Never use north, south, east, west, right or left. If there is no landmark answer NONE.\n"
            f"Note: {tip}"))), LANDMARK_TIMEOUT_S)
        text = str(raw or "").strip().strip('"').split("\n")[0]
        out = "" if text.upper().startswith("NONE") else _clean(text)[:80]
    except Exception as e:  # noqa: BLE001 - no landmarks is fine
        logger.warning("landmarks failed for %r: %s", tip, e)
    _landmarks.put(tip, out)
    return out


def _phrase(base: ParkingCheck, ap: Approach, marks: str) -> ParkingCheck:
    e = _entry(base)
    s = side(ap, e.lat, e.lng) if e else None
    if not s:
        return base  # no E (or straight ahead): keep the base tip, don't name a side
    parts = [f"On your {s}"]
    if ap.street:
        parts.append(f"off {ap.street}")
    if marks:
        parts.append(marks)
    return base.model_copy(update={"entry_tip": ", ".join(parts), "relative": True})


async def relative(place: Place, base: ParkingCheck, ap: Approach) -> ParkingCheck:
    """Same check, entry tip phrased for this approach (geometry + cached text rewrite, < 1 s)."""
    remember(place, ap)
    if not base.entry_tip or not _entry(base):
        return base
    res = _phrase(base, ap, await landmarks(base.entry_tip))
    if res.relative:
        logger.info("entry %s: %r -> %r", place.name, base.entry_tip, res.entry_tip)
    return res


def speech(c: ParkingCheck) -> str:
    verdict = {
        "listed": "They list truck parking there.",
        "likely": "From the satellite, it looks like your rig will fit.",
        "unlikely": "Heads up, from the satellite it looks tight for a rig.",
        "unclear": "I can't tell for sure from the satellite if a rig fits.",
    }[c.verdict]
    parts = [verdict]
    if c.verdict == "unlikely" and c.nearby:
        p = c.nearby.phrase
        parts.append(f"{p[0].upper()}{p[1:]}.")
    if c.entry_tip:
        tip = c.entry_tip.rstrip(".")
        parts.append(f"The truck entrance is {tip[0].lower()}{tip[1:]}." if c.relative else f"To get in: {tip}.")
    if c.park_tip:
        parts.append(f"{c.park_tip.rstrip('.')}.")
    return " ".join(parts)


async def answer(place: Place, wait: float = WAIT_S) -> tuple[str, str, Optional[ParkingCheck]]:
    """-> (status "ready" | "pending", spoken reply, check). Starts the check if needed, waits up to `wait`."""
    from services.parking import check_parking
    task = asyncio.ensure_future(check_parking(place, _last.get(_pkey(place))))
    try:
        c = await asyncio.wait_for(asyncio.shield(task), wait)
    except asyncio.TimeoutError:
        return "pending", STILL, None
    return "ready", speech(c), c
