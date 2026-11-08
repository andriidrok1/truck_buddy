"""What a route costs (tolls + diesel at the regional average) and choosing the cheapest or fastest way, on screen and
by voice (2026-11-08)."""
import copy

import pytest
from conftest import fixture_json, params_of, run

from models import AgentRequest, Place, RouteInfo, TruckProfile
from services import route_cost
from services.agent import cheaper_offer
from services.speech_text import hours_minutes

DATA = fixture_json("here/route_sac_reno")  # I-80 132 mi 2 h 52 min; CA-89 204 mi; I-580 224 mi; no tolls
SAC = {"lat": 38.5816, "lng": -121.4944}
RENO = Place(id="here-reno", name="Reno, NV", category="destination", lat=39.5296, lng=-119.8138,
             address="Reno, NV, United States")


def _tolled(data, which=(0,), usd=120.0):
    data = copy.deepcopy(data)
    for i in which:
        data["routes"][i]["sections"][0]["tolls"] = [{"fares": [{"price": {"currency": "USD", "value": usd}}]}]
    return data


@pytest.fixture
def diesel(monkeypatch):
    async def at(lat, lng):
        return 8.0, "California"
    monkeypatch.setattr(route_cost, "diesel_price", at)


def _r(mi, mins, tolls=None):
    return RouteInfo(geometry=[], distance_m=mi * 1609, duration_s=mins * 60, distance_mi=mi, duration_min=mins,
                     summary="", source="here", tolls_usd=tolls)


def test_cost_is_fuel_plus_tolls_and_tags_fastest_and_cheapest():
    fast, cheap = _r(130, 170, tolls=60), _r(150, 185)
    route_cost.price([fast, cheap], 8.0, 6.5)
    assert fast.fuel_usd == 160.0 and fast.cost_usd == 220.0
    assert cheap.cost_usd == pytest.approx(184.62)
    route_cost.tag([fast, cheap])
    assert fast.tags == ["fastest"] and cheap.tags == ["cheapest"]


def test_same_cost_is_no_cheapest_and_no_price_is_no_cost():
    a, b = _r(100, 100), _r(100.5, 110)
    route_cost.price([a, b], 8.0, 6.5)
    route_cost.tag([a, b])
    assert a.tags == ["fastest"] and b.tags == []
    route_cost.price([a], None, 6.5)
    assert a.cost_usd is None and a.fuel_usd is None


def test_mpg_from_the_profile():
    assert route_cost.mpg_of(TruckProfile(mpg=8)) == 8 and route_cost.mpg_of(TruckProfile()) == route_cost.DEFAULT_MPG


def test_offer_wording():
    assert cheaper_offer(15 * 60, 12.4) == "There's a way 15 min slower that saves about $12. Want it?"
    assert cheaper_offer(60, 30) == "There's a way about as fast that saves about $30. Want it?"
    assert cheaper_offer(-600, 30) == "There's a way 10 min faster that saves about $30. Want it?"


def _req(text, sid, active=None):
    return AgentRequest(text=text, session_id=sid, active_place=active, **SAC)


def _prov(agent_sandbox):
    agent_sandbox.llm.on(r"take me to reno", {"intent": "go", "query": "Reno, NV"})
    agent_sandbox.geocode["reno"] = [RENO]
    return agent_sandbox.provider()


def test_a_cheaper_way_is_offered_and_yes_takes_it(agent_sandbox, fake_http, diesel):
    fake_http.add("router.hereapi.com", "/v8/routes", json=_tolled(DATA))
    prov = _prov(agent_sandbox)
    first = run(prov.handle(_req("take me to Reno", "c1"), None))
    assert first.status == "route_set" and first.route.labels == ["I-80"]
    assert first.route.tags == ["fastest"] and first.route.cost_usd == pytest.approx(282.58, abs=0.01)
    cheap = first.route.alternatives[0]
    assert cheap.labels == ["I-80", "CA-89"] and cheap.tags == ["cheapest"]
    slower = hours_minutes(round((cheap.duration_s - first.route.duration_s) / 60))
    assert first.reply.endswith(f" There's a way {slower} slower that saves about $31. Want it?")

    yes = run(prov.handle(_req("yes", "c1", active=RENO), None))
    assert yes.status == "route_set" and yes.route.labels == ["I-80", "CA-89"]
    assert "about $31 cheaper in fuel and tolls" in yes.reply
    assert len(fake_http.calls_to("router.hereapi.com")) == 1


def test_no_saying_no_keeps_the_route(agent_sandbox, fake_http, diesel):
    fake_http.add("router.hereapi.com", "/v8/routes", json=_tolled(DATA))
    prov = _prov(agent_sandbox)
    run(prov.handle(_req("take me to Reno", "c2"), None))
    no = run(prov.handle(_req("no", "c2", active=RENO), None))
    assert no.status == "chat" and no.reply == "Okay, staying on this one." and no.route is None


def test_nothing_offered_when_the_fastest_is_the_cheapest(agent_sandbox, fake_http, diesel):
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    first = run(_prov(agent_sandbox).handle(_req("take me to Reno", "c3"), None))
    assert first.route.tags == ["fastest"] and "Want it?" not in first.reply
    assert all(a.tags == [] for a in first.route.alternatives)


def test_no_diesel_price_no_cost_no_offer(agent_sandbox, fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", json=_tolled(DATA))
    first = run(_prov(agent_sandbox).handle(_req("take me to Reno", "c4"), None))  # no EIA key in tests
    assert first.route.cost_usd is None and "Want it?" not in first.reply


def test_cheapest_and_fastest_by_voice(agent_sandbox, fake_http, diesel):
    fake_http.add("router.hereapi.com", "/v8/routes", json=_tolled(DATA))
    agent_sandbox.llm.on(r"cheapest route|fastest way", {"intent": "chat", "reply": "Sure."})
    prov = _prov(agent_sandbox)
    run(prov.handle(_req("take me to Reno", "c5"), None))
    run(prov.handle(_req("no", "c5", active=RENO), None))
    cheap = run(prov.handle(_req("take the cheapest route", "c5", active=RENO), None))
    assert cheap.status == "route_set" and cheap.route.labels == ["I-80", "CA-89"]
    again = run(prov.handle(_req("take the cheapest route", "c5", active=RENO), None))
    assert again.status == "chat" and again.reply == "This is already the cheapest way."
    fast = run(prov.handle(_req("what's the fastest way", "c5", active=RENO), None))
    assert fast.status == "route_set" and fast.route.labels == ["I-80"]


def test_cheapest_with_tolls_everywhere_asks_for_a_toll_free_route_once(agent_sandbox, fake_http, diesel):
    fake_http.add("router.hereapi.com", "/v8/routes", json=_tolled(DATA, (0, 1, 2), usd=200), times=1)
    free = copy.deepcopy(DATA)
    free["routes"] = [free["routes"][2]]  # I-580, no tolls
    fake_http.add("router.hereapi.com", "/v8/routes", json=free)
    agent_sandbox.llm.on(r"cheapest route", {"intent": "chat", "reply": "Sure."})
    prov = _prov(agent_sandbox)
    run(prov.handle(_req("take me to Reno", "c6"), None))
    res = run(prov.handle(_req("take the cheapest route", "c6", active=RENO), None))
    assert res.status == "route_set" and res.route.labels == ["I-80", "I-580"] and not res.route.tolls_usd
    calls = fake_http.calls_to("router.hereapi.com")
    assert len(calls) == 2 and "tollRoad" in dict(params_of(calls[1])).get("avoid[features]", "")
    run(prov.handle(_req("take the cheapest route", "c6", active=RENO), None))
    assert len(fake_http.calls_to("router.hereapi.com")) == 2  # asked once per destination


def test_cheapest_diesel_on_my_route_is_still_a_stop_question():
    from services.agent import FUEL_WORDS, ROAD_WORDS, ROUTE_CHEAPEST
    t = "cheapest diesel on my route"
    assert ROAD_WORDS.search(t) and ROUTE_CHEAPEST.search(t) and FUEL_WORDS.search(t)  # FUEL_WORDS keeps it a stop
