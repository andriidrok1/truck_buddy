""""What's the weather at Donner when I get there": the forecast at the truck's ETA (weather plan, PR C)."""
import pytest

from conftest import run
from models import AgentRequest, Place, RouteInfo
from services import weather

LINE = [[38.58, -121.49], [38.9, -121.07], [39.28, -120.7], [39.32, -120.33], [39.53, -119.81]]
RENO = Place(id="reno", name="Reno, NV", category="destination", lat=39.53, lng=-119.81)
DONNER = Place(id="donner", name="Donner Pass, CA", category="destination", lat=39.32, lng=-120.33)


def test_seconds_to_a_point_on_the_way():
    total = weather.seconds_to(LINE, (38.58, -121.49), (39.53, -119.81), 10000)
    half = weather.seconds_to(LINE, (38.58, -121.49), (39.32, -120.33), 10000)
    assert total == pytest.approx(10000)
    assert 0 < half < total
    assert weather.seconds_to(LINE, (38.58, -121.49), (34.05, -118.24), 10000) is None  # LA: not on the way
    assert weather.seconds_to(LINE, (39.53, -119.81), (38.9, -121.07), 10000) is None  # already behind


def test_mid_drive_counts_from_the_truck_not_the_whole_trip():
    """Review 2026-11-11: 8 h into a 10 h trip, the end is ~2 h away, not 10 h."""
    mid = weather.seconds_to(LINE, (39.28, -120.7), (39.53, -119.81), 10000)
    whole = weather.seconds_to(LINE, (38.58, -121.49), (39.53, -119.81), 10000)
    assert 0 < mid < whole / 2


@pytest.mark.parametrize("text,hit", [
    ("weather in Reno when I get there", True), ("what will it be like by the time I get to Donner", True),
    ("погода в Рино, когда доеду", True), ("weather in Reno at 3 pm", False), ("weather here", False),
])
def test_when_i_get_there_phrases(text, hit):
    assert bool(weather.WHEN_I_GET.search(text)) is hit


def _capture(monkeypatch):
    seen = {}

    async def fake(place, lat, lng, when_text, now=None, mono=None, in_s=None):
        seen.update(place=place, in_s=in_s, when=when_text)
        return "ok"

    monkeypatch.setattr(weather, "forecast_speech", fake)
    return seen


def test_destination_uses_the_remaining_time(agent_sandbox, monkeypatch):
    seen = _capture(monkeypatch)
    agent_sandbox.llm.on(r"when I get there", {"intent": "weather", "query": None})
    req = AgentRequest(text="what's the weather when I get there", session_id="w1", lat=38.58, lng=-121.49,
                       active_place=RENO, active_duration_min=172, active_distance_mi=132)
    run(agent_sandbox.provider().handle(req, None))
    assert seen["place"] == "Reno" and seen["in_s"] == 172 * 60  # at the start: the whole trip
    req = req.model_copy(update={"lat": 39.33, "lng": -120.18, "session_id": "w1b"})  # near Truckee, 30 mi left
    run(agent_sandbox.provider().handle(req, None))
    assert 0 < seen["in_s"] < 60 * 60


def test_a_place_on_the_route_gets_its_share(agent_sandbox, monkeypatch):
    seen = _capture(monkeypatch)
    agent_sandbox.geocode["donner"] = [DONNER]
    agent_sandbox.llm.on(r"donner", {"intent": "weather", "query": "Donner Pass"})
    prov = agent_sandbox.provider()
    from services.agent.common import _session
    _session("w2")["alternatives"] = {"current": RouteInfo(distance_m=210000, duration_s=10320, distance_mi=130.5, duration_min=172, geometry=LINE, summary="x", source="here")}
    req = AgentRequest(text="weather at Donner when I get there", session_id="w2", lat=38.58, lng=-121.49,
                       active_place=RENO, active_duration_min=172)
    run(prov.handle(req, None))
    assert seen["place"] == "Donner Pass" and 0 < seen["in_s"] < 172 * 60


def test_a_spoken_time_wins(agent_sandbox, monkeypatch):
    seen = _capture(monkeypatch)
    agent_sandbox.llm.on(r"3 pm", {"intent": "weather", "query": None, "when": "3 pm"})
    req = AgentRequest(text="weather when I get there at 3 pm", session_id="w3", lat=38.58, lng=-121.49,
                       active_place=RENO, active_duration_min=172)
    run(agent_sandbox.provider().handle(req, None))
    assert seen["in_s"] is None and seen["when"] == "3 pm"
