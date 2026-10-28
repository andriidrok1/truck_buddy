"""Stops on the way (HERE via=): request shape, section starts, and the add_stop / clear_stops dialog."""
import copy

from conftest import fixture_json, params_of, run

from models import AgentRequest, Place, TruckProfile
from services import agent, routing
from services.routing import _here_parse, _here_params

DATA = fixture_json("here/route_sac_reno")
SAC = {"lat": 38.5816, "lng": -121.4944}
RENO = Place(id="here-reno", name="Reno, NV", category="destination", lat=39.5296, lng=-119.8138)
PILOT = Place(id="g-pilot-fernley", name="Pilot Travel Center, Fernley", category="truck_stop", lat=39.6, lng=-119.25,
              address="100 Pilot Rd, Fernley, NV", distance_mi=80.0)
LOVES = Place(id="g-loves-auburn", name="Love's Travel Stop", category="truck_stop", lat=38.9, lng=-121.08,
              address="Auburn, CA", distance_mi=30.0)
TA = Place(id="g-ta-sparks", name="TA Sparks", category="truck_stop", lat=39.54, lng=-119.7, distance_mi=120.0)
FOURTH = Place(id="g-fourth", name="Flying J", category="truck_stop", lat=39.0, lng=-120.0, distance_mi=50.0)


def _vias(request):
    return [v for k, v in params_of(request) if k == "via"]


def test_via_params_repeat_in_order_with_a_stop_duration():
    p = _here_params(TruckProfile(), (38.58, -121.49), (39.53, -119.81), via=[(38.9, -121.08), [39.6, -119.25]])
    vias = [v for k, v in p if k == "via"]
    assert vias == ["38.9,-121.08!stopDuration=1800", "39.6,-119.25!stopDuration=1800"]
    assert [k for k, _ in p].index("via") > [k for k, _ in p].index("destination")
    assert "via" not in dict(_here_params(TruckProfile(), (38.58, -121.49), (39.53, -119.81)))


def test_section_starts_follow_the_sections():
    assert _here_parse(DATA).section_starts == [0]
    two = copy.deepcopy(DATA)
    s = two["routes"][0]["sections"][0]
    two["routes"][0]["sections"] = [s, copy.deepcopy(s)]
    info = _here_parse(two)
    n_raw = 2 * 2533
    assert info.section_starts == [0, routing._thin_index(2533, n_raw)]
    assert info.distance_m == 2 * 212550 and len(info.maneuvers) == 2 * 9  # 10 actions each, minus "depart"


def test_route_echoes_via_and_sends_it(fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    info = run(routing.get_routing_provider().route(38.58, -121.49, 39.53, -119.81, TruckProfile(),
                                                    via=[(38.9, -121.08)]))
    assert info.via == [[38.9, -121.08]] and all(a.via == [[38.9, -121.08]] for a in info.alternatives)
    assert _vias(fake_http.calls[0]) == ["38.9,-121.08!stopDuration=1800"]


def test_reroute_fallback_keeps_the_stops(fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes/h", status=400, text="gone")
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    info = run(routing.get_routing_provider().reroute("h", 38.9, -121.07, 80, 39.53, -119.81, TruckProfile(),
                                                      via=[(39.6, -119.25)]))
    assert info.via == [[39.6, -119.25]] and _vias(fake_http.calls[1]) == ["39.6,-119.25!stopDuration=1800"]
    assert _vias(fake_http.calls[0]) == []  # the handle endpoint keeps the stops itself


def _req(text, sid="v1", active=RENO):
    return AgentRequest(text=text, session_id=sid, active_place=active, **SAC)


def _sandbox(agent_sandbox, fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    llm = agent_sandbox.llm
    llm.on(r"take me to reno", {"intent": "go", "query": "Reno, NV"})
    llm.on(r"pilot in fernley", {"intent": "add_stop", "query": "Pilot, Fernley NV"})
    llm.on(r"love'?s", {"intent": "add_stop", "query": "Love's Auburn"})
    llm.on(r"ta in sparks", {"intent": "add_stop", "query": "TA Sparks"})
    llm.on(r"flying j", {"intent": "add_stop", "query": "Flying J"})
    llm.on(r"fuel stop", {"intent": "add_stop", "category": "truck_stop"})
    llm.on(r"narnia", {"intent": "add_stop", "query": "Narnia"})
    llm.on(r"clear the stops|no stops", {"intent": "clear_stops"})
    agent_sandbox.geocode.update({"reno": [RENO], "pilot": [PILOT], "love": [LOVES], "ta sparks": [TA],
                                  "flying": [FOURTH]})
    return agent_sandbox.provider()


def test_add_stop_by_name_then_clear(agent_sandbox, fake_http):
    prov = _sandbox(agent_sandbox, fake_http)
    run(prov.handle(_req("take me to Reno", active=None), None))

    res = run(prov.handle(_req("stop at the Pilot in Fernley on the way"), None))
    assert res.status == "route_set" and res.place.id == RENO.id
    assert res.reply == "Added Pilot Travel Center. New total 2 h 52 min, 132 miles."
    assert res.route.via == [[PILOT.lat, PILOT.lng]] and res.route.alternatives == []
    assert [o.id for o in res.options] == [PILOT.id]
    call = fake_http.calls_to("router.hereapi.com")[-1]
    p = dict(params_of(call))
    assert _vias(call) == [f"{PILOT.lat},{PILOT.lng}!stopDuration=1800"]
    assert p["destination"] == f"{RENO.lat},{RENO.lng}" and "alternatives" not in p

    res = run(prov.handle(_req("and swing by Love's first"), None))
    assert res.reply.startswith("Added Love's Travel Stop.")
    assert _vias(fake_http.calls_to("router.hereapi.com")[-1]) == [
        f"{PILOT.lat},{PILOT.lng}!stopDuration=1800", f"{LOVES.lat},{LOVES.lng}!stopDuration=1800"]
    ctx = prov._context(_req("hi"), agent._SESSIONS["v1"])
    assert "Stops on the way: Pilot Travel Center, Love's Travel Stop." in ctx

    res = run(prov.handle(_req("clear the stops"), None))
    assert res.status == "route_set" and res.reply == "Stops cleared. Straight there, 2 h 52 min, 132 miles."
    assert res.route.via == [] and _vias(fake_http.calls_to("router.hereapi.com")[-1]) == []
    assert "Stops on the way" not in prov._context(_req("hi"), agent._SESSIONS["v1"])


def test_fourth_stop_is_refused(agent_sandbox, fake_http):
    prov = _sandbox(agent_sandbox, fake_http)
    for t in ("stop at the Pilot in Fernley", "swing by Love's", "the TA in Sparks too"):
        assert run(prov.handle(_req(t, sid="full"), None)).status == "route_set"
    res = run(prov.handle(_req("and the Flying J", sid="full"), None))
    assert res.status == "chat" and res.reply == agent.VIA_FULL
    assert len(fake_http.calls_to("router.hereapi.com")) == 3


def test_add_stop_without_a_route_and_unknown_place(agent_sandbox, fake_http):
    prov = _sandbox(agent_sandbox, fake_http)
    res = run(prov.handle(_req("stop at the Pilot in Fernley", sid="none", active=None), None))
    assert res.status == "chat" and res.reply == agent.VIA_NO_ROUTE
    res = run(prov.handle(_req("stop at Narnia", sid="narnia"), None))
    assert res.status == "chat" and res.reply == "I heard Narnia, but can't find it. Say it again or type it."
    assert fake_http.calls == []


def test_add_stop_by_category_uses_places(agent_sandbox, fake_http):
    prov = _sandbox(agent_sandbox, fake_http)
    agent_sandbox.places_provider.places = [LOVES, PILOT]
    res = run(prov.handle(_req("add a fuel stop on the way", sid="cat"), None))
    assert res.status == "route_set" and res.reply.startswith("Added Love's Travel Stop.")  # the closest truck stop
    assert agent_sandbox.places_provider.calls == [("truck_stop", SAC["lat"], SAC["lng"])]


def test_clear_with_nothing_to_clear_and_new_destination_drops_stops(agent_sandbox, fake_http):
    prov = _sandbox(agent_sandbox, fake_http)
    res = run(prov.handle(_req("no stops", sid="empty"), None))
    assert res.status == "chat" and res.reply == agent.VIA_NONE
    run(prov.handle(_req("stop at the Pilot in Fernley", sid="swap"), None))
    assert agent._SESSIONS["swap"]["via"]["stops"][0].id == PILOT.id
    run(prov.handle(_req("take me to Reno", sid="swap", active=None), None))  # a new primary route
    assert "via" not in agent._SESSIONS["swap"]


def test_here_down_while_adding_a_stop(agent_sandbox, fake_http):
    prov = _sandbox(agent_sandbox, fake_http)
    fake_http.routes.clear()
    fake_http.add("router.hereapi.com", "/v8/routes", status=503, text="down")
    res = run(prov.handle(_req("stop at the Pilot in Fernley", sid="down"), None))
    assert res.status == "error" and res.reply == agent.ROUTE_DOWN
    assert "via" not in agent._SESSIONS["down"]  # nothing remembered when the route failed


def test_route_and_reroute_endpoints_accept_via(api_client, fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    body = {**SAC, "place": RENO.model_dump(), "skip_parking": True, "via": [[38.9, -121.08]]}
    r = api_client.post("/api/route", json=body)
    assert r.status_code == 200 and r.json()["route"]["via"] == [[38.9, -121.08]]
    assert _vias(fake_http.calls[-1]) == ["38.9,-121.08!stopDuration=1800"]
    r = api_client.post("/api/reroute", json={**SAC, "place": RENO.model_dump(), "via": [[38.9, -121.08]]})
    assert r.status_code == 200 and r.json()["route"]["via"] == [[38.9, -121.08]]
    assert api_client.post("/api/route", json={**body, "via": [[1, 2], [3, 4], [5, 6], [7, 8]]}).status_code == 422
    assert api_client.post("/api/route", json={**body, "via": [[48.86, 2.35]]}).status_code == 422  # Paris
    assert api_client.post("/api/route", json={**body, "via": [[38.9]]}).status_code == 422
