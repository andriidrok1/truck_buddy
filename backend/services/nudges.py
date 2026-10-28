"""Proactive lines the copilot says on its own while driving: a break that is due with a stop ahead, and
parking before the shift runs out. The app asks POST /api/nudges every few minutes with the points of the
route ahead; a nudge comes back as text plus an offer the driver accepts with a plain "yes" on the next turn.

Each kind fires once per cycle (a break resets it, a new night stop resets the other) and only when
there is something concrete to offer. NUDGE_BREAK / NUDGE_PARKING (default on) switch them off.
"""
import logging
import time
from typing import Optional

from models import Nudge, NudgeRequest, Place
from services import hos
from services.flags import on
from services.geo import haversine_km, km_to_mi

logger = logging.getLogger("truckbuddy.nudges")

BREAK_DUE_MIN = 60  # say something when the break is due within this
BREAK_RESET_MIN = 120  # a due time this far out again means the break happened: allow the next nudge
BREAK_LOOKAHEAD_MI = (20.0, 50.0)
PARKING_SHIFT_MIN = 90
PARKING_HOUR = 17  # without Motive: evening and a long day
PARKING_ELAPSED_MIN = 8 * 60
PARKING_LOOKAHEAD_MI = (20.0, 60.0)
HOS_CACHE_S = 60
BREAK = "Your break is due in {due} minutes. There's a {name} in {mi} miles, want me to add it?"
BREAK_NOW = "Your break is due now. There's a {name} in {mi} miles, want me to add it?"
PARKING = "About {shift} minutes on the shift. The {name} in {mi} miles{live}, should I route you there?"
PARKING_LATE = "It's getting late. The {name} in {mi} miles{live}, should I route you there?"
LIVE = " has {n} spots open right now"


def break_due_min(h: Optional[hos.Hos], elapsed_min: Optional[int], break_hours: Optional[int]) -> Optional[int]:
    """Minutes until the next break is due: Motive's break clock, else the drive clock, else the profile's
    reminder interval against the minutes driven since the route started."""
    if h is not None:
        if h.break_min is not None:
            return max(0, h.break_min)
        return max(0, h.drive_min)
    if elapsed_min is not None and break_hours:
        period = break_hours * 60
        return period - (elapsed_min % period)
    return None


def point_ahead(ahead: list[list[float]], target_mi: float) -> Optional[tuple[float, float, float]]:
    """(lat, lng, miles_ahead) of the first route point at least `target_mi` ahead, else the farthest one."""
    pts = [(float(p[0]), float(p[1]), float(p[2])) for p in ahead if len(p) >= 3]
    if not pts:
        return None
    for p in pts:
        if p[2] >= target_mi:
            return p
    return pts[-1]


def _mi(a: tuple[float, float], b: tuple[float, float]) -> float:
    return km_to_mi(haversine_km(a[0], a[1], b[0], b[1]))


async def place_ahead(req: NudgeRequest, categories: tuple[str, ...], window: tuple[float, float],
                      prefer_live: bool = False, sess_facts: Optional[dict] = None) -> Optional[tuple[Place, float]]:
    """A place of one of `categories` roughly `window` miles ahead along the route, with its miles ahead."""
    from services import agent  # lazy: the agent imports nothing from here
    lo, hi = window
    anchor = point_ahead(req.ahead, (lo + hi) / 2)
    if anchor is None:
        return None
    pts = [(float(a[0]), float(a[1]), float(a[2])) for a in req.ahead if len(a) >= 3]
    found: list[tuple[Place, float]] = []
    for category in categories:
        try:
            res = await agent.get_places_provider().search(category, anchor[0], anchor[1])
        except Exception as e:  # noqa: BLE001 - a nudge is never worth an error
            logger.info("nudge place search failed: %s", e)
            continue
        for p in res.places:
            # Miles ahead along the route: the closest route point's offset plus the hop to the place.
            miles = min(pt[2] + _mi((pt[0], pt[1]), (p.lat, p.lng)) for pt in pts)
            if lo <= miles <= hi and (not req.active_place or p.id != req.active_place.id):
                found.append((p, miles))
    if not found:
        return None
    if prefer_live:
        live = [f for f in found if f[0].parking_available]
        if live:
            found = live
    chosen = agent.choose_place([p for p, _ in found], req.truck_profile, set(), sess_facts)
    if chosen is None:
        return None
    miles = next(m for p, m in found if p.id == chosen.id)
    return chosen, round(miles)


async def _hos(sess: dict, key: Optional[str]) -> Optional[hos.Hos]:
    if not key or not hos.enabled():
        return None
    cached = sess.get("hos_cache")
    if cached and time.monotonic() - cached[0] < HOS_CACHE_S:
        return cached[1]
    try:
        h = await hos.available_time(key)
    except Exception as e:  # noqa: BLE001
        logger.info("nudge hos lookup failed: %s", type(e).__name__)
        return None
    sess["hos_cache"] = (time.monotonic(), h)
    return h


def _offer(sess: dict, intent: str, place: Place) -> None:
    """A plain "yes" on the next copilot turn acts on this."""
    sess["pending"] = {"d": {"intent": intent, "place": place.model_dump()}, "text": f"{intent} {place.name}"}


async def compute(req: NudgeRequest, sess: dict, motive_key: Optional[str]) -> list[Nudge]:
    if not req.driving:
        return []
    out: list[Nudge] = []
    h = await _hos(sess, motive_key)

    if on("NUDGE_BREAK"):
        due = break_due_min(h, req.drive_elapsed_min, req.truck_profile.break_hours)
        if due is not None and due > BREAK_RESET_MIN:
            sess.pop("nudge_break_done", None)
        if due is not None and due <= BREAK_DUE_MIN and not sess.get("nudge_break_done"):
            hit = await place_ahead(req, ("truck_stop", "rest_area"), BREAK_LOOKAHEAD_MI, sess_facts=sess.get("facts"))
            if hit:
                place, mi = hit
                name = place.name.split(",")[0]
                text = BREAK_NOW.format(name=name, mi=mi) if due <= 5 else BREAK.format(due=due, name=name, mi=mi)
                sess["nudge_break_done"] = True
                _offer(sess, "add_stop", place)
                out.append(Nudge(kind="break", text=text, place=place, offer="add_stop"))

    if on("NUDGE_PARKING") and not sess.get("nudge_parking_done"):
        by_hos = h is not None and h.shift_min <= PARKING_SHIFT_MIN
        by_clock = (h is None and req.local_hour is not None and req.local_hour >= PARKING_HOUR
                    and (req.drive_elapsed_min or 0) >= PARKING_ELAPSED_MIN)
        if by_hos or by_clock:
            hit = await place_ahead(req, ("truck_stop",), PARKING_LOOKAHEAD_MI, prefer_live=True,
                                    sess_facts=sess.get("facts"))
            if hit:
                place, mi = hit
                name = place.name.split(",")[0]
                live = LIVE.format(n=place.parking_available) if place.parking_available else ""
                text = (PARKING.format(shift=h.shift_min, name=name, mi=mi, live=live) if by_hos
                        else PARKING_LATE.format(name=name, mi=mi, live=live))
                sess["nudge_parking_done"] = True
                _offer(sess, "go", place)
                out.append(Nudge(kind="parking", text=text, place=place, offer="go"))
    return out
