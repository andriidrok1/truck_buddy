"""A route that breaks the truck's restrictions: HERE found no compliant route and returned one anyway, with a
critical violatedVehicleRestriction notice. The driver must hear the limit and their own number, in full, before
anything else; the state's legal maximum (admin-wide) says a permit is needed; request-level notices (a re-route
started against the course) are never a word to the driver. Notice shapes are copied from live HERE replies."""
import copy

from conftest import fixture_json, params_of, run

from models import Place, TruckProfile
from services import agent, routing
from services.routing import _here_parse, _notices

DATA = fixture_json("here/route_sac_reno")
BY_HANDLE = fixture_json("here/reroute_ok")
PILOT = Place(id="p1", name="Pilot Travel Center", category="truck_stop", lat=38.6, lng=-121.5, address="1 Main St")

VIOLATED_HEIGHT = {"title": "Violated vehicle restriction.", "code": "violatedVehicleRestriction", "severity": "critical",
                   "details": [{"type": "restriction", "cause": "Route violates vehicle restriction: height limit of 422 cm",
                                "maxHeight": 422}]}
VIOLATED_WEIGHT = {"title": "Violated vehicle restriction.", "code": "violatedVehicleRestriction", "severity": "critical",
                   "details": [{"type": "restriction", "cause": "Route violates vehicle restriction: weight limit of 20000 kg",
                                "maxWeight": 20000}]}
ADMIN_WIDE = {
    "title": "Violated admin-wide vehicle restriction.", "code": "violatedAdminWideVehicleRestriction", "severity": "info",
    "details": [{"type": "adminWideRestriction", "country": "USA", "state": "CA", "maxHeight": 428,
                 "cause": "Route violates admin-wide vehicle restriction: height limit of 428 cm"}]}
START_DIRECTION = {"title": "Violated start direction.", "code": "violatedStartDirection", "severity": "info"}
TOLL = {"title": "Violated avoid toll road preference.", "code": "violatedAvoidTollRoad", "severity": "info"}
BLOCKED = {"title": "Violated blocked road.", "code": "violatedBlockedRoad", "severity": "critical"}

TALL = TruckProfile(height_in=169, trailer_ft=53, weight_lb=80000)  # 14 ft 1 in


def _with_notices(*notices):
    data = copy.deepcopy(DATA)
    data["routes"][0]["sections"][0]["notices"] = list(notices)
    return data


def test_violated_height_names_the_limit_and_the_truck():
    info = _here_parse(_with_notices(VIOLATED_HEIGHT), TALL)
    assert info.restricted == "height"
    assert info.notices == ["This route goes under a 13 ft 10 in clearance; your truck is 14 ft 1 in."]
    assert info.truck_aware


def test_truck_height_is_said_from_inches_not_a_rounded_float():
    from services.routing import _inches_text, _ft_in
    assert _inches_text(127) == "10 ft 7 in" and _inches_text(162) == "13 ft 6 in" and _inches_text(168) == "14 ft"
    assert _ft_in(127 * 2.54) == "10 ft 7 in"  # 322.58 cm: a float error must not lose an inch
    assert _ft_in(422) == "13 ft 10 in"


def test_a_notice_with_two_details_names_both():
    both = copy.deepcopy(VIOLATED_HEIGHT)
    both["details"].append({"type": "restriction", "cause": "weight", "maxWeight": 20000})
    info = _here_parse(_with_notices(both), TALL)
    assert info.restricted == "height" and len(info.notices) == 2 and "44,092 lb" in info.notices[1]


def test_a_restricted_route_still_speaks_its_closure():
    from models import Incident
    info = _here_parse(_with_notices(VIOLATED_HEIGHT), TALL)
    info.incidents = [Incident(type="roadClosure", closed=True, description="I-80 closed at Donner Summit", index=1)]
    out = agent._route_reply(PILOT, info, 0)
    assert "goes under a 13 ft 10 in clearance" in out and out.endswith(
        "Heads up, there's a road closure on this route: I-80 closed at Donner Summit.")


def test_violated_weight_and_blocked_road():
    info = _here_parse(_with_notices(VIOLATED_WEIGHT), TruckProfile(weight_lb=80000))
    assert info.restricted == "weight"
    assert info.notices == ["This route crosses a 44,092 lb weight limit; your truck is 80,000 lb."]
    info = _here_parse(_with_notices(BLOCKED), TruckProfile())
    assert info.restricted == "blocked" and info.notices == ["Part of this route is on a road closed to trucks."]


ADMIN_WIDE_413 = copy.deepcopy(ADMIN_WIDE)
ADMIN_WIDE_413["details"][0]["maxHeight"] = 413
ADMIN_WIDE_NV = copy.deepcopy(ADMIN_WIDE)
ADMIN_WIDE_NV["details"][0]["state"] = "NV"


def test_admin_wide_limit_is_a_permit_not_a_bridge():
    info = _here_parse(_with_notices(ADMIN_WIDE), TALL)
    assert info.restricted == "legal"
    assert info.notices == ["Your truck is over the legal height limit in CA (14 ft); that takes a permit."]
    # Live, HERE sends one notice per state and per limit (13 ft 6 in general, 14 ft on designated routes):
    # one line, the states listed, the highest limit.
    info = _here_parse(_with_notices(ADMIN_WIDE_413, ADMIN_WIDE, ADMIN_WIDE_NV), TALL)
    assert info.notices == ["Your truck is over the legal height limit in CA and NV (14 ft); that takes a permit."]
    # A bridge and the permit at once (downtown Sacramento, live): the bridge first, the permit line stays.
    info = _here_parse(_with_notices(ADMIN_WIDE_413, ADMIN_WIDE, VIOLATED_HEIGHT), TALL)
    assert info.restricted == "height"
    assert info.notices == ["This route goes under a 13 ft 10 in clearance; your truck is 14 ft 1 in.",
                            "Your truck is over the legal height limit in CA (14 ft); that takes a permit."]


def test_request_level_notices_are_dropped():
    lines, restricted = _notices([START_DIRECTION, {"code": "simplePolyline"}, {"code": ""}, {}], TruckProfile())
    assert lines == [] and restricted is None
    # The live re-route fixture carries violatedStartDirection: before, every re-route said "may be restricted".
    info = _here_parse(BY_HANDLE, TruckProfile())
    assert info.notices == [] and info.restricted is None


def test_preferences_that_could_not_be_honoured_keep_their_lines():
    lines, restricted = _notices([TOLL, {"code": "violatedAvoidFerry"}, {"code": "somethingNew", "severity": "critical"},
                                  {"code": "somethingInformational", "severity": "info"}], TruckProfile())
    assert lines == ["Tolls couldn't be fully avoided on this route.", "Route includes a ferry.",
                     "Check the route for restrictions."]
    assert restricted is None


def test_the_spoken_warning_puts_the_restriction_first_and_in_full():
    info = _here_parse(_with_notices(VIOLATED_HEIGHT), TALL)
    out = agent._route_reply(PILOT, info, 0)
    assert out.endswith("Heads up, I couldn't find a legal truck route to there. This route goes under a 13 ft 10 in "
                        "clearance; your truck is 14 ft 1 in. Don't take this one blind: check the signs, or give me "
                        "another destination.")
    legal = _here_parse(_with_notices(ADMIN_WIDE), TALL)
    assert agent._route_reply(PILOT, legal, 0).endswith(
        "Heads up: Your truck is over the legal height limit in CA (14 ft); that takes a permit.")
    # A compliant route with a closure still speaks the closure (unchanged).
    plain = _here_parse(DATA, TALL)
    assert plain.restricted is None and "Heads up" not in agent._route_reply(PILOT, plain, 0)


def test_vehicle_sizes_are_rounded_up_and_the_rig_is_a_tractor_trailer(fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    run(routing.get_routing_provider().route(38.58, -121.49, 39.53, -119.81, TALL))
    p = dict(params_of(fake_http.calls[0]))
    assert p["vehicle[height]"] == "430"  # 169 in = 429.26 cm
    assert p["vehicle[type]"] == "tractor" and p["vehicle[trailerCount]"] == "1"


def test_ors_rounds_up_too(fake_http, monkeypatch):
    import json
    monkeypatch.setenv("ORS_API_KEY", "x")
    fake_http.add("api.openrouteservice.org", "/v2/directions/driving-hgv/geojson",
                  json={"features": [{"geometry": {"coordinates": [[-121.4, 38.5], [-119.8, 39.5]]},
                                      "properties": {"summary": {"distance": 1000.0, "duration": 100.0}}}]})
    run(routing._ors(38.5, -121.4, 39.5, -119.8, TALL))
    body = json.loads(fake_http.calls[0].content)
    restr = body["options"]["profile_params"]["restrictions"]
    assert restr["height"] == 4.3 and restr["weight"] == 36.3 and restr["length"] == 22.26  # m, t, m


def test_avoid_tunnel_violation_is_named():
    lines, restricted = _notices([{"code": "violatedAvoidTunnel", "severity": "info"}], TruckProfile())
    assert lines == ["A tunnel couldn't be avoided on this route."] and restricted is None


def test_a_restriction_without_a_limit_is_said_in_plain_words():
    # QA 2026-11-02: the driver heard HERE's own "Violated access restriction for this type of vehicle".
    lines, restricted = _notices([{"code": "violatedVehicleRestriction", "severity": "critical",
                                   "details": [{"type": "restriction", "cause": "Violated access restriction for this type of vehicle"}]}],
                                 TruckProfile())
    assert restricted == "other"
    assert lines == ["Part of this route is on a road with a truck restriction."]
