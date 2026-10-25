"""Approach-aware parking: which way the driver arrives, entrance relative to the driver,
answers to "how do I get in / where do I park / will I fit", and routing to the entrance (E).

- from_route(): bearing over the last 300 m of the route + street of the last HERE maneuver.
- relative(): second vision call on the SAME satellite image that rewrites only the entry tip
  as "On your right/left ..." for that approach (warm-up checks keep compass directions).
- answer(): spoken parking answer, waiting up to WAIT_S for a check in flight.
"""
import asyncio
import base64
import logging
import math
import os
import re
import uuid
from typing import Optional

from emergentintegrations.llm.chat import ImageContent, LlmChat, UserMessage

from models import Approach, ParkingCheck, Place, RouteInfo

logger = logging.getLogger("truckbuddy.approach")

SPAN_M = 300.0
WAIT_S = 15.0
REWRITE_TIMEOUT_S = 25
COMPASS = ["north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"]
LOOKING = "Let me look at the satellite."
STILL = "Still looking at the satellite, I'll tell you when I have it."
RELATIVE = re.compile(r"^On your (right|left)\b", re.I)
_STREET = re.compile(r"\b(?:onto|on)\s+(.+?)(?:\.\s|\.?$|,| toward)", re.I)

PROMPT = """- parking: a question about getting into or parking at a place ("how do I get in?", "where do I park?", "will I fit?", "is there truck parking?"). place_index = the number of that place in "Places found earlier", or null for the current route."""

_last: dict[str, Approach] = {}  # place key -> approach of the latest route to it
_rel: dict[str, ParkingCheck] = {}  # place key|bearing bucket -> check with a relative entry tip
_rel_pending: dict[str, "asyncio.Task[ParkingCheck]"] = {}


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


def from_route(route: RouteInfo) -> Optional[Approach]:
    g = route.geometry
    if len(g) < 2:
        return None
    i, dist = len(g) - 1, 0.0
    while i > 0 and dist < SPAN_M:
        dist += _m(g[i - 1], g[i])
        i -= 1
    if dist < 15:
        return None
    b = _bearing(g[i], g[-1])
    street = None
    for m in reversed(route.maneuvers):
        if m.action == "arrive":
            continue
        hit = _STREET.search(m.instruction)
        if hit:
            street = hit.group(1).strip()[:60]
            break
    return Approach(bearing=round(b) % 360, compass=COMPASS[round(((b + 180) % 360) / 45) % 8], street=street)


def remember(place: Place, ap: Optional[Approach]) -> None:
    if ap:
        _last[_pkey(place)] = ap


def has_approach(place: Place) -> bool:
    """A route to this place was built (its entry tip will be rewritten relative to the driver)."""
    return _pkey(place) in _last


def _rkey(place: Place, ap: Approach) -> str:
    return f"{_pkey(place)}|{ap.bearing // 30}"


def best(place: Place) -> Optional[ParkingCheck]:
    """Cached check, with the relative entry tip if one exists for the latest approach."""
    from services.parking import cached_parking
    base = cached_parking(place)
    ap = _last.get(_pkey(place))
    return (_rel.get(_rkey(place, ap)) if ap else None) or base


def target(place: Place) -> tuple[float, float]:
    """Route end: the truck entrance (E) when the satellite check found one, else the place."""
    c = best(place)
    e = next((s for s in (c.spots if c else []) if s.kind == "entry"), None)
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


async def _rewrite(img: bytes, base: ParkingCheck, ap: Approach) -> str:
    e = next((s for s in base.spots if s.kind == "entry"), None)
    along = f" along {ap.street}" if ap.street else ""
    chat = LlmChat(api_key=os.environ["EMERGENT_LLM_KEY"], session_id=f"approach-{uuid.uuid4()}",
                   system_message="You give a truck driver one short, concrete direction to a site entrance "
                                  "from a satellite image (north is up, ~300 m across).").with_model("openai", "gpt-5.5")
    text = (f"The driver arrives from the {ap.compass} heading {ap.bearing}°{along}. "
            "Describe the entrance relative to the driver: on your right/left, before/after a landmark. "
            f"The truck entrance was described as: '{base.entry_tip or 'see marker'}'"
            + (f", marked at x={e.x:.2f}, y={e.y:.2f} of the image" if e else "") + ". "
            "Answer with ONE sentence, max 18 words, that starts with 'On your right' or 'On your left', "
            "and name the street it's off if you can.")
    out = await chat.send_message(UserMessage(text=text, file_contents=[ImageContent(image_base64=base64.b64encode(img).decode())]))
    return str(out or "").strip().strip('"').split("\n")[0][:160]


async def relative(place: Place, base: ParkingCheck, ap: Approach) -> ParkingCheck:
    """Same check, entry tip rewritten for this approach (single-flight, cached)."""
    remember(place, ap)
    if base.source == "none" or not base.image_path or not (base.entry_tip or base.spots):
        return base
    k = _rkey(place, ap)
    if k in _rel:
        return _rel[k]
    task = _rel_pending.get(k)
    if task is None:
        task = asyncio.create_task(_relative(place, base, ap, k))
        _rel_pending[k] = task
        task.add_done_callback(lambda _t: _rel_pending.pop(k, None))
    return await asyncio.shield(task)


async def _relative(place: Place, base: ParkingCheck, ap: Approach, k: str) -> ParkingCheck:
    from services.parking import fetch_satellite
    try:
        img = await fetch_satellite(place.lat, place.lng)  # cached: the same image as the first call
        tip = await asyncio.wait_for(_rewrite(img, base, ap), REWRITE_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 - keep the compass tip
        logger.warning("relative entry failed for %s: %s", place.name, e)
        return base
    if not RELATIVE.match(tip):
        logger.info("relative entry rejected for %s: %r", place.name, tip)
        return base
    res = base.model_copy(update={"entry_tip": tip.rstrip("."), "relative": True})
    _rel[k] = res
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
