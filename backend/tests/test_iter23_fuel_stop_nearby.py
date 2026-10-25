# Iteration 23: gas stations tagged truck_stop are demoted / called "a fuel stop"; nearby truck lot in the frame
from unittest.mock import patch

from models import NearbyLot, ParkingCheck, Place, RouteInfo, TruckProfile
from services import agent, approach
from services.parking import _parse


def _p(pid, name, mi, cat="truck_stop"):
    return Place(id=pid, name=name, category=cat, lat=38.5, lng=-121.5, distance_mi=mi, distance_km=mi * 1.6)


def _verdicts(d):
    return patch.object(agent, "_verdict", lambda p: d.get(p.id))


def test_gas_station_with_unlikely_is_demoted():
    found = [_p("a", "AAA truck Wash Scale", 1.0), _p("b", "Pilot Travel Center", 4.0)]
    with _verdicts({"a": "unlikely"}):
        assert agent.choose_place(found, TruckProfile(), set()).id == "b"


def test_truck_stop_name_preferred_when_verdicts_unknown():
    found = [_p("a", "Chevron", 1.0), _p("b", "Love's Travel Stop", 3.0)]
    with _verdicts({}):
        assert agent.choose_place(found, TruckProfile(), set()).id == "b"


def test_only_fuel_stops_left_still_routes_and_says_fuel_stop():
    found = [_p("a", "Shell", 1.0)]
    route = RouteInfo(geometry=[[0, 0], [1, 1]], distance_m=1609, duration_s=180, distance_mi=1.0, duration_min=3,
                      summary="3 min · 1 mi", source="mock")
    with _verdicts({"a": "unlikely"}):
        place = agent.choose_place(found, TruckProfile(), set())
        assert place.id == "a"
        reply = agent._route_reply(place, route, 0)
    assert "fuel stop" in reply.lower() and "truck stop" not in reply.lower().replace("not a truck stop", "")


def test_chain_name_is_never_fuel_stop():
    with _verdicts({"a": "unlikely"}):
        assert not agent.is_fuel_stop(_p("a", "Flying J Travel Plaza", 1.0))
        assert not agent.is_fuel_stop(_p("a", "TA Sacramento", 1.0))


def test_vision_nearby_only_with_unlikely():
    place = _p("a", "x", 1.0)
    raw = ('{"fits": "unlikely", "reason": "Small car lot", "entry": "", "park": "", "spots": [], '
           '"nearby": {"direction": "south-east", "kind": "private yard"}}')
    v = _parse(raw, place)
    assert v["nearby"] == NearbyLot(direction="southeast", kind="private yard")
    assert _parse(raw.replace('"unlikely"', '"likely"'), place)["nearby"] is None
    assert _parse(raw.replace('"south-east"', '"up"'), place)["nearby"] is None


def test_nearby_spoken():
    c = ParkingCheck(verdict="unlikely", label="", reason="", source="satellite",
                     nearby=NearbyLot(direction="southeast", kind="private yard"))
    assert "There's a lot with trailers to the southeast, looks like a private yard." in approach.speech(c)
