"""Incidents from HERE return=incidents: parsing, closure detection, the spoken heads-up, the flag."""
import copy

import pytest
from conftest import fixture_json, params_of, run

from models import Incident, Place, RouteInfo, TruckProfile
from services import agent, routing
from services.routing import _here_parse, _incident, _incidents

DATA = fixture_json("here/route_sac_reno")


def _route(**kw) -> RouteInfo:
    base = dict(geometry=[[38.5, -121.4], [38.6, -121.3]], distance_m=10000.0, duration_s=600.0, distance_mi=6.2,
                duration_min=10, summary="10 min · 6.2 mi", source="here")
    base.update(kw)
    return RouteInfo(**base)


def _place() -> Place:
    return Place(id="p1", name="Pilot Travel Center", category="truck_stop", lat=38.5, lng=-121.4,
                 address="123 Main St, Lodi, CA")


def test_fixture_incidents_parse_with_positions():
    info = _here_parse(DATA)
    assert len(info.incidents) == 2
    a, b = info.incidents
    assert a.type == "construction" and a.criticality == "major" and a.closed is False
    assert a.description == "Road construction. Two lanes closed"
    assert b.description == "Major road construction" and b.criticality == "minor"
    assert 0 < a.index < len(info.geometry) and 0 < b.index < len(info.geometry)
    assert a.index == routing._thin_index(1042, 2533)  # first span that references incident 0


def test_closure_detection():
    assert _incident(0, {"type": "roadClosure", "criticality": "major", "description": "I-80 closed"}).closed
    assert _incident(0, {"type": "accident", "criticality": "critical", "description": "x"}).closed
    assert not _incident(0, {"type": "accident", "criticality": "major", "description": "x"}).closed
    assert not _incident(0, {"type": "construction", "criticality": "minor"}).closed


def test_description_is_capped_and_cleaned():
    long = "Road construction on westbound lanes " * 5
    inc = _incident(3, {"type": "construction", "criticality": "minor", "description": long})
    assert len(inc.description) <= routing.INCIDENT_TEXT_MAX and not inc.description.endswith(" ")
    assert inc.index == 3
    assert _incident(0, {"type": "weather", "description": "  Fog.  "}).description == "Fog"
    assert _incident(0, {"type": "weather"}).description == "weather"


def test_incident_without_a_span_starts_at_the_section():
    section = {"incidents": [{"type": "roadClosure", "criticality": "critical", "description": "closed"}]}
    assert _incidents(section, [], base=120) == [(120, section["incidents"][0])]


def test_malformed_incident_lists_are_ignored():
    assert _incidents({"incidents": "nope"}, [], 0) == []
    assert _incidents({"incidents": None}, [], 0) == []
    assert _incidents({"incidents": [1, "x", None]}, [], 0) == []
    assert _incidents({}, [{"offset": 0, "incidents": [0]}], 0) == []  # span refers to a list that isn't there
    data = copy.deepcopy(DATA)
    data["routes"][0]["sections"][0]["incidents"] = {"bad": True}
    assert _here_parse(data).incidents == []


RESTRICTED = "Part of the route may be restricted for your truck. Watch posted signs."  # as _friendly() emits it


def test_route_reply_speaks_the_closure_first():
    route = _route(notices=[RESTRICTED], incidents=[
        Incident(type="construction", closed=False, description="Two lanes closed", index=1),
        Incident(type="roadClosure", closed=True, description="I-80 closed at Donner Summit", index=1),
    ])
    out = agent._route_reply(_place(), route, 0)
    assert out.endswith(" Heads up, there's a road closure on this route: I-80 closed at Donner Summit.")
    assert "tight for your rig" not in out  # the notice gives way to the closure


def test_route_reply_without_closure_keeps_the_notice_or_nothing():
    only_construction = _route(incidents=[Incident(type="construction", closed=False, description="x", index=1)])
    assert "Heads up" not in agent._route_reply(_place(), only_construction, 0)
    with_notice = _route(notices=[RESTRICTED])
    assert agent._route_reply(_place(), with_notice, 0).endswith(
        "Heads up, part of this route might be tight for your rig, so watch the signs.")


def test_exact_reply_contract_with_closure():
    closure = Incident(type="roadClosure", closed=True, description="US-50 closed near Kyburz", index=1)
    assert agent._route_reply(_place(), _route(incidents=[closure]), 0) == (
        "Alright, got one. Pilot Travel Center, right off Main St, about 6.2 miles out. "
        "Heads up, there's a road closure on this route: US-50 closed near Kyburz.")


def test_context_mentions_the_posted_limit():
    from models import AgentRequest
    req = AgentRequest(text="hi", lat=38.5, lng=-121.4, active_place=_place(), active_distance_mi=5.0,
                       active_speed_limit_mph=65)
    ctx = agent.get_agent_provider()._context(req, {"history": [], "places": []})
    assert "Posted speed limit here: 65 mph." in ctx
    req.active_speed_limit_mph = None
    assert "Posted speed limit" not in agent.get_agent_provider()._context(req, {"history": [], "places": []})


@pytest.mark.parametrize("flag,expect", [("1", True), ("0", False)])
def test_flag_controls_the_request(monkeypatch, fake_http, flag, expect):
    monkeypatch.setenv("ROUTE_INCIDENTS", flag)
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    run(routing.get_routing_provider().route(38.58, -121.49, 39.53, -119.81, TruckProfile()))
    p = dict(params_of(fake_http.calls[0]))
    assert ("incidents" in p["return"].split(",")) is expect
    assert ("incidents" in p["spans"].split(",")) is expect


def test_route_endpoint_returns_incidents(api_client, fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    place = {"id": "ts-test", "name": "Pilot Travel Center", "category": "truck_stop", "lat": 39.53, "lng": -119.81}
    r = api_client.post("/api/route", json={"lat": 38.58, "lng": -121.49, "place": place, "skip_parking": True})
    assert r.status_code == 200, r.text
    inc = r.json()["route"]["incidents"]
    assert len(inc) == 2 and set(inc[0]) == {"type", "closed", "description", "index", "criticality"}
