""""How much is diesel there?" after a find went to reviews and said "No driver reviews there yet" (synthetic voice
run 2026-11-08). It is the price at that place, or that there is none."""
import re

from conftest import fixture_json, run

from models import AgentRequest, Place
from services.agent import PRICE_AT

DATA = fixture_json("here/route_sac_reno")
SAC = {"lat": 38.5816, "lng": -121.4944}
PRICED = Place(id="p", name="Pilot Travel Center, Sacramento, CA", category="truck_stop", lat=38.6, lng=-121.5,
               distance_mi=3.0, diesel_price=4.519, diesel_kind="truck")
BARE = Place(id="l", name="Love's Travel Stop Lodi", category="truck_stop", lat=38.13, lng=-121.27, distance_mi=32.0)


def _prov(agent_sandbox, fake_http, places_, as_intent="reviews"):
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    agent_sandbox.places_provider.places = places_
    agent_sandbox.llm.rules.insert(0, (re.compile(r"diesel there|fuel cost", re.I), {"intent": as_intent}))
    agent_sandbox.llm.rules.insert(0, (re.compile(r"truck stop", re.I), {"intent": "find", "category": "truck_stop"}))
    return agent_sandbox.provider()


def test_price_at_the_place_found(agent_sandbox, fake_http):
    prov = _prov(agent_sandbox, fake_http, [PRICED])
    run(prov.handle(AgentRequest(text="find a truck stop", session_id="p1", **SAC), None))
    res = run(prov.handle(AgentRequest(text="how much is diesel there", session_id="p1", **SAC), None))
    assert res.status == "chat" and res.reply == "Diesel's $4.52 there."


def test_no_price_says_so_by_name(agent_sandbox, fake_http):
    prov = _prov(agent_sandbox, fake_http, [BARE], as_intent="chat")
    run(prov.handle(AgentRequest(text="find a truck stop", session_id="p2", **SAC), None))
    res = run(prov.handle(AgentRequest(text="what's the fuel cost there", session_id="p2", **SAC), None))
    assert res.reply == "I don't have a diesel price for Love's Travel Stop Lodi."


def test_which_phrases_ask_a_price():
    asks = ["how much is diesel there", "what's diesel going for at the Pilot", "diesel price there", "what is fuel at that one",
            "how much for diesel"]
    talk = ["how much fuel do I need", "what is DEF", "how much diesel did I burn"]
    assert all(PRICE_AT.search(t) for t in asks)
    assert not any(PRICE_AT.search(t) for t in talk)
