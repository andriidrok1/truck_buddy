"""Expired driver tokens inside the grace window (services/auth.py _claims/_grace): build 2 of the app never refreshes
and signs up as a new device on a 401, so the backend keeps the old token working while the device row is there."""
from datetime import datetime, timedelta, timezone

import jwt
from conftest import run

from services import auth, tenancy


def _client(sqlite_store, monkeypatch):
    from fastapi.testclient import TestClient
    import server
    monkeypatch.setenv("MEMORY_STORE", "sqlite")
    tenancy.reset_caches()
    auth._signups.clear()
    auth._attempts.clear()
    c = TestClient(server.app)
    c.__enter__()
    return c


def _decode(token: str) -> dict:
    return jwt.decode(token, auth._secret(), algorithms=["HS256"], options={"verify_exp": False})


def _aged(token: str, days_past_exp: float, secret: str | None = None) -> str:
    """The same claims, issued JWT_DAYS + days_past_exp ago (so it expired days_past_exp ago)."""
    claims = _decode(token)
    now = datetime.now(timezone.utc)
    iat = now - timedelta(days=auth.JWT_DAYS + days_past_exp)
    claims.update(iat=iat, exp=iat + timedelta(days=auth.JWT_DAYS))
    return jwt.encode(claims, secret or auth._secret(), algorithm="HS256")


def _get(c, path, token):
    return c.get(path, headers={"Authorization": f"Bearer {token}"})


def _device(c) -> dict:
    return c.post("/api/auth/device").json()


def test_an_expired_token_of_a_known_device_keeps_its_identity(sqlite_store, monkeypatch):
    c = _client(sqlite_store, monkeypatch)
    body = _device(c)
    sub = _decode(body["access_token"])["sub"]
    old = _aged(body["access_token"], 40)
    assert _get(c, "/api/auth/check", old).status_code == 200
    # the same driver: what it stored under the fresh token is there under the expired one
    h = {"Authorization": f"Bearer {body['access_token']}"}
    profile = {"height_in": 162, "weight_lbs": 80000, "length_ft": 70, "trailer_ft": 53, "axles": 5, "hazmat": False}
    assert c.put("/api/me/profile", json=profile, headers=h).status_code == 200
    assert _get(c, "/api/me/profile", old).json()["height_in"] == 162
    # renew trades it for a fresh pair on the same sub
    r = c.post("/api/auth/renew", headers={"Authorization": f"Bearer {old}"})
    assert r.status_code == 200 and r.json()["refresh_token"]
    assert _decode(r.json()["access_token"])["sub"] == sub
    assert _decode(r.json()["access_token"])["exp"] > datetime.now(timezone.utc).timestamp()
    c.__exit__(None, None, None)


def test_grace_refreshes_seen_at(sqlite_store, monkeypatch):
    from services import db
    c = _client(sqlite_store, monkeypatch)
    body = _device(c)
    sub = _decode(body["access_token"])["sub"]
    run(db._sq_run(lambda con: con.execute("UPDATE devices SET seen_at = '2025-01-01T00:00:00+00:00' WHERE sub = ?", (sub,))))
    assert _get(c, "/api/auth/check", _aged(body["access_token"], 5)).status_code == 200
    seen = run(db._sq_run(lambda con: con.execute("SELECT seen_at FROM devices WHERE sub = ?", (sub,)).fetchone()))[0]
    assert seen > "2026"
    c.__exit__(None, None, None)


def test_past_the_grace_window_is_401(sqlite_store, monkeypatch):
    c = _client(sqlite_store, monkeypatch)
    body = _device(c)
    assert _get(c, "/api/auth/check", _aged(body["access_token"], auth.GRACE_DAYS_DEFAULT + 1)).status_code == 401
    monkeypatch.setenv("TOKEN_GRACE_DAYS", "10")
    assert _get(c, "/api/auth/check", _aged(body["access_token"], 11)).status_code == 401
    assert _get(c, "/api/auth/check", _aged(body["access_token"], 9)).status_code == 200
    monkeypatch.setenv("TOKEN_GRACE_DAYS", "0")
    assert _get(c, "/api/auth/check", _aged(body["access_token"], 1)).status_code == 401
    c.__exit__(None, None, None)


def test_an_unknown_device_is_401_and_gets_no_row(sqlite_store, monkeypatch):
    c = _client(sqlite_store, monkeypatch)
    _device(c)  # the store is up
    stranger = _aged(auth._token("driver"), 3)  # signed by us, never seen by the store
    assert _get(c, "/api/auth/check", stranger).status_code == 401
    assert run(tenancy.device_of(_decode(stranger)["sub"])) is None
    c.__exit__(None, None, None)


def test_a_deleted_device_row_is_401(sqlite_store, monkeypatch):
    from services import db
    c = _client(sqlite_store, monkeypatch)
    body = _device(c)
    sub = _decode(body["access_token"])["sub"]
    run(db._sq_run(lambda con: con.execute("DELETE FROM devices WHERE sub = ?", (sub,))))
    tenancy.reset_caches()
    assert _get(c, "/api/auth/check", _aged(body["access_token"], 3)).status_code == 401
    c.__exit__(None, None, None)


def test_delete_my_data_ends_the_grace_for_older_tokens_only(sqlite_store, monkeypatch):
    c = _client(sqlite_store, monkeypatch)
    body = _device(c)
    old = _aged(body["access_token"], 3)
    assert _get(c, "/api/auth/check", old).status_code == 200
    h = {"Authorization": f"Bearer {body['access_token']}"}
    assert c.delete("/api/memory", headers=h).status_code == 200
    assert _get(c, "/api/auth/check", old).status_code == 401
    assert _get(c, "/api/auth/check", body["access_token"]).status_code == 200  # the token in use still works
    # a token issued after the deletion (refresh) gets its grace back later
    fresh = c.post("/api/auth/refresh", json={"refresh_token": body["refresh_token"]}).json()["access_token"]
    claims = _decode(fresh)
    claims["exp"] = int(datetime.now(timezone.utc).timestamp()) - 60
    assert _get(c, "/api/auth/check", jwt.encode(claims, auth._secret(), algorithm="HS256")).status_code == 200
    c.__exit__(None, None, None)


def test_tampered_and_foreign_tokens_are_401(sqlite_store, monkeypatch):
    c = _client(sqlite_store, monkeypatch)
    body = _device(c)
    old = _aged(body["access_token"], 3)
    head, payload, sig = old.split(".")
    flipped = sig[:-2] + ("AA" if sig[-2:] != "AA" else "BB")
    assert _get(c, "/api/auth/check", f"{head}.{payload}.{flipped}").status_code == 401
    assert _get(c, "/api/auth/check", _aged(body["access_token"], 3, secret="x" * 40)).status_code == 401  # rotated secret
    none_alg = jwt.encode(_decode(old), None, algorithm="none")
    assert _get(c, "/api/auth/check", none_alg).status_code == 401
    c.__exit__(None, None, None)


def test_tester_tokens_get_no_grace(sqlite_store, monkeypatch):
    monkeypatch.setenv("TESTER_CODE", "TESTCODE1")
    c = _client(sqlite_store, monkeypatch)
    body = c.post("/api/auth/access-code", json={"code": "testcode1"}).json()
    assert body["role"] == "tester"
    assert _get(c, "/api/auth/check", body["access_token"]).status_code == 200
    assert _get(c, "/api/auth/check", _aged(body["access_token"], 1)).status_code == 401
    c.__exit__(None, None, None)


def test_a_fleet_owner_gets_no_grace(sqlite_store, monkeypatch):
    from services import db
    c = _client(sqlite_store, monkeypatch)
    body = _device(c)
    sub = _decode(body["access_token"])["sub"]
    run(db._sq_run(lambda con: con.execute("UPDATE devices SET role = 'owner' WHERE sub = ?", (sub,))))
    tenancy.reset_caches()
    assert _get(c, "/api/auth/check", _aged(body["access_token"], 2)).status_code == 401  # row role
    claims = _decode(_aged(body["access_token"], 2))
    claims["role"] = "owner"
    assert _get(c, "/api/auth/check", jwt.encode(claims, auth._secret(), algorithm="HS256")).status_code == 401
    c.__exit__(None, None, None)


def test_kill_switch_cuts_older_expired_tokens(sqlite_store, monkeypatch):
    c = _client(sqlite_store, monkeypatch)
    body = _device(c)
    old = _aged(body["access_token"], 3)
    monkeypatch.setenv("TOKEN_GRACE_NOT_BEFORE", datetime.now(timezone.utc).date().isoformat())
    assert _get(c, "/api/auth/check", old).status_code == 401
    assert _get(c, "/api/auth/check", body["access_token"]).status_code == 200
    monkeypatch.setenv("TOKEN_GRACE_NOT_BEFORE", "garbage")
    assert _get(c, "/api/auth/check", old).status_code == 401
    c.__exit__(None, None, None)


def test_store_off_gives_no_grace(monkeypatch):
    from fastapi.testclient import TestClient
    import server
    monkeypatch.setenv("MEMORY_STORE", "off")
    tenancy.reset_caches()
    with TestClient(server.app) as c:
        token = _aged(auth._token("driver"), 3)
        assert _get(c, "/api/auth/check", token).status_code == 401


def test_a_store_error_is_503_not_401(sqlite_store, monkeypatch):
    c = _client(sqlite_store, monkeypatch)
    body = _device(c)
    tenancy.reset_caches()

    async def boom(_sub):
        raise RuntimeError("down")
    monkeypatch.setattr(tenancy, "grace_info", boom)
    assert _get(c, "/api/auth/check", _aged(body["access_token"], 3)).status_code == 503
    c.__exit__(None, None, None)
