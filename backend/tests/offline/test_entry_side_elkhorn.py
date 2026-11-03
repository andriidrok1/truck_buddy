"""QA 2026-11-02 (UX): Elkhorn Rest Area "(northbound)" got "On your left, off I-5 S". Live HERE (recorded here,
Sacramento -> 38.67148,-121.61224): there is no northbound Elkhorn, HERE knows only "Elkhorn Rest Area S" and the
point snaps to that lot, so the truck goes north past it, turns at County Road 118 and comes back on I-5 S, "Keep
right" twice into the rest area. "Off I-5 S" is right; the side was not. The street comes from the ramp onto I-5
S two km back, the heading from the last 300 m, which are the rest area's own ramp: the lot lies almost dead ahead
of it and the geometry named "left" on a few meters. The way the route leaves the street says it: "Keep right"."""
import pytest
from conftest import fixture_json

from models import Approach, ParkingCheck, ParkingSpot
from services import approach
from services.routing import _here_parse

POI = (38.67183, -121.61215)  # HERE's "Elkhorn Rest Area S"


def _check(e):
    return ParkingCheck(verdict="likely", label="", reason="", source="satellite", image_path="/s",
                        entry_tip="Truck lot past the car parking", spots=[ParkingSpot(kind="entry", x=0.5, y=0.5,
                                                                                       lat=e[0], lng=e[1])])


def test_the_side_off_the_interstate_is_the_way_the_route_leaves_it():
    ap = approach.from_route(_here_parse(fixture_json("here/route_sac_elkhorn")))
    assert ap.street == "I-5 S" and ap.exit_side == "right"
    for e in (POI, (38.67148, -121.61224), (38.6716, -121.6123)):  # anywhere in the lot
        assert approach._phrase(_check(e), ap, "").entry_tip == "On your right, off I-5 S"


def test_a_leaving_maneuver_far_back_is_not_the_entrance():
    # the Sacramento -> Reno fixture leaves its last named road well before the end: no side from a maneuver there
    ap = approach.from_route(_here_parse(fixture_json("here/route_sac_reno")))
    assert ap.exit_side is None


@pytest.mark.parametrize("lateral_m, side", [(3.0, None), (-3.0, None), (20.0, "left"), (-20.0, "right")])
def test_nearly_dead_ahead_names_no_side(lateral_m, side):
    a = Approach(bearing=0, compass="south", lat=38.0, lng=-121.0)  # heading north, E 100 m ahead
    lng = -121.0 - lateral_m / (111320 * 0.788)  # positive = west = left
    assert approach.side(a, 38.0009, lng) == side
