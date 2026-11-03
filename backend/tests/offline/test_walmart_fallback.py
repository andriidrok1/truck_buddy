"""QA 2026-11-02 (UX): "find a Walmart" with none around ended at "I couldn't find one nearby". A Walmart is where
a driver parks or picks up supplies: the closest truck stop is offered instead, and for the night the closest
rest area counts too. "yes" goes there."""
import re

from conftest import fixture_json, run

from models import AgentRequest, Place

DATA = fixture_json("here/route_sac_reno")
SAC = {"lat": 38.5816, "lng": -121.4944}
STOP = Place(id="ts", name="Roady's Truck Stop", category="truck_stop", lat=38.62, lng=-121.42, distance_mi=4.2,
             address="1 A St, West Sacramento, CA")
REST = Place(id="ra", name="Elkhorn Rest Area", category="rest_area", lat=38.66, lng=-121.6, distance_mi=2.5,
             address="I-5, Sacramento, CA")


def _prov(agent_sandbox, fake_http, monkeypatch, places):
    monkeypatch.setenv("CATEGORIES_EXTRA", "walmart")
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    agent_sandbox.places_provider.places = places
    agent_sandbox.llm.rules.insert(0, (re.compile(r"walmart", re.I), {"intent": "find", "category": "walmart"}))
    return agent_sandbox.provider()


def _req(text, sid):
    return AgentRequest(text=text, session_id=sid, **SAC)


def test_no_walmart_offers_the_closest_truck_stop(agent_sandbox, fake_http, monkeypatch):
    prov = _prov(agent_sandbox, fake_http, monkeypatch, [STOP, REST])
    res = run(prov.handle(_req("find a Walmart", "w1"), None))
    assert res.status == "confirm" and res.route is None
    assert res.reply == ("I couldn't find a Walmart nearby. The closest truck stop is Roady's Truck Stop, 4.2 miles. "
                         "Want to go there?")
    yes = run(prov.handle(_req("yes", "w1"), None))
    assert yes.status == "route_set" and yes.place.id == STOP.id


def test_no_walmart_for_the_night_counts_a_rest_area(agent_sandbox, fake_http, monkeypatch):
    prov = _prov(agent_sandbox, fake_http, monkeypatch, [STOP, REST])
    res = run(prov.handle(_req("can I park overnight at a Walmart", "w2"), None))
    assert res.status == "confirm"
    assert res.reply == ("I couldn't find a Walmart nearby. The closest rest area is Elkhorn Rest Area, 2.5 miles. "
                         "Want to go there?")
    no = run(prov.handle(_req("no", "w2"), None))
    assert no.status == "chat" and no.route is None


def test_nothing_at_all_is_still_said(agent_sandbox, fake_http, monkeypatch):
    prov = _prov(agent_sandbox, fake_http, monkeypatch, [])
    res = run(prov.handle(_req("find a Walmart", "w3"), None))
    assert res.status == "no_results" and res.reply == "I couldn't find one nearby."
