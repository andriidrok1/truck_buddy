"""Dialog contexts for the eval cases, built by the real agent (`LlmAgentProvider._context`).

Each preset returns the (AgentRequest, session dict) the agent would have at that point of a dialog;
`build_context` turns that into the context string the classifier sees. Places come from the recorded
Google fixture (tests/fixtures/google/truck_stop_sac.json), so the list the model reads is the one the
live provider would build from that response. Everything here is deterministic and offline.
"""
import json
from pathlib import Path
from typing import Callable

from models import AgentRequest, Place, TruckProfile
from services.geo import haversine_km, km_to_mi
from services.google_places import reviews_from

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "google" / "truck_stop_sac.json"
SAC = (38.5816, -121.4944)  # driver location: downtown Sacramento, like the legacy eval
ACTIVE_DISTANCE_MI = 4.7
ACTIVE_DURATION_MIN = 9
NEAR_STOCKTON = {"query": "Stockton, CA", "lat": 37.9577, "lng": -121.2908}
PAST_LINES = [  # what this driver said in earlier dialogs; the model must not act on any of it
    "take me to Reno",
    "find me a truck stop",
    "I like Love's and Pilot",
    "avoid tolls",
    "my trailer is 53 foot",
]


def _place(raw: dict) -> Place:
    """A Place from one Google row, shaped like GooglePlacesProvider._query builds it (no staleness
    filter on the diesel price, so the context never changes with the calendar)."""
    loc = raw["location"]
    km = haversine_km(*SAC, loc["latitude"], loc["longitude"])
    diesel = next((fp for fp in (raw.get("fuelOptions") or {}).get("fuelPrices", [])
                   if fp.get("type") == "TRUCK_DIESEL"), None)
    price = None
    if diesel:
        money = diesel["price"]
        price = round(int(money.get("units", 0)) + int(money.get("nanos", 0)) / 1e9, 3)
    return Place(
        id=raw["id"], name=raw["displayName"]["text"], category="truck_stop",
        lat=loc["latitude"], lng=loc["longitude"], address=raw.get("formattedAddress"),
        rating=raw.get("rating"), rating_count=raw.get("userRatingCount"),
        summary=(raw.get("editorialSummary") or {}).get("text"),
        diesel_price=price, diesel_kind="truck" if price else None,
        diesel_updated=diesel.get("updateTime") if diesel else None,
        distance_km=round(km, 2), distance_mi=round(km_to_mi(km), 1),
        reviews=reviews_from(raw.get("reviews")),
    )


def fixture_places() -> list[Place]:
    """Pilot (West Sacramento), Love's (Sacramento), Flying J (Reno), in fixture order."""
    return [_place(p) for p in json.loads(FIXTURE.read_text())["places"]]


def _session() -> dict:
    return {"history": [], "places": []}


def _request(**kw) -> AgentRequest:
    return AgentRequest(text="", lat=SAC[0], lng=SAC[1], session_id="eval", **kw)


def idle() -> tuple[AgentRequest, dict]:
    """Nothing found, no route: the first words of a dialog."""
    return _request(), _session()


def route_active() -> tuple[AgentRequest, dict]:
    """A route to the Pilot is set (the app reports 4.7 mi / 9 min), nothing else happened yet."""
    pilot = fixture_places()[0]
    return _request(active_place=pilot, active_distance_mi=ACTIVE_DISTANCE_MI,
                    active_duration_min=ACTIVE_DURATION_MIN), _session()


def options_shown() -> tuple[AgentRequest, dict]:
    """The driver asked for a truck stop, got the Pilot, and two more places are on the list."""
    req, sess = route_active()
    places = fixture_places()
    sess["places"] = places
    sess["history"] = ["Driver: find me a truck stop",
                       f"Copilot: Alright, got one. {places[0].name} on W Capitol Ave, "
                       f"{ACTIVE_DISTANCE_MI} miles, about {ACTIVE_DURATION_MIN} minutes."]
    req.ui.options_count = len(places) - 1
    return req, sess


def route_active_alts() -> tuple[AgentRequest, dict]:
    """options_shown plus the alternative routes HERE returned for the current route, exactly as the live
    dialog has them: the context then says "1 alternative route not yet offered", which pulled "not that
    one" towards `alternative` (QA 2026-10-30, H4)."""
    req, sess = options_shown()
    sess["alternatives"] = {"place": sess["places"][0].id, "routes": [object()], "i": 0}
    return req, sess


def stops() -> tuple[AgentRequest, dict]:
    """Route to the Pilot with one stop added on the way (the Love's)."""
    req, sess = route_active()
    places = fixture_places()
    sess["via"] = {"place": places[0].id, "stops": [places[1]]}
    sess["history"] = ["Driver: add a fuel stop on the way",
                       f"Copilot: Added {places[1].name} on the way to {places[0].name}."]
    return req, sess


def near_follow_up() -> tuple[AgentRequest, dict]:
    """The last find was near Stockton; "and a hotel there" must reuse that point."""
    req, sess = idle()
    sess["near"] = dict(NEAR_STOCKTON)
    sess["history"] = ["Driver: find a truck stop near my delivery in Stockton",
                       "Copilot: Alright, got one. Flying J near Stockton, 2.1 miles from your delivery."]
    return req, sess


def past_memory() -> tuple[AgentRequest, dict]:
    """A fresh dialog with the driver's lines from earlier dialogs in long-term memory."""
    req, sess = idle()
    sess["past"] = list(PAST_LINES)
    return req, sess


PRESETS: dict[str, Callable[[], tuple[AgentRequest, dict]]] = {
    "idle": idle,
    "route_active": route_active,
    "options_shown": options_shown,
    "route_active_alts": route_active_alts,
    "stops": stops,
    "near_follow_up": near_follow_up,
    "past_memory": past_memory,
}


def build_context(preset: str) -> tuple[AgentRequest, str, TruckProfile]:
    """(request, context string, truck profile) for a preset, via the real agent's context builder."""
    from services import agent
    req, sess = PRESETS[preset]()
    context = agent.LlmAgentProvider()._context(req, sess)
    return req, context, req.truck_profile
