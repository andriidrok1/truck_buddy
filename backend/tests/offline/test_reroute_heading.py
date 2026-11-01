"""QA 2026-10-31, H14: the app sends the phone's course as it comes (271.4); the reroute body wanted a whole
number 0..359, so every reroute was a 422 and the app planned from scratch instead of resuming the plan."""
import pytest

from models import Place, RerouteRequest

P = Place(id="x", name="X", category="truck_stop", lat=38.6, lng=-121.5)


@pytest.mark.parametrize("given, want", [(271.4, 271), (359.6, 0), (360, 0), (0.0, 0), (-1, None), (float("nan"), None), (None, None)])
def test_heading_is_whole_degrees_or_unknown(given, want):
    assert RerouteRequest(lat=38.58, lng=-121.54, place=P, heading=given).heading == want


def test_reroute_body_from_the_app_validates():
    body = {"lat": 38.58, "lng": -121.54, "place": P.model_dump(), "handle": None, "heading": 268.7231,
            "section_index": 0, "traveled_m": 1834, "via": []}
    assert RerouteRequest(**body).heading == 269
