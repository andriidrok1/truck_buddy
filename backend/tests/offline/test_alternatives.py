"""Alternative truck routes (HERE alternatives=N, routeLabels, tolls) and "another way?" in the dialog."""
import copy

from conftest import fixture_json, params_of, run

from models import AgentRequest, Place, TruckProfile
from services import agent, routing
from services.routing import _here_parse, _labels, _tolls_usd

DATA = fixture_json("here/route_sac_reno")
SAC = {"lat": 38.5816, "lng": -121.4944}
RENO = Place(id="here-reno", name="Reno, NV", category="destination", lat=39.5296, lng=-119.8138,
             address="Reno, NV, United States")


def test_fixture_has_two_alternatives_with_labels():
    info = _here_parse(DATA)
    assert info.labels == ["I-80"]
    assert len(info.alternatives) == 2
    a, b = info.alternatives
    assert a.labels == ["I-80", "CA-89"] and b.labels == ["I-80", "I-580"]
    assert a.alternatives == [] and b.alternatives == []  # no nesting
    assert a.maneuvers and a.speed_limits and 2 <= len(a.geometry) <= routing.MAX_POINTS
    assert a.duration_min > info.duration_min and b.duration_min > info.duration_min  # HERE puts the best first
    assert info.tolls_usd is None  # no tolls on I-80 to Reno


def test_labels_and_tolls_parsers_are_tolerant():
    labels = [{"name": {"value": "I-5"}}, {"name": {"value": "I-5"}}, {"bad": 1}, None]
    assert _labels({"routeLabels": labels}) == ["I-5"]
    assert _labels({}) == [] and _labels({"routeLabels": "x"}) == []
    sections = [{"tolls": [{"fares": [{"price": {"currency": "USD", "value": 12.5}},
                                      {"price": {"currency": "USD", "value": 9.0}}]}]},
                {"tolls": [{"fares": [{"price": {"currency": "CAD", "value": 40}}]}]},
                {"tolls": [{"fares": [{"price": {"currency": "USD", "value": 3}}]}, {"fares": []}]}]
    assert _tolls_usd(sections) == 12.0  # cheapest fare per toll: 9 + 3; CAD ignored
    assert _tolls_usd([{"tolls": [{"fares": [{"price": {"currency": "CAD", "value": 40}}]}]}]) is None
    assert _tolls_usd([{}]) is None and _tolls_usd([{"tolls": None}]) is None


def test_a_broken_alternative_is_dropped_not_fatal():
    data = copy.deepcopy(DATA)
    data["routes"][1]["sections"] = []
    info = _here_parse(data)
    assert len(info.alternatives) == 1 and info.alternatives[0].labels == ["I-80", "I-580"]


def test_request_asks_for_alternatives_labels_tolls(fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    run(routing.get_routing_provider().route(38.58, -121.49, 39.53, -119.81, TruckProfile()))
    p = dict(params_of(fake_http.calls[0]))
    assert p["alternatives"] == "2" and p["currency"] == "USD"
    assert {"routeLabels", "tolls"} <= set(p["return"].split(","))


def test_flag_off_requests_nothing_extra(monkeypatch, fake_http):
    monkeypatch.setenv("ROUTE_ALTERNATIVES", "0")
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    info = run(routing.get_routing_provider().route(38.58, -121.49, 39.53, -119.81, TruckProfile()))
    p = dict(params_of(fake_http.calls[0]))
    assert "alternatives" not in p and "currency" not in p and "routeLabels" not in p["return"]
    assert len(info.alternatives) == 2  # whatever HERE sends is still parsed


def test_explicit_count_overrides_the_flag(fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    run(routing.get_routing_provider().route(38.58, -121.49, 39.53, -119.81, TruckProfile(), alternatives=0))
    assert "alternatives" not in dict(params_of(fake_http.calls[0]))
    run(routing.get_routing_provider().route(38.58, -121.49, 39.53, -119.81, TruckProfile(), alternatives=9))
    assert dict(params_of(fake_http.calls[1]))["alternatives"] == "6"  # HERE's maximum


def test_alt_reply_wording():
    info = _here_parse(DATA)
    assert agent._alt_reply(info.alternatives[0]) == "Here's another way via I-80 and CA-89, 4 h 38 min, 204 miles."
    tolled = info.alternatives[1].model_copy(update={"tolls_usd": 14.25, "labels": []})
    assert agent._alt_reply(tolled) == "Here's another way, 4 h 56 min, 224 miles, about $14 in tolls."


def _req(text, sid="s1", active=None):
    return AgentRequest(text=text, session_id=sid, active_place=active, **SAC)


def test_dialog_walks_through_the_alternatives(agent_sandbox, fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    agent_sandbox.llm.on(r"take me to reno", {"intent": "go", "query": "Reno, NV"})
    agent_sandbox.llm.on(r"another way", {"intent": "alternative"})
    agent_sandbox.geocode["reno"] = [RENO]
    prov = agent_sandbox.provider()

    first = run(prov.handle(_req("take me to Reno"), None))
    assert first.status == "route_set" and first.route.labels == ["I-80"] and len(first.route.alternatives) == 2

    second = run(prov.handle(_req("another way?", active=RENO), None))
    assert second.status == "route_set" and second.place.id == RENO.id
    assert second.reply == "Here's another way via I-80 and CA-89, 4 h 38 min, 204 miles."
    assert second.route.labels == ["I-80", "CA-89"] and second.route.alternatives == [] and second.route.maneuvers

    third = run(prov.handle(_req("is there another way?", active=RENO), None))
    assert third.status == "route_set" and third.route.labels == ["I-80", "I-580"]

    fourth = run(prov.handle(_req("another way", active=RENO), None))
    assert fourth.status == "chat" and fourth.reply == "That's the only truck route I've got."
    assert len(fake_http.calls_to("router.hereapi.com")) == 1  # all from the one HERE call


def test_another_way_without_a_route(agent_sandbox):
    agent_sandbox.llm.on(r"another way", {"intent": "alternative"})
    res = run(agent_sandbox.provider().handle(_req("another way"), None))
    assert res.status == "chat" and res.reply == "Set a route first and I'll look for other ways."


def test_another_way_fetches_fresh_when_the_dialog_has_none(agent_sandbox, fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    agent_sandbox.llm.on(r"another way", {"intent": "alternative"})
    res = run(agent_sandbox.provider().handle(_req("another way", sid="new", active=RENO), None))
    assert res.status == "route_set" and res.route.labels == ["I-80", "CA-89"]
    p = dict(params_of(fake_http.calls[0]))
    assert p["alternatives"] == "2" and p["origin"] == f"{SAC['lat']},{SAC['lng']}"


def test_here_down_on_fresh_fetch_is_the_usual_routing_error(agent_sandbox, fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", status=503, text="down")
    agent_sandbox.llm.on(r"another way", {"intent": "alternative"})
    res = run(agent_sandbox.provider().handle(_req("another way", sid="dead", active=RENO), None))
    assert res.status == "error" and res.reply == agent.ROUTE_DOWN


def test_context_counts_unoffered_alternatives(agent_sandbox, fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    agent_sandbox.llm.on(r"take me to reno", {"intent": "go", "query": "Reno, NV"})
    agent_sandbox.geocode["reno"] = [RENO]
    prov = agent_sandbox.provider()
    run(prov.handle(_req("take me to Reno", sid="ctx"), None))
    ctx = prov._context(_req("hi", sid="ctx", active=RENO), agent._SESSIONS["ctx"])
    assert "2 alternative routes not yet offered." in ctx


def test_route_endpoint_carries_alternatives(api_client, fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    r = api_client.post("/api/route", json={**SAC, "place": RENO.model_dump(), "skip_parking": True})
    assert r.status_code == 200, r.text
    route = r.json()["route"]
    assert route["labels"] == ["I-80"] and route["tolls_usd"] is None
    assert [a["labels"] for a in route["alternatives"]] == [["I-80", "CA-89"], ["I-80", "I-580"]]
    assert all(a["maneuvers"] and a["geometry"] for a in route["alternatives"])
