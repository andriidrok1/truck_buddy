"""Visits (plan 2026-11-02, track 3): the app logs `arrived` once per stop or destination reached, and the server
counts them per place for the driver, apart from the likes (whether a place stopped at often is a like is not
decided yet). "What do you know about me" says the places stopped at three times or more as visits; "forget that"
and "delete my data" clear them with the rest."""
import os
import time

import pytest
from conftest import run

from models import AgentRequest
from services import db, memory, profile_learn, trip_events

SAC = {"lat": 38.5816, "lng": -121.4944}


def _ago(s):
    return time.time() - s


def _arrived(place_id, name="Pilot Travel Center", category="truck_stop", kind="stop", trip="t1", t=None):
    e = {"kind": "arrived", "trip_id": trip, "data": {"place_id": place_id, "kind": kind, "category": category,
                                                      "name": name}}
    if t is not None:
        e["t"] = t
    return e


def _three_pilots_two_loves(driver):
    run(trip_events.add(driver, [_arrived("g-pilot", trip="t1"), _arrived("g-loves", "Love's Travel Stop", trip="t1")]))
    run(trip_events.add(driver, [_arrived("g-pilot", trip="t2"), _arrived("g-loves", "Love's Travel Stop", trip="t2")]))
    reno = _arrived("here-reno", "Reno, NV", "destination", "destination", trip="t3")
    run(trip_events.add(driver, [_arrived("g-pilot", trip="t3"), reno]))


def test_arrivals_from_the_app_are_counted_per_place(sqlite_store):
    _three_pilots_two_loves("dv1")
    run(trip_events.add("dv1", [_arrived("g-pilot")], source="server"))  # only the app's arrivals count
    run(trip_events.add("dv1", [{"kind": "arrived", "data": {"name": "no id"}}, {"kind": "route_ended", "data": {}}]))
    got = {v["place_id"]: v for v in run(memory.visits("dv1"))}
    assert set(got) == {"g-pilot", "g-loves", "here-reno"}
    assert got["g-pilot"]["count"] == 3 and got["g-pilot"]["name"] == "Pilot Travel Center"
    assert got["g-pilot"]["category"] == "truck_stop" and got["g-loves"]["count"] == 2
    assert got["g-pilot"]["last_at"].tzinfo is not None
    assert run(memory.visits("someone_else")) == []
    # a visit is not a like
    assert run(memory.get_facts("dv1")) is None


def test_visits_keep_the_most_recent_places(sqlite_store):
    events = [_arrived(f"p{i}", f"Stop {i}", t=_ago(3600 - i * 60)) for i in range(memory.MAX_VISITS + 5)]
    run(trip_events.add("dv2", events))
    got = run(memory.visits("dv2"))
    assert len(got) == memory.MAX_VISITS
    assert {v["place_id"] for v in got} == {f"p{i}" for i in range(5, memory.MAX_VISITS + 5)}


def test_forget_and_delete_clear_the_visits(sqlite_store):
    _three_pilots_two_loves("dv3")
    run(memory.clear_facts("dv3"))
    assert run(memory.visits("dv3")) == []
    _three_pilots_two_loves("dv3")
    run(memory.clear("dv3"))
    assert run(memory.visits("dv3")) == []


def test_visits_are_said_plainly_from_three_on():
    visits = [{"place_id": "g-pilot", "name": "Pilot Travel Center, Fernley", "category": "truck_stop", "count": 3},
              {"place_id": "g-loves", "name": "Love's Travel Stop", "category": "truck_stop", "count": 2},
              {"place_id": "here-reno", "name": "Reno, NV", "category": "destination", "count": 7},
              {"place_id": "g-ta", "name": "TA Sparks", "category": "truck_stop", "count": 5}]
    assert profile_learn.visits_speech(visits) == "You've stopped at TA Sparks 5 times and Pilot Travel Center 3 times."
    assert profile_learn.visits_speech(visits[1:3]) == ""


@pytest.mark.parametrize("sessions", ["memory", "db"])
def test_what_you_know_says_the_visits_and_forget_clears_them(sqlite_store, agent_sandbox, monkeypatch, sessions):
    monkeypatch.setenv("SESSION_STORE", sessions)
    _three_pilots_two_loves("dv4")
    agent_sandbox.llm.on(r"know about me", {"intent": "memory", "query": "show"})
    agent_sandbox.llm.on(r"forget that", {"intent": "memory", "query": "forget"})
    prov = agent_sandbox.provider()
    res = run(prov.handle(AgentRequest(text="what do you know about me", session_id=f"dv4:{sessions}", **SAC), "dv4"))
    assert res.reply == "Here's what I know. You've stopped at Pilot Travel Center 3 times.", res.reply
    run(prov.handle(AgentRequest(text="forget that", session_id=f"dv4:{sessions}", **SAC), "dv4"))
    assert run(memory.visits("dv4")) == []
    res = run(prov.handle(AgentRequest(text="what do you know about me", session_id=f"dv4:{sessions}2", **SAC), "dv4"))
    assert "stopped at" not in res.reply


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="needs TEST_DATABASE_URL (a throwaway Postgres)")
def test_visits_on_postgres(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("MEMORY_STORE", "pg")

    async def scenario():
        try:
            await db.migrate()
            await memory.clear("pgv")
            for trip in ("t1", "t2", "t3"):
                await trip_events.add("pgv", [_arrived("g-pilot", trip=trip)])
            await trip_events.add("pgv", [_arrived(f"p{i}", f"Stop {i}", t=_ago(3600 - i * 60))
                                          for i in range(memory.MAX_VISITS)])
            got = await memory.visits("pgv")
            assert len(got) == memory.MAX_VISITS and got[0]["place_id"] == "g-pilot" and got[0]["count"] == 3
            await memory.clear_facts("pgv")
            assert await memory.visits("pgv") == []
            await trip_events.add("pgv", [_arrived("g-pilot")])
            await memory.clear("pgv")
            assert await memory.visits("pgv") == []
        finally:
            await db.close()

    try:
        run(scenario())
    finally:
        db._pool, db._pool_lock = None, None
