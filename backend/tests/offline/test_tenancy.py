"""Organisations, fleet codes, refresh tokens and the stored truck profile (migration 0004, services/tenancy.py)."""
import json

from conftest import fixture_json, run

from models import TruckProfile
from services import auth, db, tenancy

DATA = fixture_json("here/route_sac_reno")
SAC = {"lat": 38.5816, "lng": -121.4944}


def _client(sqlite_store, monkeypatch):
    """A TestClient on the sqlite store with a fresh device token that carries tenancy claims."""
    from fastapi.testclient import TestClient
    import server
    monkeypatch.setenv("MEMORY_STORE", "sqlite")
    tenancy.reset_caches()
    auth._signups.clear()  # each test registers a device; the 20-per-IP sign-up window is for abuse, not for us
    c = TestClient(server.app)
    c.__enter__()
    body = c.post("/api/auth/device").json()
    c.headers.update({"Authorization": f"Bearer {body['access_token']}"})
    return c, body


def _claims(token: str) -> dict:
    import base64
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def test_a_new_device_gets_an_org_of_one_and_a_refresh_token(sqlite_store, monkeypatch):
    c, body = _client(sqlite_store, monkeypatch)
    claims = _claims(body["access_token"])
    assert claims["org_id"].startswith("org_") and claims["driver_id"].startswith("drv_") and "vehicle_id" not in claims
    assert body["refresh_token"] and body["expires_in"] == auth.JWT_DAYS * 86400
    dev = run(tenancy.device_of(claims["sub"]))
    assert dev.org_id == claims["org_id"] and dev.vehicle_id is None and dev.role == "driver"
    # idempotent: the same sub never gets a second org
    tenancy.reset_caches()
    again = run(tenancy.ensure_solo_org(claims["sub"]))
    assert again.org_id == dev.org_id
    c.__exit__(None, None, None)


def test_refresh_rotates_and_the_old_token_dies(sqlite_store, monkeypatch):
    c, body = _client(sqlite_store, monkeypatch)
    r = c.post("/api/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert r.status_code == 200 and r.json()["refresh_token"] != body["refresh_token"]
    assert _claims(r.json()["access_token"])["sub"] == _claims(body["access_token"])["sub"]
    assert c.post("/api/auth/refresh", json={"refresh_token": body["refresh_token"]}).status_code == 401
    assert c.post("/api/auth/refresh", json={"refresh_token": "x" * 40}).status_code == 401
    c.__exit__(None, None, None)


def test_a_legacy_token_without_claims_still_works_and_gets_an_org(sqlite_store, monkeypatch):
    c, _ = _client(sqlite_store, monkeypatch)
    legacy = auth._token("driver")  # the 2026-10 shape: sub, role, iat, exp
    r = c.get("/api/auth/check", headers={"Authorization": f"Bearer {legacy}"})
    assert r.status_code == 200
    assert run(tenancy.device_of(_claims(legacy)["sub"])).org_id.startswith("org_")
    c.__exit__(None, None, None)


def test_fleet_code_binds_the_device_to_the_fleet_and_its_truck(sqlite_store, monkeypatch):
    c, body = _client(sqlite_store, monkeypatch)
    sub = _claims(body["access_token"])["sub"]
    org = run(tenancy.create_org("Acme Haul"))
    truck = run(tenancy.create_vehicle(org, "Unit 12", TruckProfile(height_in=169, trailer_ft=48)))
    code = run(tenancy.create_fleet_code(org, truck))
    assert c.post("/api/auth/fleet-code", json={"code": "NOPE1234"}).status_code == 401
    r = c.post("/api/auth/fleet-code", json={"code": code.lower()})
    assert r.status_code == 200 and r.json()["org_id"] == org and r.json()["vehicle_id"] == truck
    claims = _claims(r.json()["access_token"])
    assert claims["org_id"] == org and claims["vehicle_id"] == truck
    # the code is spent; the driver row moved into the fleet; the old token resolves to the new org too
    assert c.post("/api/auth/fleet-code", json={"code": code}).status_code == 401
    dev = run(tenancy.device_of(sub))
    assert dev.org_id == org and dev.vehicle_id == truck
    assert run(tenancy.org_members(org)) == [sub]
    # the vehicle's profile is what the server routes for when the app sends the default
    assert run(tenancy.effective_profile(sub, TruckProfile())).height_in == 169
    assert run(tenancy.effective_profile(sub, TruckProfile(height_in=160))).height_in == 160  # the app's own wins
    c.__exit__(None, None, None)


def test_profile_endpoint_round_trip_and_the_body_override(sqlite_store, monkeypatch, fake_http):
    c, body = _client(sqlite_store, monkeypatch)
    assert c.get("/api/me/profile").status_code == 404
    r = c.put("/api/me/profile", json={"height_in": 170, "trailer_ft": 53, "weight_lb": 80000, "hazmat": False})
    assert r.status_code == 200 and r.json()["height_in"] == 170
    assert c.get("/api/me/profile").json()["height_in"] == 170
    # /route with the default profile in the body routes for the stored 170 (HERE gets 432 cm)
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    place = {"id": "here-reno", "name": "Reno, NV", "category": "destination", "lat": 39.5296, "lng": -119.8138}
    assert c.post("/api/route", json={**SAC, "place": place, "skip_parking": True}).status_code == 200
    from conftest import params_of
    assert dict(params_of(fake_http.calls_to("router.hereapi.com")[-1]))["vehicle[height]"] == "432"
    # a non-default body wins
    short = {"height_in": 150, "trailer_ft": 53, "weight_lb": 80000, "hazmat": False}
    assert c.post("/api/route", json={**SAC, "place": place, "skip_parking": True, "truck_profile": short}).status_code == 200
    assert dict(params_of(fake_http.calls_to("router.hereapi.com")[-1]))["vehicle[height]"] == "381"
    c.__exit__(None, None, None)


def test_store_off_keeps_the_old_behaviour(api_client):
    body = api_client.post("/api/auth/device").json()
    assert "refresh_token" not in body and "org_id" not in _claims(body["access_token"])
    assert api_client.get("/api/me/profile").status_code == 503 or api_client.get("/api/me/profile").status_code == 404
    assert run(tenancy.ensure_solo_org("drv_x")) is None


def test_org_member_dependency(sqlite_store, monkeypatch):
    c, body = _client(sqlite_store, monkeypatch)
    sub = _claims(body["access_token"])["sub"]
    dev = run(tenancy.device_of(sub))
    p = run(auth._principal(_claims(body["access_token"])))
    assert str(p) == sub and p.org_id == dev.org_id and p.role == "driver"
    assert f"{p}:solo" == f"{sub}:solo"  # str semantics for session ids and DB keys
    c.__exit__(None, None, None)


def test_credentials_round_trip(sqlite_store, monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("CREDENTIALS_KEY", Fernet.generate_key().decode())
    c, body = _client(sqlite_store, monkeypatch)
    org = _claims(body["access_token"])["org_id"]
    run(tenancy.credential_put(org, "motive", "mk_secret_123"))
    tenancy.reset_caches()
    assert run(tenancy.credential_get(org, "motive")) == "mk_secret_123"
    assert run(tenancy.credential_get(org, "other")) is None
    row = run(db._sq_run(lambda con: con.execute("SELECT secret_enc FROM org_credentials WHERE org_id = ?", (org,)).fetchone()))
    assert "mk_secret_123" not in row[0]
    monkeypatch.delenv("CREDENTIALS_KEY")
    tenancy.reset_caches()
    assert run(tenancy.credential_get(org, "motive")) is None  # no key, no plaintext, no crash
    c.__exit__(None, None, None)
