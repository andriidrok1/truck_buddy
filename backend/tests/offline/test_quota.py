"""Daily budgets for the free release (services/quota.py): copilot turns per driver, new satellite checks."""
from datetime import datetime

import pytest
from conftest import run

from models import Place
from services import parking, quota
from services.quota import PACIFIC

SAC = {"lat": 38.5816, "lng": -121.4944}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    quota.reset()
    yield
    quota.reset()


def at(h: int, d: int = 4) -> datetime:
    return datetime(2026, 11, d, h, 0, tzinfo=PACIFIC)


def test_turns_per_driver_per_pacific_day(monkeypatch):
    monkeypatch.setenv("TURNS_PER_DAY", "3")
    assert [quota.take_turn("drv_a", now=at(9)) for _ in range(4)] == [True, True, True, False]
    assert quota.take_turn("drv_b", now=at(9))  # another driver has a budget of their own
    assert quota.take_turn("drv_a", now=at(23)) is False  # still the same Pacific day at 11 pm
    assert quota.take_turn("drv_a", now=at(0, d=5))  # back after midnight Pacific
    assert quota.day(datetime(2026, 11, 5, 7, 30, tzinfo=PACIFIC)) == "2026-11-05"


def test_testers_and_a_zero_budget_are_not_counted(monkeypatch):
    monkeypatch.setenv("TURNS_PER_DAY", "1")
    assert all(quota.take_turn("drv_t", role="tester", now=at(9)) for _ in range(5))
    assert quota.take_turn(None) is True
    monkeypatch.setenv("TURNS_PER_DAY", "0")
    assert all(quota.take_turn("drv_a", now=at(9)) for _ in range(5))


def test_spent_budget_is_spoken_not_an_error(api_client, agent_sandbox, monkeypatch):
    monkeypatch.setenv("TURNS_PER_DAY", "1")
    agent_sandbox.llm.default = {"intent": "chat", "reply": "Sure thing."}
    first = api_client.post("/api/agent", json={"text": "hi", **SAC})
    assert first.status_code == 200 and first.json()["reply"] == "Sure thing."
    second = api_client.post("/api/agent", json={"text": "hi again", **SAC})
    assert second.status_code == 200
    body = second.json()
    assert body["reply"] == quota.OUT_OF_TURNS and body["status"] == "chat" and body["source"] == "quota"


def test_satellite_budget_skips_new_checks_without_caching(monkeypatch):
    monkeypatch.setenv("SAT_NEW_PER_DAY", "1")
    assert quota.take_satellite()  # the day's one check is gone
    fetched = []

    async def no_fetch(lat, lng):
        fetched.append((lat, lng))
        raise AssertionError("must not fetch over budget")

    monkeypatch.setattr(parking, "fetch_satellite", no_fetch)
    place = Place(id="x-1", name="Some Lot", category="truck_stop", lat=40.1, lng=-111.9)
    res = run(parking._check_parking(place))
    assert res.verdict == "unclear" and res.reason == quota.SAT_SKIPPED and fetched == []
    assert parking._from_cache(parking._key(place.lat, place.lng)) is None  # tomorrow it is checked for real
