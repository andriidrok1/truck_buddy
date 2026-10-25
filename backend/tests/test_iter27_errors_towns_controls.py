# Iteration 27: distinct infra-error lines, towns never get a parking verdict, control phrases never geocoded,
# found places survive a route (same session), "take the second one"
import asyncio
from unittest.mock import patch

import pytest
import requests

from conftest import BASE_URL
from models import AgentRequest, Place
from services import agent, parking
from services.agent import LLM_DOWN, PLACES_DOWN, ROUTE_DOWN, control_phrase
from services.google_places import PlacesUnavailable

SAC = {"lat": 38.58, "lng": -121.49}
API = f"{BASE_URL}/api"


def _ask(text, sid=None, active=None):
    body = {"text": text, **SAC}
    if sid:
        body["session_id"] = sid
    if active:
        body["active_place"] = active
    r = requests.post(f"{API}/agent", json=body, timeout=90)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("text,kind", [
    ("start the route", "start_navigation"), ("start the ride", "start_navigation"),
    ("the stars are out", "start_navigation"), ("let's go", "start_navigation"),
    ("cancel the trip", "end_route"), ("turn directions off", "voice_guidance"), ("recenter", "recenter"),
])
def test_control_phrase(text, kind):
    assert control_phrase(text)["type"] == kind


@pytest.mark.parametrize("text", ["2828 El Centro Rd", "let's go to Reno Nevada Truck Plaza", "Pilot in Dixon"])
def test_not_control_phrase(text):
    assert control_phrase(text) is None


def _run(req):
    return asyncio.run(agent.LlmAgentProvider()._handle(req))


def _req(text):
    return AgentRequest(text=text, lat=38.58, lng=-121.49)


def test_llm_failure_has_its_own_line():
    async def boom(*a, **k):
        raise asyncio.TimeoutError()
    with patch.object(agent, "classify", boom):
        r = _run(_req("I need fuel"))
    assert r.status == "error" and r.reply == LLM_DOWN and r.message == LLM_DOWN


def test_places_down_has_its_own_line():
    async def cls(*a, **k):
        return {"intent": "find", "category": "truck_stop"}

    class Down:
        async def search(self, *a, **k):
            raise PlacesUnavailable("google 503")
    with patch.object(agent, "classify", cls), patch.object(agent, "get_places_provider", lambda: Down()):
        r = _run(_req("I need fuel"))
    assert r.status == "error" and r.reply == PLACES_DOWN


def test_routing_down_has_its_own_line():
    async def cls(*a, **k):
        return {"intent": "go", "query": "Reno, NV"}

    async def geo(*a, **k):
        return [Place(id="here-x", name="Reno, NV", category="destination", lat=39.53, lng=-119.81)]

    class Down:
        async def route(self, *a, **k):
            raise RuntimeError("HERE 500")
    with patch.object(agent, "classify", cls), patch.object(agent, "geocode", geo), \
            patch.object(agent, "get_routing_provider", lambda: Down()):
        r = _run(_req("take me to Reno"))
    assert r.status == "error" and r.reply == ROUTE_DOWN


def test_town_never_gets_a_verdict():
    town = Place(id="here-town-x", name="Sacramento, CA", category="destination", lat=38.57944, lng=-121.49085)
    assert parking.cached_parking(town).label == "No parking check for a town"
    from services import approach
    assert approach.best(town).verdict == "unclear"


def test_town_parking_question_live():
    sid = "t27-town"
    r1 = _ask("take me to Sacramento", sid)
    assert r1["status"] == "route_set", r1
    assert r1["place"]["id"].startswith("here-town-"), r1["place"]["id"]
    r2 = _ask("what's the parking like there", sid, r1["place"])
    assert "unlikely" not in r2["reply"].lower() and "tight" not in r2["reply"].lower(), r2["reply"]
    assert "town" in r2["reply"].lower(), r2["reply"]


def test_start_the_ride_with_route_is_navigation_not_geocode():
    sid = "t27-ctl"
    r1 = _ask("take me to Reno", sid)
    assert r1["status"] == "route_set"
    for text in ("start the ride", "the stars are out"):
        r = _ask(text, sid, r1["place"])
        assert "can't find" not in r["reply"].lower(), r["reply"]
        assert any(a["type"] == "start_navigation" for a in r.get("actions") or []), (text, r)


def test_take_the_second_one_after_route_same_session():
    sid = "t27-second"
    r1 = _ask("I need fuel", sid)
    assert r1["status"] == "route_set" and r1["options"], r1
    shown = [r1["place"], *r1["options"]]  # the list on screen, in order
    r2 = _ask("take the second one", sid, r1["place"])
    assert r2["status"] == "route_set", r2
    assert r2["place"]["id"] == shown[1]["id"], (r2["place"]["name"], [p["name"] for p in shown])
