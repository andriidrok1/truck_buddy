"""QA 2026-11-02 (bug 25): the card said "On your right, off El Centro Rd" and the voice said "on your left,
off W El Camino Ave". The card was phrased for the route to the place; the route to the entrance that replaced
it remembered another approach, and only the voice used it. Every route now returns the tip for itself."""
from models import Maneuver, ParkingCheck, ParkingSpot, Place, RouteInfo
from services import approach, parking, parking_store

P = Place(id="49er", name="Sacramento 49er Travel Plaza", category="truck_stop", lat=38.6000, lng=-121.5000)
E = ParkingSpot(kind="entry", x=0.5, y=0.5, lat=38.6000, lng=-121.4990)  # entrance ~90 m east of the place


def _route(north: bool, street: str) -> RouteInfo:
    # 1 km straight line ending at the place, heading north (E on the right) or south (E on the left).
    step = 0.001 if north else -0.001
    geo = [[38.6000 - step * (9 - i), -121.5000] for i in range(10)]
    r = RouteInfo(geometry=geo, distance_m=1000, duration_s=60, distance_mi=0.6, duration_min=1, summary="", source="t",
                  maneuvers=[Maneuver(action="turn", direction="left", instruction=f"Turn left onto {street}", lat=0, lng=0, index=2),
                             Maneuver(action="arrive", instruction="Arrive", lat=0, lng=0, index=9)])
    r.approach = approach.from_route(r)  # what the routing provider sets on every route
    return r


def test_card_and_voice_follow_the_latest_route():
    parking_store._reset_for_tests()
    tip = "Use the wide driveway off the frontage road"
    parking._verdict_cache[parking._key(P.lat, P.lng)] = ParkingCheck(
        verdict="likely", label="x", reason="x", source="satellite", entry_tip=tip, spots=[E])
    approach._landmarks.put(tip, "the wide driveway off the frontage road")

    first = approach.for_route(P, _route(True, "El Centro Rd"))
    assert first.entry_tip.startswith("On your right, off El Centro Rd")

    card = approach.for_route(P, _route(False, "W El Camino Ave"))  # the route to the entrance replaced it
    assert card.entry_tip.startswith("On your left, off W El Camino Ave")
    voice = approach.speech(approach.best(P), "entry")
    assert voice == f"The truck entrance is {card.entry_tip[0].lower()}{card.entry_tip[1:]}."


def test_without_a_check_the_route_still_answers_none():
    parking_store._reset_for_tests()
    q = Place(id="q", name="Nowhere", category="truck_stop", lat=39.0, lng=-120.0)
    assert approach.for_route(q, _route(True, "Main St")) is None
