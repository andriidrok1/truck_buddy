"""What the turn handler and its parts share: the dialog state of this turn, route keys, the error replies, the
place categories, chain names and the reply length at speed."""
import logging
import math
import os
from typing import Optional

from models import AgentRequest, AgentResponse, Place, RouteInfo, TruckProfile
from services import timing
from services.google_places import TRUCK_STOP_NAME
from services import session_store
from services.dialog_state import DialogState
from services.errors import NoTruckRoute
from services.flags import on
from services.speech_text import fit_reply
from services.agent.replies import NOT_UNDERSTOOD, ROUTE_DOWN, NO_TRUCK_ROUTE, typed_miss

logger = logging.getLogger("truckbuddy.agent")


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


def _route_key(r: RouteInfo) -> tuple:
    """What tells two routes apart once they have been through JSON (object identity does not survive a save)."""
    return (r.handle or "", r.distance_m, r.duration_s, tuple(r.labels or ()))


def _profile_key(t: TruckProfile) -> tuple:
    """What changes the truck route: alternatives planned for another truck are not offered."""
    return (t.height_in, t.trailer_ft, t.weight_lb, t.hazmat, t.avoid_tolls, t.avoid_ferries_tunnels)


CATEGORY_WORD = {"truck_stop": "truck stop", "hotel": "hotel", "rest_area": "rest area", "walmart": "Walmart",
                 "cat_scale": "CAT scale", "truck_wash": "truck wash", "weigh_station": "weigh station"}
CORE_CATEGORIES = ("truck_stop", "hotel", "rest_area")
EXTRA_CATEGORIES = ("walmart", "cat_scale", "truck_wash", "weigh_station")
# Satellite parking checks only where a truck would park; a scale or a wash is not a place to spend the night.
PARKING_CATEGORIES = {"truck_stop", "hotel", "rest_area", "walmart", "destination"}


def enabled_categories() -> tuple[str, ...]:
    """Core categories plus whatever CATEGORIES_EXTRA lists (comma-separated, e.g. 'walmart,cat_scale').
    Off by default until the app knows how to draw them."""
    extra = {c.strip() for c in os.environ.get("CATEGORIES_EXTRA", "").split(",") if c.strip()}
    return CORE_CATEGORIES + tuple(c for c in EXTRA_CATEGORIES if c in extra or "all" in extra)


ALTERNATIVES_FRESH = 2  # when "another way?" comes with no cached alternatives: ask HERE for this many
ALT_ORIGIN_KM = 8.0  # cached alternatives were planned from here; further on, "another way" plans from the truck
PASSED_KM = 1.6  # a stop this close to the truck is being approached, never counted as passed
REACHED_KM = 0.15  # ...unless the truck is at it (parked in its lot): then it is made, and the next plan leaves it out

CATEGORIES = CORE_CATEGORIES + EXTRA_CATEGORIES  # all the classifier may name; enabled_categories() gates


MAX_HISTORY = 8

_SESSIONS = session_store.memory_sessions()  # the memory store's dialogs (tests look them up here)


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
    if typed_miss(req.input, res.reply):
        return  # typed: the second sentence ("Try another name or a nearby town.") is the useful part
    res.reply = fit_reply(res.reply, FAST_SENTENCES if at_speed(req) else SLOW_SENTENCES)


def _session(sid: Optional[str]) -> DialogState:
    """The dialog's state: the one the current turn loaded (services/session_store), else the in-process one."""
    return session_store.current_or_memory(sid)


def _session_if(sid: Optional[str]) -> Optional[DialogState]:
    """The dialog's state when this turn has one (or the memory store knows the id); None otherwise."""
    if not sid:
        return None
    got = session_store.current(sid)
    if got is not None:
        return got
    return _SESSIONS.get(sid)


REJECTIONS_TO_LEARN = 3  # "not that one" on the same chain this many times in a dialog -> avoided


def _facts(req: AgentRequest) -> Optional[dict]:
    """The learned facts of this dialog, from the state the turn loaded: with the database store the in-process
    dialogs are empty, and a learned "avoid Pilot" was ignored on every worker (review 2026-11-02, item 16)."""
    sess = _session_if(req.session_id)
    return sess.get("facts") if sess is not None else None


STOP_DETOUR_MIN_S = 45 * 60  # a stop costing more than this beyond its own 30 minutes...
STOP_DETOUR_FRACTION = 0.5  # ...and more than half the trip so far is asked about first


def _id(place: Optional[Place]) -> Optional[str]:
    return place.id if place else None


def _not_understood(text: str, source: str) -> AgentResponse:
    return AgentResponse(status="not_understood", transcript=text, reply=NOT_UNDERSTOOD,
                         message=NOT_UNDERSTOOD, source=source)


def _chain_of(name: str) -> Optional[str]:
    """The truck stop chain in a place name, spelled the usual way ("Love's", "Pilot", "TA"), or None."""
    m = TRUCK_STOP_NAME.search(name or "")
    if not m:
        return None
    raw = m.group(0).lower()
    return CHAIN_SPELLING.get(raw, raw.title())


# Chains a driver asks for by name ("find a Love's"); "truck stop" and "travel center" match TRUCK_STOP_NAME too
# but name no chain.
NAMED_CHAINS = {"Love's", "Pilot", "Flying J", "TA", "Petro", "Sapp Bros", "Roady's"}


def chain_named(text: str) -> Optional[str]:
    """The truck stop chain the driver named ("find a Love's" -> "Love's"), or None."""
    c = _chain_of(text)
    return c if c in NAMED_CHAINS else None


def _short_name(p: Place) -> str:
    """"Sacramento 49er Travel Plaza, Sacramento, CA" -> "Sacramento 49er Travel Plaza": how a place is remembered."""
    return p.name.split(",")[0].strip()


CHAIN_SPELLING = {"love's": "Love's", "loves": "Love's", "ta": "TA", "flying j": "Flying J", "sapp bros": "Sapp Bros",
                  "roady's": "Roady's", "roadys": "Roady's"}
