"""Review 2026-11-02, bug 14: "where can I park near my delivery in Stockton" over a route that went under a
low bridge said only "Pilot Travel Center, 1 miles from your delivery...": _find_near replaced the whole route
answer and the restriction, closure, weather and chain lines went with it. Every answer that builds a route
ends with the same warnings (agent._with_warnings)."""
import copy

from conftest import fixture_json, run

from models import AgentRequest, Place, TruckProfile
from services import breakdown
from services.dialog_state import DialogState

DATA = fixture_json("here/route_sac_reno")
SAC = {"lat": 38.5816, "lng": -121.4944}
TALL = TruckProfile(height_in=169, trailer_ft=53, weight_lb=80000)
RENO = Place(id="here-reno", name="Reno, NV", category="destination", lat=39.5296, lng=-119.8138)
STOCKTON = Place(id="here-town-stockton", name="Stockton, CA", category="destination", lat=37.95, lng=-121.29)
PILOT = Place(id="g-pilot", name="Pilot Travel Center", category="truck_stop", lat=37.96, lng=-121.30, distance_mi=1.0,
              address="1 A St, Stockton, CA")
LOVES = Place(id="g-loves", name="Love's Travel Stop", category="truck_stop", lat=38.9, lng=-121.08, distance_mi=30.0,
              address="Auburn, CA")
VIOLATED_HEIGHT = {"title": "Violated vehicle restriction.", "code": "violatedVehicleRestriction", "severity": "critical",
                   "details": [{"type": "restriction", "cause": "height limit of 422 cm", "maxHeight": 422}]}
LOW_BRIDGE = "This route goes under a 13 ft 10 in clearance; your truck is 14 ft 1 in."


def _restricted():
    d = copy.deepcopy(DATA)
    d["routes"][0]["sections"][0]["notices"] = [VIOLATED_HEIGHT]
    return d


def _req(text, sid, active=None):
    return AgentRequest(text=text, session_id=sid, active_place=active, truck_profile=TALL, **SAC)


def test_park_near_a_place_keeps_the_restriction(agent_sandbox, fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", json=_restricted())
    agent_sandbox.places_provider.places = [PILOT]
    agent_sandbox.geocode["stockton"] = [STOCKTON]
    agent_sandbox.llm.on(r"park near", {"intent": "find", "category": "truck_stop", "near": "Stockton, CA"})
    agent_sandbox.llm.on(r"parking close", {"intent": "parking", "near": "Stockton, CA"})
    for text, sid in (("where can I park near my delivery in Stockton", "w1"),
                      ("any parking close to my delivery in Stockton", "w2")):
        res = run(agent_sandbox.provider().handle(_req(text, sid), None))
        assert res.status == "route_set" and res.route.restricted == "height"
        assert res.reply.startswith("Pilot Travel Center, right at your delivery,") or \
            res.reply.startswith("Pilot Travel Center, 1 miles from your delivery,"), res.reply
        assert LOW_BRIDGE in res.reply and "Heads up, I couldn't find a legal truck route" in res.reply


def test_a_stop_on_the_way_says_a_restriction_on_the_new_route(agent_sandbox, fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA, times=1)
    fake_http.add("router.hereapi.com", "/v8/routes", json=_restricted())
    agent_sandbox.geocode["reno"] = [RENO]
    agent_sandbox.places_provider.places = [LOVES]
    agent_sandbox.llm.on(r"reno", {"intent": "go", "query": "Reno, NV"})
    agent_sandbox.llm.on(r"fuel stop", {"intent": "add_stop", "category": "truck_stop"})
    agent_sandbox.llm.on(r"no stops", {"intent": "clear_stops"})
    first = run(agent_sandbox.provider().handle(_req("take me to Reno", "w3"), None))
    assert "Heads up" not in first.reply
    res = run(agent_sandbox.provider().handle(_req("add a fuel stop", "w3", RENO), None))
    assert res.reply.startswith("Added Love's Travel Stop") and LOW_BRIDGE in res.reply
    res = run(agent_sandbox.provider().handle(_req("no stops", "w3", RENO), None))
    assert res.reply.startswith("Stops cleared.") and LOW_BRIDGE in res.reply


def test_a_route_to_a_repair_shop_says_it_too(fake_http):
    from test_breakdown_followup import RANKED
    fake_http.add("router.hereapi.com", "/v8/routes", json=_restricted())
    sess = DialogState(providers=[breakdown.to_provider(p) for p in RANKED])
    res = run(breakdown.followup(_req("route me to the closest", "w4"), sess, "route me to the closest", "t"))
    assert res.reply.startswith("Okay, route to Delta Road Service is set,") and LOW_BRIDGE in res.reply
