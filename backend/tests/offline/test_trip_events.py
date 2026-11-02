"""The trip log: batches from the app, the server's own events from a turn, reads per trip, retention, privacy."""
import copy
import json
from datetime import datetime, timedelta, timezone

import httpx
from conftest import fixture_json, run

from models import Place
from services import agent, auth, memory, trip_events

DATA = fixture_json("here/route_sac_reno")
SAC = {"lat": 38.5816, "lng": -121.4944}
RENO = Place(id="here-reno", name="Reno, NV", category="destination", lat=39.5296, lng=-119.8138)
PILOT = Place(id="g-pilot-fernley", name="Pilot Travel Center, Fernley", category="truck_stop", lat=39.6, lng=-119.25,
              address="100 Pilot Rd, Fernley, NV", distance_mi=80.0)


def _driver_of(client) -> str:
    import base64
    tok = client.headers["Authorization"].split()[1]
    payload = tok.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))["sub"]


def _ev(kind, trip="trip-1", **data):
    return {"t": "2026-11-01T17:02:11.120Z", "trip_id": trip, "kind": kind, "data": data}


def test_add_read_by_trip_and_driver_isolation(sqlite_store):
    run(trip_events.add("d1", [_ev("route_started", place="p1"), _ev("reroute", cause="off_route", ok=True),
                               _ev("route_started", trip="trip-2"), {"kind": "not_understood", "data": {"text": "hm"}}]))
    run(trip_events.add("d2", [_ev("route_started", trip="trip-x")]))
    got = run(trip_events.for_trip("d1", "trip-1"))
    assert [e["kind"] for e in got] == ["route_started", "reroute"]
    assert got[1]["data"] == {"cause": "off_route", "ok": True} and got[0]["source"] == "app"
    assert got[0]["t"].startswith("2026-11-01T17:02:11")
    trips = run(trip_events.trips("d1"))
    assert [t["trip_id"] for t in trips] == ["trip-1", "trip-2"] or {t["trip_id"] for t in trips} == {"trip-1", "trip-2"}
    assert next(t for t in trips if t["trip_id"] == "trip-1")["kinds"] == {"route_started": 1, "reroute": 1}
    assert run(trip_events.for_trip("d2", "trip-1")) == []  # another driver sees nothing of d1
    assert len(run(trip_events.recent())) == 5 and run(trip_events.recent())[0]["driver"] in ("d1", "d2")


def test_time_parsing_and_clamp():
    now = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    assert trip_events.parse_time("2026-11-01T11:00:00Z", now) == datetime(2026, 11, 1, 11, 0, tzinfo=timezone.utc)
    hour_ago = now - timedelta(hours=1)
    assert trip_events.parse_time(hour_ago.timestamp() * 1000, now) == hour_ago  # epoch ms
    assert trip_events.parse_time(hour_ago.timestamp(), now) == hour_ago  # epoch s
    assert trip_events.parse_time(None, now) == now and trip_events.parse_time("garbage", now) == now
    assert trip_events.parse_time("1999-01-01T00:00:00Z", now) == now - timedelta(days=30)  # a clock set back
    assert trip_events.parse_time("2027-01-01T00:00:00Z", now) == now + timedelta(minutes=5)


def test_a_bad_batch_is_rejected_whole():
    import pytest
    with pytest.raises(ValueError, match="bad kind"):
        trip_events.normalize("d", [_ev("route_started"), {"kind": "Route Started"}])
    with pytest.raises(ValueError, match="over"):
        trip_events.normalize("d", [{"kind": "x", "data": {"blob": "a" * (17 * 1024)}}])
    with pytest.raises(ValueError, match="at most"):
        trip_events.normalize("d", [_ev("x")] * 201)
    with pytest.raises(ValueError, match="trip_id"):
        trip_events.normalize("d", [{"kind": "x", "trip_id": "t" * 65}])


def test_batch_endpoint_stores_and_lists(api_client, sqlite_store, monkeypatch):
    monkeypatch.setenv("MEMORY_STORE", "sqlite")
    r = api_client.post("/api/trips/events", json=[_ev("route_started", place="p1"), _ev("maneuver_said", toNextM=40, stage="now"),
                                                   _ev("route_ended", driving_s=600)])
    assert r.status_code == 200 and r.json() == {"stored": 3}
    r = api_client.get("/api/trips/trip-1/events")
    assert [e["kind"] for e in r.json()["events"]] == ["route_started", "maneuver_said", "route_ended"]
    r = api_client.get("/api/trips")
    assert r.json()["trips"][0]["trip_id"] == "trip-1" and r.json()["trips"][0]["n"] == 3
    # limits are a 422 for the whole batch (the app drops it instead of retrying)
    r = api_client.post("/api/trips/events", json=[_ev("x")] * 201)
    assert r.status_code == 422
    r = api_client.post("/api/trips/events", json=[{"kind": "Bad Kind"}])
    assert r.status_code == 422
    r = api_client.post("/api/trips/events", json=[{"kind": "x", "data": {"blob": "a" * (17 * 1024)}}])
    assert r.status_code == 422
    assert len(api_client.get("/api/trips/trip-1/events").json()["events"]) == 3


def test_store_off_is_503_and_server_events_are_silent(api_client):
    r = api_client.post("/api/trips/events", json=[_ev("route_started")])
    assert r.status_code == 503
    before = len(trip_events._tasks)
    trip_events.add_later("d", "t", "stop_guard", {"result": "asked"})
    assert len(trip_events._tasks) == before  # no task with the store off


def test_recent_is_tester_only(api_client, sqlite_store, monkeypatch):
    monkeypatch.setenv("MEMORY_STORE", "sqlite")
    assert api_client.post("/api/trips/events", json=[_ev("route_started")]).status_code == 200
    assert api_client.get("/api/trips/recent").status_code == 403
    r = api_client.get("/api/trips/recent", headers={"Authorization": f"Bearer {auth._token('tester')}"})
    assert r.status_code == 200 and r.json()["events"][0]["driver"] == _driver_of(api_client)


def test_memory_clear_wipes_trip_events(api_client, sqlite_store, monkeypatch):
    monkeypatch.setenv("MEMORY_STORE", "sqlite")
    me = _driver_of(api_client)
    run(memory.add(me, [("driver", "hi"), ("copilot", "Hello.")]))
    api_client.post("/api/trips/events", json=[_ev("route_started")])
    r = api_client.delete("/api/memory")
    assert r.json()["deleted"] == 2  # turns, as before
    assert api_client.get("/api/trips").json()["trips"] == []


def test_server_records_the_guard_and_a_failed_turn(api_client, agent_sandbox, fake_http, sqlite_store, monkeypatch):
    monkeypatch.setenv("MEMORY_STORE", "sqlite")
    long = copy.deepcopy(DATA)
    for s in long["routes"][0]["sections"]:
        s["summary"]["duration"] *= 6
    fake_http.add("router.hereapi.com", "/v8/routes",
                  fn=lambda request: httpx.Response(200, json=long if "via" in str(request.url) else DATA, request=request))
    agent_sandbox.llm.on(r"take me to reno", {"intent": "go", "query": "Reno, NV"})
    agent_sandbox.llm.on(r"pilot in fernley", {"intent": "add_stop", "query": "Pilot, Fernley NV"})
    agent_sandbox.llm.on(r"blorp", {"intent": "unclear"})
    agent_sandbox.geocode.update({"reno": [RENO], "pilot": [PILOT]})
    body = {"session_id": "te1", "trip_id": "trip-9", **SAC}
    r = api_client.post("/api/agent", json={"text": "take me to Reno", **body})
    assert r.json()["status"] == "route_set"
    reno = r.json()["place"]
    r = api_client.post("/api/agent", json={"text": "stop at the Pilot in Fernley", "active_place": reno, **body})
    assert r.json()["status"] == "confirm"
    r = api_client.post("/api/agent", json={"text": "no", "active_place": reno, **body})
    assert r.json()["reply"] == agent.VIA_FAR_NO
    r = api_client.post("/api/agent", json={"text": "blorp", **body})
    assert r.json()["status"] == "not_understood"
    events = api_client.get("/api/trips/trip-9/events").json()["events"]
    kinds = [(e["kind"], e["data"].get("result")) for e in events]
    assert ("stop_guard", "asked") in kinds and ("stop_guard", "declined") in kinds
    failed = next(e for e in events if e["kind"] == "not_understood")
    assert failed["data"]["text"] == "blorp" and failed["source"] == "server"


def test_reroute_endpoint_records_a_request(api_client, fake_http, sqlite_store, monkeypatch):
    monkeypatch.setenv("MEMORY_STORE", "sqlite")
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    place = RENO.model_dump()
    body = {"lat": 38.9, "lng": -121.07, "heading": 80, "place": place, "handle": None, "section_index": 0, "traveled_m": 0,
            "trip_id": "trip-r"}
    assert api_client.post("/api/reroute", json=body).status_code == 200
    fake_http.routes.clear()
    fake_http.add("router.hereapi.com", "/v8/routes", json=fixture_json("here/error_no_route")["body"])
    assert api_client.post("/api/reroute", json=body).status_code == 422
    events = api_client.get("/api/trips/trip-r/events").json()["events"]
    assert [(e["kind"], e["data"].get("ok"), e["data"].get("error")) for e in events] == [
        ("reroute_requested", True, None), ("route_failed", None, "no_truck_route"), ("reroute_requested", False, "no_truck_route")]
    assert events[0]["data"] == {"section_index": 0, "handle": False, "via": 0, "ok": True}


def test_prune_drops_old_rows(sqlite_store):
    run(trip_events.add("d", [_ev("route_started")]))
    old = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    run(memory._sq_run(lambda con: con.execute(
        "INSERT INTO trip_events (driver, trip_id, kind, at, source, data) VALUES ('d', 'old', 'route_started', ?, 'app', '{}')", (old,))))
    assert run(trip_events.prune()) == 1
    assert [t["trip_id"] for t in run(trip_events.trips("d"))] == ["trip-1"]
