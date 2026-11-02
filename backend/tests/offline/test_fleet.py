"""Fleet dashboard layer (plan Б2 steps 4-7): events carry org, truck and position; /api/fleet/* is per org and
never shows a transcript; a fleet's stored Motive key; the JSON turn line, request ids, LOG_TRANSCRIPTS,
/api/health?deep=1 and scripts/turn_stats.py on both log formats."""
import json
import logging
import sys
from pathlib import Path

import httpx
import pytest
from conftest import fixture_json, params_of, run

from models import Place, TruckProfile
from services import auth, db, tenancy, trip_events

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import turn_stats  # noqa: E402

DATA = fixture_json("here/route_sac_reno")
MOTIVE = fixture_json("motive/available_time")
SAC = {"lat": 38.5816, "lng": -121.4944}
RENO = Place(id="here-reno", name="Reno, NV", category="destination", lat=39.5296, lng=-119.8138)


def _claims(token: str) -> dict:
    import base64
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


@pytest.fixture
def client(sqlite_store, monkeypatch):
    """A TestClient on the sqlite store, no default token (each test signs up the devices it needs)."""
    from fastapi.testclient import TestClient
    import server
    monkeypatch.setenv("MEMORY_STORE", "sqlite")
    tenancy.reset_caches()
    auth._signups.clear()
    server._deep.clear()
    with TestClient(server.app) as c:
        yield c


def _device(c, code=None) -> dict:
    """Headers of a freshly signed-up device; bound to a fleet when a code is given."""
    token = c.post("/api/auth/device").json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    if code:
        r = c.post("/api/auth/fleet-code", json={"code": code}, headers=h)
        assert r.status_code == 200, r.text
        h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    return h


def _sub(h: dict) -> str:
    return _claims(h["Authorization"].split()[1])["sub"]


def _two_fleets(c):
    """Acme (two trucks, a dispatcher, an owner, a driver in Unit 1) and Bravo (one truck, its dispatcher and driver)."""
    acme, bravo = run(tenancy.create_org("Acme")), run(tenancy.create_org("Bravo"))
    t1 = run(tenancy.create_vehicle(acme, "Unit 1", TruckProfile(height_in=162)))
    t2 = run(tenancy.create_vehicle(acme, "Unit 2"))
    t9 = run(tenancy.create_vehicle(bravo, "B-9"))
    return {
        "acme": acme, "bravo": bravo, "t1": t1, "t2": t2, "t9": t9,
        "a_disp": _device(c, run(tenancy.create_fleet_code(acme, None, "dispatcher"))),
        "a_owner": _device(c, run(tenancy.create_fleet_code(acme, None, "owner"))),
        "a_drv": _device(c, run(tenancy.create_fleet_code(acme, t1))),
        "b_disp": _device(c, run(tenancy.create_fleet_code(bravo, None, "dispatcher"))),
        "b_drv": _device(c, run(tenancy.create_fleet_code(bravo, t9))),
        "solo": _device(c),
    }


def _ev(kind, trip="trip-1", t="2026-11-02T17:00:00Z", **data):
    return {"t": t, "trip_id": trip, "kind": kind, "data": data}


def _day(c, h, trip="trip-1"):
    """Waits for the driver's background saves (the driver's own read settles them) and returns its trip events."""
    return c.get(f"/api/trips/{trip}/events", headers=h).json()["events"]


def test_events_carry_org_truck_and_position(client, fake_http):
    f = _two_fleets(client)
    r = client.post("/api/trips/events", headers=f["a_drv"], json=[
        _ev("route_started", place_id="here-reno", lat=38.6, lng=-121.4, t="2026-11-02T17:00:00Z"),
        _ev("maneuver_said", action="turn", index=3, to_next_m=40, t="2026-11-02T17:05:00Z"),
        _ev("speech_dropped", why="ttl", lat="nope", lng=None, t="2026-11-02T17:06:00Z")])  # not a position, stored anyway
    assert r.status_code == 200, r.text
    # a server event takes the request's position
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    body = {"lat": 38.9, "lng": -121.07, "heading": 80, "place": RENO.model_dump(), "handle": None, "section_index": 0,
            "traveled_m": 0, "trip_id": "trip-1"}
    assert client.post("/api/reroute", json=body, headers=f["a_drv"]).status_code == 200
    _day(client, f["a_drv"])
    rows = run(db._sq_run(lambda con: con.execute(
        "SELECT kind, org_id, vehicle_id, lat, lng FROM trip_events ORDER BY id").fetchall()))
    assert rows == [("route_started", f["acme"], f["t1"], 38.6, -121.4), ("maneuver_said", f["acme"], f["t1"], None, None),
                    ("speech_dropped", f["acme"], f["t1"], None, None), ("reroute_requested", f["acme"], f["t1"], 38.9, -121.07)]
    # a solo driver's events land in the org of one, without a truck
    client.post("/api/trips/events", headers=f["solo"], json=[_ev("route_started", trip="s1")])
    solo_org = run(tenancy.device_of(_sub(f["solo"]))).org_id
    row = run(db._sq_run(lambda con: con.execute(
        "SELECT org_id, vehicle_id FROM trip_events WHERE trip_id = 's1'").fetchone()))
    assert row == (solo_org, None) and solo_org not in (f["acme"], f["bravo"])


def test_dashboard_kinds_are_validated(client):
    h = _device(client)
    bad = [[_ev("route_started", distance_m="far")], [_ev("reroute", ok="maybe")], [_ev("route_ended", lat=200, lng=0)],
           [_ev("stop_guard", result={"x": 1})]]
    for batch in bad:
        r = client.post("/api/trips/events", headers=h, json=batch)
        assert r.status_code == 422, batch
    ok = [_ev("route_started", distance_m=1200.5, alternatives=2, restricted=None, source="here", labels=["fastest"]),
          _ev("reroute", cause="off_route", ok=True, applied=True), _ev("some_new_kind", anything={"goes": [1]})]
    assert client.post("/api/trips/events", headers=h, json=ok).json() == {"stored": 3}
    with pytest.raises(ValueError, match="route_started data.distance_m"):
        trip_events.normalize("d", [_ev("route_started", distance_m="far")])


def test_fleet_vehicles_are_per_org_with_the_last_position(client):
    f = _two_fleets(client)
    client.post("/api/trips/events", headers=f["a_drv"], json=[
        _ev("route_started", lat=38.6, lng=-121.4, t="2026-11-02T17:00:00Z"),
        _ev("maneuver_said", action="turn", lat=38.7, lng=-121.3, t="2026-11-02T17:10:00Z"),
        _ev("speech_dropped", why="ttl", t="2026-11-02T17:20:00Z")])  # newest, but no position
    client.post("/api/trips/events", headers=f["b_drv"], json=[_ev("route_started", lat=40.0, lng=-120.0)])
    r = client.get("/api/fleet/vehicles", headers=f["a_disp"])
    assert r.status_code == 200, r.text
    by_id = {v["id"]: v for v in r.json()["vehicles"]}
    assert set(by_id) == {f["t1"], f["t2"]}  # never Bravo's truck
    u1 = by_id[f["t1"]]
    assert u1["position"]["lat"] == 38.7 and u1["position"]["lng"] == -121.3 and u1["status"] == "on_route"
    assert u1["last_kind"] == "speech_dropped" and u1["last_seen"].startswith("2026-11-02T17:20") and u1["devices"] == 1
    assert by_id[f["t2"]]["status"] == "no_data" and by_id[f["t2"]]["position"] is None
    client.post("/api/trips/events", headers=f["a_drv"], json=[_ev("route_ended", driving_s=600, t="2026-11-02T17:30:00Z")])
    assert next(v for v in client.get("/api/fleet/vehicles", headers=f["a_disp"]).json()["vehicles"]
                if v["id"] == f["t1"])["status"] == "idle"
    # Bravo sees its own truck only; the owner reads like a dispatcher
    assert [v["id"] for v in client.get("/api/fleet/vehicles", headers=f["b_disp"]).json()["vehicles"]] == [f["t9"]]
    assert client.get("/api/fleet/vehicles", headers=f["a_owner"]).status_code == 200


def test_drivers_and_other_orgs_get_403_or_404(client):
    f = _two_fleets(client)
    for who in ("a_drv", "solo", "b_drv"):  # a driver's token, a fleet driver's included, is not a fleet reader
        for path in ("/api/fleet/vehicles", "/api/fleet/summary", f"/api/fleet/vehicles/{f['t1']}/events", "/api/fleet/drivers"):
            assert client.get(path, headers=f[who]).status_code == 403, (who, path)
    assert client.get("/api/fleet/vehicles").status_code == 401
    # another fleet's dispatcher: the truck does not exist for them
    assert client.get(f"/api/fleet/vehicles/{f['t1']}/events", headers=f["b_disp"]).status_code == 404
    assert client.get(f"/api/fleet/vehicles/{f['t9']}/events", headers=f["b_disp"]).status_code == 200
    summary = client.get("/api/fleet/summary?day=2026-11-02", headers=f["b_disp"]).json()
    assert [v["vehicle_id"] for v in summary["vehicles"]] == [f["t9"]]
    assert client.get("/api/fleet/summary?day=2026-13-40", headers=f["a_disp"]).status_code == 422
    assert client.get("/api/fleet/summary?day=yesterday", headers=f["a_disp"]).status_code == 422


def test_fleet_events_never_carry_the_transcript(client):
    f = _two_fleets(client)
    client.post("/api/trips/events", headers=f["a_drv"], json=[
        _ev("turn_failed", status="not_understood", text="take me to my sister's on 5th street", t="2026-11-02T17:00:00Z"),
        _ev("route_started", place_id="p1", t="2026-11-02T17:01:00Z")])
    driver = run(auth._principal(_claims(f["a_drv"]["Authorization"].split()[1])))
    run(trip_events.add(driver, [{"t": "2026-11-02T17:02:00Z", "trip_id": "trip-1", "kind": "not_understood",
                                  "data": {"intent": "unclear", "text": "blorp", "transcript": "blorp"}}], "server"))
    events = client.get(f"/api/fleet/vehicles/{f['t1']}/events?day=2026-11-02", headers=f["a_disp"]).json()["events"]
    assert [e["kind"] for e in events] == ["turn_failed", "route_started", "not_understood"]
    assert all("text" not in e["data"] and "transcript" not in e["data"] for e in events)
    assert events[2]["data"] == {"intent": "unclear"} and "driver" not in events[0]
    assert "5th street" not in json.dumps(events) and "blorp" not in json.dumps(events)
    # the driver's own trip log keeps it (decision 2026-11-01)
    assert _day(client, f["a_drv"])[0]["data"]["text"] == "take me to my sister's on 5th street"
    # another day is empty
    assert client.get(f"/api/fleet/vehicles/{f['t1']}/events?day=2026-11-01", headers=f["a_disp"]).json()["events"] == []


def test_fleet_summary_counts_per_truck(client):
    f = _two_fleets(client)
    client.post("/api/trips/events", headers=f["a_drv"], json=[
        _ev("route_started"), _ev("reroute", cause="off_route", ok=True), _ev("reroute_requested", ok=True),
        _ev("reroute_requested", ok=False, error="no_truck_route"), _ev("route_failed", where="reroute"),
        _ev("stop_guard", result="asked"), _ev("stop_guard", result="declined"), _ev("not_understood", text="hm"),
        _ev("turn_failed", status="error"), _ev("maneuver_said", action="turn")])
    client.post("/api/trips/events", headers=f["b_drv"], json=[_ev("route_started"), _ev("not_understood")])
    s = client.get("/api/fleet/summary?day=2026-11-02", headers=f["a_disp"]).json()
    u1 = next(v for v in s["vehicles"] if v["vehicle_id"] == f["t1"])
    assert u1 == {"vehicle_id": f["t1"], "label": "Unit 1", "routes": 1, "reroutes": 2, "guard_questions": 1, "failed_turns": 2,
                  "route_failures": 1}
    assert next(v for v in s["vehicles"] if v["vehicle_id"] == f["t2"])["routes"] == 0
    assert s["totals"]["failed_turns"] == 2 and s["day"] == "2026-11-02"  # Bravo's turn is not counted


def test_fleet_motive_key_is_used_only_for_a_mapped_driver(client, agent_sandbox, fake_http, monkeypatch, caplog):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("CREDENTIALS_KEY", Fernet.generate_key().decode())
    f = _two_fleets(client)
    fleet_key = "fleet-motive-key-XYZ789"
    assert client.put("/api/fleet/credentials/motive", json={"key": fleet_key}, headers=f["a_disp"]).status_code == 403
    assert client.put("/api/fleet/credentials/motive", json={"key": fleet_key}, headers=f["a_owner"]).json() == {"stored": True}
    company = {"users": [{"user": {"id": 7, "first_name": "Other"}, "available_time": {"drive": 600, "shift": 600, "cycle": 600}},
                         MOTIVE["users"][0]]}  # the company answer lists somebody else first
    fake_http.add("api.gomotive.com", "/v1/available_time", json=company)
    agent_sandbox.llm.on(r"hours", {"intent": "hos"})
    ask = {"text": "how many hours do I have", **SAC}
    # not mapped yet: the fleet key is not used at all
    r = client.post("/api/agent", json=ask, headers=f["a_drv"])
    assert r.json()["reply"] == "Connect Motive in settings and I'll keep track of your hours." and not fake_http.calls_to("api.gomotive.com")
    drivers = client.get("/api/fleet/drivers", headers=f["a_disp"]).json()["drivers"]
    mine = next(d for d in drivers if f["t1"] in d["vehicle_ids"])
    assert not mine["motive_linked"] and all("sub" not in d for d in drivers)
    other_org_driver = run(tenancy.device_of(_sub(f["b_drv"]))).driver_id
    # the mapping is the owner's, like the key: a dispatcher gets 403
    assert client.put(f"/api/fleet/drivers/{mine['id']}/motive", json={"user_id": "1"}, headers=f["a_disp"]).status_code == 403
    assert client.put(f"/api/fleet/drivers/{other_org_driver}/motive", json={"user_id": "1"}, headers=f["a_owner"]).status_code == 404
    assert client.put(f"/api/fleet/drivers/{mine['id']}/motive", json={"user_id": "1"}, headers=f["a_owner"]).status_code == 200
    assert client.put(f"/api/fleet/drivers/{mine['id']}/motive", json={"user_id": "1"}, headers=f["a_owner"]).status_code == 200
    # one Motive user is one driver of the fleet: a second driver with the same id is a 409, another fleet may
    second = run(tenancy.device_of(_sub(_device(client, run(tenancy.create_fleet_code(f["acme"], f["t2"])))))).driver_id
    r = client.put(f"/api/fleet/drivers/{second}/motive", json={"user_id": "1"}, headers=f["a_owner"])
    assert r.status_code == 409, r.text
    assert run(tenancy.set_motive_user(f["bravo"], other_org_driver, "1")) is True
    with caplog.at_level(logging.INFO):
        r = client.post("/api/agent", json=ask, headers=f["a_drv"])
    assert r.json()["reply"].startswith("You've got 4 hours 20 minutes of drive time")  # user 1, not user 7
    call = fake_http.calls_to("api.gomotive.com")[-1]
    assert call.headers["X-Api-Key"] == fleet_key and ("driver_ids[]", "1") in params_of(call)
    assert fleet_key not in caplog.text
    # the driver's own header still wins
    fake_http.routes.clear()
    fake_http.add("api.gomotive.com", "/v1/available_time", json=MOTIVE)
    client.post("/api/agent", json=ask, headers={**f["a_drv"], "X-Motive-Key": "personal-key-1"})
    call = fake_http.calls_to("api.gomotive.com")[-1]
    assert call.headers["X-Api-Key"] == "personal-key-1" and not params_of(call)
    # Bravo has no key: its driver gets the connect line
    r = client.post("/api/agent", json=ask, headers=f["b_drv"])
    assert r.json()["reply"] == "Connect Motive in settings and I'll keep track of your hours."


def test_turn_line_is_json_without_the_transcript(client, agent_sandbox, monkeypatch, caplog):
    h = _device(client)
    agent_sandbox.llm.on(r"blorp", {"intent": "unclear"})
    monkeypatch.delenv("LOG_TRANSCRIPTS", raising=False)
    with caplog.at_level(logging.INFO):
        r = client.post("/api/agent", json={"text": "blorp my home is 12 Elm St", "session_id": "s-7", **SAC},
                        headers={**h, "X-Request-Id": "req-from-app-1"})
    assert r.status_code == 200 and r.headers["x-request-id"] == "req-from-app-1"
    assert "Elm St" not in caplog.text
    line = next(rec.getMessage() for rec in caplog.records if rec.getMessage().startswith("turn "))
    fields = json.loads(line[len("turn "):])
    org = run(tenancy.device_of(_sub(h))).org_id
    assert fields["req"] == "req-from-app-1" and fields["driver"] == _sub(h) and fields["sid"] == "s-7" and fields["org"] == org
    assert fields["status"] == "not_understood" and "text" not in fields and "transcript" not in fields
    assert all(getattr(rec, "req", None) == "req-from-app-1" for rec in caplog.records if rec.getMessage().startswith("turn "))
    # the trip log still has the words, and the request id to join them
    client.get("/api/trips", headers=h)  # waits for the driver's background saves
    row = run(db._sq_run(lambda con: con.execute("SELECT data FROM trip_events WHERE kind = 'not_understood'").fetchone()))
    assert json.loads(row[0])["req"] == "req-from-app-1" and "Elm St" in json.loads(row[0])["text"]
    caplog.clear()
    monkeypatch.setenv("LOG_TRANSCRIPTS", "1")
    with caplog.at_level(logging.INFO):
        r = client.post("/api/agent", json={"text": "blorp my home is 12 Elm St", **SAC}, headers=h)
    assert "Elm St" in caplog.text and len(r.headers["x-request-id"]) == 16


def _tester(c, monkeypatch) -> dict:
    monkeypatch.setenv("TESTER_CODE", "TEST-SCENARIOS")
    r = c.post("/api/auth/access-code", json={"code": "TEST-SCENARIOS"})
    assert r.status_code == 200 and r.json()["role"] == "tester", r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.parametrize("sent, kept", [
    ("req-from-app.1_A", True), ("a" * 64, True), ("a" * 65, False), ("has space", False), ("x\u00e9".encode(), False),
    ("evil\" injected=1", False), ("", False), ("../../etc", False),
])
def test_request_id_is_taken_only_when_well_formed(client, sent, kept):
    r = client.get("/api/health", headers={"X-Request-Id": sent})  # bytes: a non-ASCII header as sent on the wire
    rid = r.headers["x-request-id"]
    assert (rid == sent) if kept else (rid != sent and len(rid) == 16)


def test_unhandled_error_is_logged_and_answered_with_the_request_id(caplog):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services import reqid

    app = FastAPI()

    @app.get("/boom")
    async def boom():
        raise RuntimeError("kaboom")
    app.add_middleware(reqid.RequestIdMiddleware)
    reqid.install_log_field()
    with caplog.at_level(logging.ERROR), TestClient(app) as c:
        r = c.get("/boom", headers={"X-Request-Id": "req-boom-1"})
    assert r.status_code == 500 and r.text == "Internal Server Error" and r.headers["x-request-id"] == "req-boom-1"
    rec = next(rec for rec in caplog.records if rec.name == "truckbuddy.http")
    assert rec.req == "req-boom-1" and rec.exc_info and "kaboom" in caplog.text


def test_health_deep_checks_the_db_and_here(client, fake_http, monkeypatch, caplog):
    import server
    plain = client.get("/api/health").json()
    assert plain["ok"] and "checks" not in plain and not fake_http.calls_to("api.gomotive.com")
    fake_http.add("geocode.search.hereapi.com", "/v1/geocode", json={"items": [{"title": "Sacramento, CA"}]})
    # deep=1 is for testers: no token 401, a driver 403, and HERE is not called for either
    assert client.get("/api/health?deep=1").status_code == 401
    assert client.get("/api/health?deep=1", headers=_device(client)).status_code == 403
    assert not fake_http.calls_to("geocode.search.hereapi.com")
    client.headers.update(_tester(client, monkeypatch))
    monkeypatch.setenv("HERE_API_KEY", "here-key-SECRET42")
    with caplog.at_level(logging.INFO):
        r = client.get("/api/health?deep=1")
    # the request went through the app's httpx client, and its URL (with apiKey) was not logged at INFO
    assert fake_http.calls_to("geocode.search.hereapi.com")[0].url.params["apiKey"] == "here-key-SECRET42"
    assert "here-key-SECRET42" not in caplog.text and not any(rec.name.startswith("httpx") for rec in caplog.records)
    assert r.status_code == 200, r.text
    checks = r.json()["checks"]
    assert checks["db"]["ok"] is True and checks["db"]["store"] == "sqlite" and checks["here"]["ok"] is True
    client.get("/api/health?deep=1")
    assert len(fake_http.calls_to("geocode.search.hereapi.com")) == 1  # cached for DEEP_CACHE_S
    server._deep.clear()
    fake_http.routes.clear()
    fake_http.add("geocode.search.hereapi.com", "/v1/geocode", status=401, json={"error": "Unauthorized"})
    r = client.get("/api/health?deep=1")
    assert r.status_code == 503 and r.json()["ok"] is False and r.json()["checks"]["here"] == {**r.json()["checks"]["here"], "ok": False}
    server._deep.clear()
    monkeypatch.setenv("HEALTH_HERE", "0")
    r = client.get("/api/health?deep=1")
    assert r.status_code == 200 and r.json()["checks"]["here"] == {"ok": None, "skipped": True}
    server._deep.clear()
    fake_http.raise_for("geocode.search.hereapi.com", "/v1/geocode", httpx.ConnectError("offline"))
    monkeypatch.setenv("HEALTH_HERE", "1")

    async def broken(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(db, "_sq_run", broken)
    r = client.get("/api/health?deep=1")
    assert r.status_code == 503 and r.json()["checks"]["db"]["ok"] is False and r.json()["checks"]["here"]["ok"] is False


def test_health_deep_refreshes_once_for_concurrent_callers(monkeypatch):
    """Ten requests at once on a cold cache make one HERE call, not ten."""
    import asyncio
    import server
    server._deep.clear()
    calls = []

    async def slow_here():
        calls.append(1)
        await asyncio.sleep(0.05)
        return {"ok": True, "ms": 50}

    async def db_ok():
        return {"ok": True, "store": "sqlite"}
    monkeypatch.setattr(server, "_check_here", slow_here)
    monkeypatch.setattr(server, "_check_db", db_ok)

    async def burst():
        return await asyncio.gather(*(server._deep_checks() for _ in range(10)))
    results = run(burst())
    server._deep.clear()
    assert len(calls) == 1 and all(r["here"]["ok"] is True for r in results)


def test_turn_stats_reads_old_and_new_lines():
    old = "2026-11-01 18:00:00,000 - truckbuddy - INFO - turn status=route_set intent=go stt=800 llm_round_1=1500 total=2300"
    new = [
        '2026-11-02 18:00:00,000 - truckbuddy - INFO - r1 - turn {"req":"r1","driver":"drv_a","sid":"s1","org":"org_1",'
        '"status":"route_set","intent":"go","stt":900,"llm_round_1":1000,"total":1900,"n_prompt_tokens":2000,"n_cached_tokens":1000}',
        '2026-11-02 18:00:05,000 - truckbuddy - INFO - r2 - turn {"req":"r2","driver":"drv_b","sid":"s9","org":"org_2",'
        '"status":"route_set","intent":"go","stt":700,"llm_round_1":1200,"total":1900}',
        '2026-11-02 18:00:09,000 - truckbuddy - INFO - r3 - turn {"req":"r3","driver":"drv_a","sid":"s1","org":"org_1",'
        '"status":"not_understood","intent":"unclear","stt":600,"llm_round_1":800,"total":1400,"sid_extra":null}',
        '2026-11-02 18:00:10,000 - truckbuddy - INFO - r4 - turn {broken json',
    ]
    turns = turn_stats.parse([old, *new])
    assert len(turns) == 4 and turns[1]["driver"] == "drv_a" and turns[1]["stt"] == 900 and "sid_extra" not in turns[3]
    assert turns[0]["status"] == "route_set" and turns[0]["stt"] == 800  # the old format still reads
    s = turn_stats.summarize(turns)
    assert s["stages"]["stt"]["n"] == 4 and s["status"]["route_set"] == 3
    # pairs: drv_a s1 (go -> not_understood: a re-ask) and drv_b alone; the old line is its own dialog
    assert s["reask_rate"] == 1.0


def test_device_cache_expires(monkeypatch):
    """Another worker's cached device row is re-read after the TTL, so a bind made elsewhere is seen within a minute."""
    clock = [1000.0]
    monkeypatch.setattr(tenancy.time, "monotonic", lambda: clock[0])
    tenancy._devices["sub_x"] = (tenancy.Device("sub_x", "drv_old", "org_old", None, "driver"), clock[0])
    assert run(tenancy.device_of("sub_x")).org_id == "org_old"
    clock[0] += tenancy.DEVICE_TTL_S + 1
    assert run(tenancy.device_of("sub_x")) is None  # re-read: no such row in the store
    tenancy._devices.pop("sub_x", None)
