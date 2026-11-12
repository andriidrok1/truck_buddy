"""Deleting an account (DELETE /api/account) with the Apple token revocation, clearing the history only
(DELETE /api/memory/history) and a dialog per signed-in phone.

Same harness as test_accounts.py: a test RSA key behind a stubbed JWKS, Apple's token and revoke endpoints stubbed,
every test on SQLite and, with TEST_DATABASE_URL, on Postgres."""
import asyncio
import hashlib
import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from conftest import run
from models import AgentRequest, AgentResponse, TruckProfile
from services import accounts, agent, apple, auth, breakdown, db, kv, memory, secrets as sec, session_store, tenancy, trip_events
from services.dialog_state import DialogState
import test_accounts
from test_accounts import APPLE_HOST, BUNDLE, GONE, PROFILE, _apple, _claims, _fleet_code, _google, _h, _person, _rows

# The fixtures of test_accounts.py, under the same names (pytest finds a fixture by the module attribute).
client, jwks, store = test_accounts.client, test_accounts.jwks, test_accounts.store

TEAM, KEY_ID = "SDAGV82ZXL", "ABC123DEFG"
APPLE_KEY = ec.generate_private_key(ec.SECP256R1())  # the team's Sign in with Apple key (.p8)
APPLE_PEM = APPLE_KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()).decode()
CODE = "c0de-" + "x" * 50  # the authorization code the phone got with its identity token
REFRESH = "r.apple-refresh-token-0123456789"
SAC = {"lat": 38.58, "lng": -121.49}


class Apple:
    """Apple's /auth/token and /auth/revoke, stubbed: records the form bodies, answers what the test set."""

    def __init__(self, fake_http):
        self.exchanges, self.revocations = [], []
        self.token_reply = lambda request: httpx.Response(200, request=request, json={
            "access_token": "a.apple-access", "refresh_token": REFRESH, "id_token": "i.d", "token_type": "Bearer", "expires_in": 3600})
        self.revoke_reply = lambda request: httpx.Response(200, request=request)
        fake_http.add(APPLE_HOST, "/auth/token", fn=lambda request: self._seen(self.exchanges, request, self.token_reply))
        fake_http.add(APPLE_HOST, "/auth/revoke", fn=lambda request: self._seen(self.revocations, request, self.revoke_reply))

    @staticmethod
    def _seen(into: list, request: httpx.Request, reply):
        assert request.method == "POST" and request.headers["content-type"] == "application/x-www-form-urlencoded"
        into.append({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
        return reply(request)


@pytest.fixture
def apple_api(client, monkeypatch):
    """The Sign in with Apple key and CREDENTIALS_KEY configured (the `\\n` form a one-line env value has), and
    Apple's two endpoints stubbed."""
    monkeypatch.setenv("APPLE_TEAM_ID", TEAM)
    monkeypatch.setenv("APPLE_KEY_ID", KEY_ID)
    monkeypatch.setenv("APPLE_PRIVATE_KEY", APPLE_PEM.replace("\n", "\\n"))
    monkeypatch.setenv("CREDENTIALS_KEY", Fernet.generate_key().decode())
    return Apple(jwks_http(client))


def jwks_http(client):
    """The FakeHttp router behind the shared client (the `jwks` fixture put its routes on it)."""
    from services import http
    return http._client._transport.handler


def _secret_claims(secret: str) -> dict:
    """The client secret, checked the way Apple checks it: with the public half of the team's key."""
    assert jwt.get_unverified_header(secret) == {"alg": "ES256", "kid": KEY_ID, "typ": "JWT"}
    return jwt.decode(secret, APPLE_KEY.public_key(), algorithms=["ES256"], audience="https://appleid.apple.com")


def _settle(client) -> None:
    client.portal.call(apple.settle)


def _sealed(client, sub: str):
    return _rows(client, "SELECT apple_refresh_enc FROM accounts WHERE sub = ?", sub)[0][0]


SECRETS = (CODE, REFRESH, "a.apple-access")


def _no_secrets_in(caplog, *more: str) -> None:
    """Nothing a log reader could sign in or revoke with: the code, Apple's tokens, a client secret, the key."""
    key_lines = [line for line in APPLE_PEM.splitlines() if line and not line.startswith("-----")]
    for secret in (*SECRETS, *key_lines, *more):
        assert secret not in caplog.text, f"{secret[:12]}… is in the log"


# ---- what a deletion has to remove ------------------------------------------------------------------------------

def _migration_tables(dialect: str) -> set[str]:
    """Every table the migrations of this store create."""
    names: set[str] = set()
    for _, path in db.migration_files("sqlite" if dialect == "sqlite" else "pg"):
        names.update(re.findall(r"CREATE TABLE(?: IF NOT EXISTS)? (\w+)", path.read_text()))
    return names


def _holding(client, needles: dict[str, str]) -> dict[str, set[str]]:
    """table -> the names of the needles found anywhere in it. Reads every row of every table the migrations
    create and looks for each needle in every value, so a table added later is covered without being listed here."""
    found: dict[str, set[str]] = {}
    tables = _migration_tables(client.store)
    assert {"accounts", "account_sessions", "conversation", "devices", "jobs"} <= tables  # the parse found them
    for table in sorted(tables):
        for row in _rows(client, f"SELECT * FROM {table}"):
            text = " | ".join(str(v) for v in row)
            for name, needle in needles.items():
                if needle in text:
                    found.setdefault(table, set()).add(name)
    return found


def _fill(client, body: dict, name: str = "Sam Driver") -> dict[str, str]:
    """Puts something of the driver into every place the app can: profile and name, turns, facts, a visit, trip
    events, a breakdown job, a dialog on this phone. Returns the needles to look for afterwards."""
    claims = _claims(body["access_token"])
    sub = claims["sub"]
    assert client.put("/api/me/profile", json={**PROFILE, "driver_name": name}, headers=_h(body)).status_code == 200

    async def fill():
        now = datetime.now(timezone.utc)
        await memory.add(sub, [("driver", "find me diesel"), ("copilot", "Pilot is 4 miles ahead.")])
        await memory.set_facts(sub, {"chains_liked": ["Pilot"]}, 2)
        await trip_events.add(sub, [{"kind": "arrived", "trip_id": "trip-1", "at": now.isoformat(),
                                     "data": {"place_id": "pl-1", "name": "Pilot Fernley", "category": "truck_stop"}}])
        await memory.add_visits(sub, [("pl-2", "Love's Reno", "truck_stop", now, "trip-2")])
        await kv.job_put(f"rid-{uuid.uuid4().hex}", sub, "breakdown", {"status": "researching"})
        state = DialogState()
        state["driver"] = sub
        principal = auth.Principal(sub)
        principal.sid = claims.get("sid")
        await session_store.get_store().save(session_store.dialog_key(principal, "dlg-1731400000000-abc123"), state)
    client.portal.call(fill)
    return {"sub": sub, "driver_id": claims["driver_id"]}


DATA_TABLES = {"conversation", "driver_facts", "driver_visits", "driver_visit_trips", "trip_events", "jobs", "dialog_sessions"}


@pytest.fixture
def db_dialogs(client, monkeypatch):
    monkeypatch.setenv("SESSION_STORE", "db")  # dialogs in the store, as on prod


def test_deleting_a_solo_account_leaves_nothing_of_the_driver(client, db_dialogs):
    who = _person()
    a = _apple(client, who, name="Sam Driver", platform="ios").json()
    b = _apple(client, who, platform="ios").json()  # a second phone of the same account
    claims = _claims(a["access_token"])
    needles = {**_fill(client, a), "org_id": claims["org_id"], "provider_sub": who, "sid_a": claims["sid"],
               "sid_b": _claims(b["access_token"])["sid"], "email": f"{who}@privaterelay.appleid.com"}
    before = _holding(client, needles)
    # the scan is not blind: it sees the driver in the account, tenancy and data tables, and in each sub column
    assert DATA_TABLES | {"accounts", "account_sessions", "devices", "drivers", "orgs"} <= set(before)
    assert {table for table, _ in accounts.SUB_COLUMNS} <= {t for t, names in before.items() if "sub" in names}

    r = client.delete("/api/account", headers=_h(a))
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "deleted": 2, "trip_events": 1, "profile": True, "sessions": 2, "apple_revoked": None}
    assert _holding(client, needles) == {}

    # both phones are out, and their refresh tokens are worth nothing
    for phone in (a, b):
        r = client.get("/api/auth/check", headers=_h(phone))
        assert r.status_code == 401 and r.json() == {"detail": GONE}
        assert client.post("/api/auth/refresh", json={"refresh_token": phone["refresh_token"]}).status_code == 401
    assert client.delete("/api/account", headers=_h(a)).status_code == 401
    assert _holding(client, needles) == {}  # being refused put nothing back

    # the same Apple identity signs in to a new, empty account
    again = _apple(client, who).json()
    assert again["new_account"] is True and again["has_profile"] is False and again["name"] is None
    assert _claims(again["access_token"])["sub"] != needles["sub"]
    assert client.get("/api/memory", headers=_h(again)).json() == {"turns": []}
    assert client.get("/api/me/profile", headers=_h(again)).status_code == 404


def test_deleting_a_fleet_drivers_account_leaves_the_fleet_whole(client, db_dialogs):
    who = _person()
    a = _google(client, who).json()
    solo_org = _claims(a["access_token"])["org_id"]
    code = _fleet_code(client)
    bound = client.post("/api/auth/fleet-code", json={"code": code}, headers=_h(a)).json()
    fleet, truck = bound["org_id"], bound["vehicle_id"]
    assert fleet != solo_org
    _rows(client, "UPDATE vehicles SET profile = ? WHERE id = ?", json.dumps(PROFILE), truck)
    name = f"Sam {who[-12:]}"  # the store may be a Postgres other runs left rows in: every needle is this test's own
    needles = {**_fill(client, bound, name=name), "solo_org": solo_org, "provider_sub": who,
               "email": f"{who}@gmail.com", "name": name}
    # a colleague on the same truck, to be left alone
    other = client.post("/api/auth/device").json()
    code2 = client.portal.call(tenancy.create_fleet_code, fleet, truck)
    other = client.post("/api/auth/fleet-code", json={"code": code2}, headers=_h(other)).json()
    other_sub = _claims(other["access_token"])["sub"]
    client.portal.call(memory.add, other_sub, [("driver", "where is the scale"), ("copilot", "Two miles ahead.")])
    assert "solo_org" not in {n for names in _holding(client, needles).values() for n in names}  # gone at the bind

    r = client.delete("/api/account", headers=_h(bound))
    assert r.status_code == 200, r.text
    assert r.json()["sessions"] == 1 and r.json()["apple_revoked"] is None  # a Google account: nothing at Apple
    assert _holding(client, needles) == {}

    # the fleet: its org, its truck with the profile, the colleague and their memory
    assert _rows(client, "SELECT solo FROM orgs WHERE id = ?", fleet) == [(0,)]
    assert json.loads(_rows(client, "SELECT profile FROM vehicles WHERE id = ?", truck)[0][0])["height_in"] == 162
    assert _rows(client, "SELECT org_id, vehicle_id FROM devices WHERE sub = ?", other_sub) == [(fleet, truck)]
    assert len(client.get("/api/memory", headers=_h(other)).json()["turns"]) == 2
    # the code stays spent, without the sub of who spent it: it cannot bind anybody else to the fleet
    used = _rows(client, "SELECT used_by, used_at FROM fleet_codes WHERE code = ?", code)
    assert used[0][0] == accounts.CODE_USER_GONE and used[0][1] is not None
    newcomer = _google(client, _person()).json()
    assert client.post("/api/auth/fleet-code", json={"code": code}, headers=_h(newcomer)).status_code == 401


def test_a_phone_on_another_worker_is_refused_within_the_cache_minute(client, monkeypatch):
    """The other worker still holds the session and the device in its caches when the account goes."""
    who = _person()
    a, b = _apple(client, who).json(), _apple(client, who).json()
    sub = _claims(a["access_token"])["sub"]
    assert client.get("/api/auth/check", headers=_h(b)).status_code == 200  # phone b is in the caches
    stale = dict(accounts._sessions), dict(tenancy._devices)
    assert client.delete("/api/account", headers=_h(a)).status_code == 200
    accounts._sessions.update(stale[0])  # as the worker that did not run the deletion sees it
    tenancy._devices.update(stale[1])
    clock = {"now": time.monotonic()}
    monkeypatch.setattr(accounts, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    clock["now"] += accounts.SESSION_TTL_S - 2
    assert client.get("/api/auth/check", headers=_h(b)).status_code == 200
    r = client.post("/api/auth/renew", headers=_h(b))  # but nothing new is issued for a session that is gone
    assert r.status_code == 401 and r.json() == {"detail": GONE}
    clock["now"] += 3
    r = client.get("/api/auth/check", headers=_h(b))
    assert r.status_code == 401 and r.json() == {"detail": GONE}
    for table, column in (("devices", "sub"), ("account_sessions", "sub"), ("accounts", "sub")):
        assert _rows(client, f"SELECT count(*) FROM {table} WHERE {column} = ?", sub) == [(0,)]  # the stale minute made no rows


def test_a_deletion_that_fails_halfway_can_be_repeated_with_the_same_token(client, monkeypatch, db_dialogs):
    who = _person()
    a = _apple(client, who).json()
    needles = _fill(client, a)
    sub = needles["sub"]
    steps = accounts._erase_steps
    # the last statement of the transaction fails: everything in it is rolled back
    monkeypatch.setattr(accounts, "_erase_steps", lambda *args: [*steps(*args), ("DELETE FROM no_such_table WHERE sub = ?", (sub,))])
    r = client.delete("/api/account", headers=_h(a))
    assert r.status_code == 503 and r.json() == {"detail": "The account could not be deleted right now. Try again."}
    for table, column in (("accounts", "sub"), ("account_sessions", "sub"), ("devices", "sub")):
        assert _rows(client, f"SELECT count(*) FROM {table} WHERE {column} = ?", sub) == [(1,)], table
    for table in ("jobs", "conversation", "trip_events", "dialog_sessions"):  # all of it was in the transaction
        assert _rows(client, f"SELECT count(*) FROM {table} WHERE driver = ?", sub) == [(1 if table != "conversation" else 2,)], table
    assert client.get("/api/me/profile", headers=_h(a)).json()["height_in"] == 162
    assert client.get("/api/auth/check", headers=_h(a)).status_code == 200  # still signed in, so it can ask again
    monkeypatch.setattr(accounts, "_erase_steps", steps)
    r = client.delete("/api/account", headers=_h(a))
    assert r.status_code == 200 and r.json()["deleted"] == 2 and r.json()["sessions"] == 1
    assert _holding(client, needles) == {}


def test_a_device_token_has_no_account_to_delete(client):
    for token in (client.post("/api/auth/device").json(), client.post("/api/auth/renew", headers=_h(client.post("/api/auth/device").json())).json()):
        sub = _claims(token["access_token"])["sub"]
        client.portal.call(memory.add, sub, [("driver", "hi"), ("copilot", "Hello.")])
        r = client.delete("/api/account", headers=_h(token))
        assert r.status_code == 409
        assert r.json() == {"detail": "This sign-in has no account. Use DELETE /api/memory to delete its data.", "code": "not_an_account"}
        assert len(client.get("/api/memory", headers=_h(token)).json()["turns"]) == 2  # nothing was touched
        assert client.delete("/api/memory", headers=_h(token)).json() == {"deleted": 2, "trip_events": 0, "profile": False}


def test_an_access_code_token_has_no_account_either(client, monkeypatch):
    monkeypatch.setenv("ACCESS_CODE", "ROADTEST")
    token = client.post("/api/auth/access-code", json={"code": "roadtest"}).json()
    assert client.delete("/api/account", headers=_h(token)).status_code == 409


def test_delete_account_needs_a_token(client):
    assert client.delete("/api/account").status_code == 401


def test_the_loser_of_a_first_contact_race_leaves_no_org_or_driver_rows(client, monkeypatch):
    """Two first requests for one sub both make an org and a driver row; only one device row wins. The other's rows
    carry no sub, so a deletion could never find them: they are taken back on the spot."""
    made: list[str] = []
    new_id = tenancy.new_id
    monkeypatch.setattr(tenancy, "new_id", lambda prefix: made.append(new_id(prefix)) or made[-1])
    sub = auth.new_sub()

    async def race():
        return await asyncio.gather(tenancy.ensure_solo_org(sub), tenancy.ensure_solo_org(sub))
    first, second = client.portal.call(race)
    assert first == second
    orgs, drivers = [i for i in made if i.startswith("org_")], [i for i in made if i.startswith("drv_")]
    assert len(orgs) == 2 and len(drivers) == 2  # both ran the creation
    kept = [i for i in orgs if _rows(client, "SELECT 1 FROM orgs WHERE id = ?", i)]
    assert kept == [first.org_id]
    assert [i for i in drivers if _rows(client, "SELECT 1 FROM drivers WHERE id = ?", i)] == [first.driver_id]


# ---- Apple: the refresh token is kept at sign-in and revoked at deletion ------------------------------------------

def test_apple_sign_in_trades_the_code_for_a_refresh_token_and_keeps_it_encrypted(client, apple_api, caplog):
    caplog.set_level(logging.DEBUG)
    who = _person()
    r = _apple(client, who, authorization_code=CODE)
    assert r.status_code == 200, r.text
    sub = _claims(r.json()["access_token"])["sub"]
    _settle(client)
    assert len(apple_api.exchanges) == 1
    form = apple_api.exchanges[0]
    assert set(form) == {"grant_type", "code", "client_id", "client_secret"}
    assert form["grant_type"] == "authorization_code" and form["code"] == CODE and form["client_id"] == BUNDLE
    claims = _secret_claims(form["client_secret"])
    assert claims["iss"] == TEAM and claims["sub"] == BUNDLE and claims["aud"] == "https://appleid.apple.com"
    assert abs(claims["iat"] - time.time()) < 60 and 0 < claims["exp"] - claims["iat"] <= 10 * 60  # Apple's cap is 6 months
    sealed = _sealed(client, sub)
    assert sealed and REFRESH not in sealed
    assert json.loads(sec.decrypt(sealed)) == {"token": REFRESH, "client_id": BUNDLE}
    _no_secrets_in(caplog, form["client_secret"], sealed)


def test_the_sign_in_is_answered_before_apple_answers_the_exchange(client, apple_api):
    release = asyncio.Event()

    async def slow(request):
        await release.wait()
        return httpx.Response(200, request=request, json={"refresh_token": REFRESH})
    apple_api.token_reply = slow
    who = _person()
    r = _apple(client, who, authorization_code=CODE)  # returns while Apple has not answered
    assert r.status_code == 200 and len(apple._tasks) == 1
    sub = _claims(r.json()["access_token"])["sub"]
    assert _sealed(client, sub) is None
    client.portal.call(release.set)
    _settle(client)
    assert json.loads(sec.decrypt(_sealed(client, sub)))["token"] == REFRESH


def _refused(request):
    return httpx.Response(400, request=request, json={"error": "invalid_grant", "error_description": f"code {CODE} was used"})


def _network_down(request):
    # the message carries the whole form, as a transport error may: nothing of it is to reach the log
    raise httpx.ConnectError(f"no route to appleid.apple.com for {request.content.decode()}")


def _no_refresh_token(request):
    return httpx.Response(200, request=request, json={"access_token": "a.apple-access"})


def _not_json(request):
    return httpx.Response(200, request=request, text="<html>maintenance</html>")


@pytest.mark.parametrize("reply, logged", [(_refused, "apple code exchange refused: 400 invalid_grant"),
                                           (_network_down, "apple code exchange failed: ConnectError"),
                                           (_no_refresh_token, "apple code exchange refused: 200 -"),
                                           (_not_json, "apple code exchange failed: JSONDecodeError")])
def test_a_failed_exchange_does_not_fail_the_sign_in(client, apple_api, caplog, reply, logged):
    caplog.set_level(logging.DEBUG)
    apple_api.token_reply = reply
    who = _person()
    r = _apple(client, who, authorization_code=CODE)
    assert r.status_code == 200 and r.json()["new_account"] is True
    _settle(client)
    assert _sealed(client, _claims(r.json()["access_token"])["sub"]) is None
    assert logged in caplog.text
    _no_secrets_in(caplog, apple_api.exchanges[0]["client_secret"] if apple_api.exchanges else CODE)
    assert client.get("/api/auth/check", headers=_h(r.json())).status_code == 200


def test_a_key_that_does_not_load_does_not_fail_the_sign_in(client, apple_api, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("APPLE_PRIVATE_KEY", "-----BEGIN PRIVATE KEY-----\\nbm90IGEga2V5\\n-----END PRIVATE KEY-----")
    r = _apple(client, _person(), authorization_code=CODE)
    assert r.status_code == 200
    _settle(client)
    assert apple_api.exchanges == [] and "apple code exchange failed" in caplog.text
    assert "bm90IGEga2V5" not in caplog.text and CODE not in caplog.text


@pytest.mark.parametrize("unset", ["APPLE_TEAM_ID", "APPLE_KEY_ID", "APPLE_PRIVATE_KEY", "CREDENTIALS_KEY"])
def test_without_the_key_the_sign_in_works_and_nothing_is_exchanged_or_revoked(client, apple_api, monkeypatch, caplog, unset):
    monkeypatch.delenv(unset)
    who = _person()
    r = _apple(client, who, authorization_code=CODE)
    assert r.status_code == 200
    sub = _claims(r.json()["access_token"])["sub"]
    _settle(client)
    assert apple_api.exchanges == [] and _sealed(client, sub) is None
    warnings = [rec.getMessage() for rec in caplog.records if rec.name == "truckbuddy.apple"]
    assert warnings == [f"apple refresh token not kept for {sub}: {unset} not set, deleting this account will not revoke it at Apple"]
    r = client.delete("/api/account", headers=_h(r.json()))
    assert r.status_code == 200 and r.json()["apple_revoked"] is None and apple_api.revocations == []
    assert _rows(client, "SELECT count(*) FROM accounts WHERE sub = ?", sub) == [(0,)]


def test_a_sign_in_without_a_code_asks_apple_nothing(client, apple_api, caplog):
    assert _apple(client, _person()).status_code == 200
    _settle(client)
    assert apple_api.exchanges == [] and not [rec for rec in caplog.records if rec.name == "truckbuddy.apple"]


def test_google_sign_in_never_talks_to_apples_token_endpoint(client, apple_api):
    g = _google(client, _person()).json()
    _settle(client)
    r = client.delete("/api/account", headers=_h(g))
    assert r.json()["apple_revoked"] is None and apple_api.exchanges == [] and apple_api.revocations == []


def _signed_in_with_token(client, who: str) -> dict:
    body = _apple(client, who, authorization_code=CODE).json()
    _settle(client)
    assert _sealed(client, _claims(body["access_token"])["sub"])
    return body


def test_deleting_an_apple_account_revokes_its_token(client, apple_api, caplog):
    caplog.set_level(logging.DEBUG)
    who = _person()
    a = _signed_in_with_token(client, who)
    sub = _claims(a["access_token"])["sub"]
    sealed = _sealed(client, sub)
    r = client.delete("/api/account", headers=_h(a))
    assert r.status_code == 200 and r.json()["apple_revoked"] is True
    assert len(apple_api.revocations) == 1
    form = apple_api.revocations[0]
    assert set(form) == {"token", "token_type_hint", "client_id", "client_secret"}
    assert form["token"] == REFRESH and form["token_type_hint"] == "refresh_token" and form["client_id"] == BUNDLE
    claims = _secret_claims(form["client_secret"])
    assert claims["iss"] == TEAM and claims["sub"] == BUNDLE and 0 < claims["exp"] - claims["iat"] <= 10 * 60
    assert _rows(client, "SELECT count(*) FROM accounts WHERE sub = ?", sub) == [(0,)]
    _no_secrets_in(caplog, form["client_secret"], apple_api.exchanges[0]["client_secret"], sealed)


def test_signing_in_again_does_not_exchange_again_unless_the_stored_token_is_unreadable(client, apple_api, monkeypatch):
    who = _person()
    a = _signed_in_with_token(client, who)
    apple_api.token_reply = lambda request: httpx.Response(200, request=request, json={"refresh_token": "r.newer"})
    _apple(client, who, authorization_code=CODE)
    _settle(client)
    assert len(apple_api.exchanges) == 1  # the account holds a token for this client: Apple is not asked
    monkeypatch.setenv("CREDENTIALS_KEY", Fernet.generate_key().decode())  # rotated: the stored token no longer opens
    _apple(client, who, authorization_code=CODE)
    _settle(client)
    assert len(apple_api.exchanges) == 2
    client.delete("/api/account", headers=_h(a))
    assert [f["token"] for f in apple_api.revocations] == ["r.newer"]


def _revoke_refused(request):
    return httpx.Response(400, request=request, json={"error": "invalid_client"})


@pytest.mark.parametrize("reply, logged", [(_revoke_refused, "apple revoke refused for {sub}: 400 invalid_client"),
                                           (_network_down, "apple revoke failed for {sub}: ConnectError")])
def test_a_failed_revocation_is_reported_and_the_account_is_still_deleted(client, apple_api, caplog, reply, logged):
    caplog.set_level(logging.DEBUG)
    who = _person()
    a = _signed_in_with_token(client, who)
    sub = _claims(a["access_token"])["sub"]
    apple_api.revoke_reply = reply
    r = client.delete("/api/account", headers=_h(a))
    assert r.status_code == 200 and r.json()["apple_revoked"] is False
    assert logged.format(sub=sub) in caplog.text
    assert _rows(client, "SELECT count(*) FROM accounts WHERE sub = ?", sub) == [(0,)]
    assert client.get("/api/auth/check", headers=_h(a)).status_code == 401
    _no_secrets_in(caplog)


def test_a_stored_token_that_no_longer_opens_is_a_failed_revocation(client, apple_api, monkeypatch, caplog):
    a = _signed_in_with_token(client, _person())
    monkeypatch.setenv("CREDENTIALS_KEY", Fernet.generate_key().decode())  # the key was rotated since
    r = client.delete("/api/account", headers=_h(a))
    assert r.status_code == 200 and r.json()["apple_revoked"] is False and apple_api.revocations == []
    assert "apple revoke failed" in caplog.text and "NoCredentialsKey" in caplog.text


def test_an_apple_account_without_a_stored_token_reports_null(client, apple_api, caplog):
    a = _apple(client, _person()).json()  # the app sent no code
    r = client.delete("/api/account", headers=_h(a))
    assert r.status_code == 200 and r.json()["apple_revoked"] is None and apple_api.revocations == []
    assert "has no stored refresh token" in caplog.text


# ---- DELETE /api/memory/history ---------------------------------------------------------------------------------

def test_clearing_the_history_keeps_the_profile_the_trips_and_the_visits(client, db_dialogs):
    a = _google(client, _person()).json()
    sub = _fill(client, a)["sub"]
    r = client.delete("/api/memory/history", headers=_h(a))
    assert r.status_code == 200 and r.json() == {"deleted": 2, "facts": True}
    # what was said, what was learned from it, and the thread
    assert client.get("/api/memory", headers=_h(a)).json() == {"turns": []}
    assert client.portal.call(memory.get_facts, sub) is None
    assert _rows(client, "SELECT count(*) FROM dialog_sessions WHERE driver = ?", sub) == [(0,)]
    # the rest stays
    profile = client.get("/api/me/profile", headers=_h(a)).json()
    assert profile["height_in"] == 162 and profile["driver_name"] == "Sam Driver"
    assert _rows(client, "SELECT kind FROM trip_events WHERE driver = ?", sub) == [("arrived",)]
    visits = client.portal.call(memory.visits, sub)
    assert sorted(v["place_id"] for v in visits) == ["pl-1", "pl-2"]
    assert _rows(client, "SELECT count(*) FROM driver_visit_trips WHERE driver = ?", sub) == [(2,)]
    assert client.get("/api/auth/check", headers=_h(a)).status_code == 200
    assert client.delete("/api/memory/history", headers=_h(a)).json() == {"deleted": 0, "facts": False}  # nothing left to clear


def test_clear_history_works_for_a_device_token_and_delete_memory_is_unchanged(client, db_dialogs):
    d = client.post("/api/auth/device").json()
    sub = _fill(client, d)["sub"]
    assert client.delete("/api/memory/history", headers=_h(d)).json() == {"deleted": 2, "facts": True}
    assert client.get("/api/me/profile", headers=_h(d)).status_code == 200
    # "Delete my data" of build 2: the same answer as before, and the device stays registered
    assert client.delete("/api/memory", headers=_h(d)).json() == {"deleted": 0, "trip_events": 1, "profile": True}
    assert client.portal.call(memory.visits, sub) == []
    assert client.get("/api/me/profile", headers=_h(d)).status_code == 404
    assert client.get("/api/auth/check", headers=_h(d)).status_code == 200


def test_clear_history_without_a_store_is_503(client, monkeypatch):
    d = client.post("/api/auth/device").json()
    monkeypatch.setenv("MEMORY_STORE", "off")
    r = client.delete("/api/memory/history", headers=_h(d))
    assert r.status_code == 503 and r.json() == {"detail": "Memory is unavailable right now"}


# ---- a dialog per signed-in phone ---------------------------------------------------------------------------------

def _principal(sub: str, sid: str | None = None) -> auth.Principal:
    p = auth.Principal(sub)
    p.sid = sid
    return p


def test_a_device_token_keeps_the_dialog_key_it_always_had():
    sub = auth.new_sub()
    app_id = "dlg-1731400000000-abc123"
    assert session_store.dialog_key(_principal(sub), app_id) == f"{sub}:{app_id}"[:64] == f"{sub}:{app_id}"
    assert session_store.dialog_key(_principal(sub), None) == f"{sub}:solo"
    assert session_store.dialog_key(sub, "x" * 64) == f"{sub}:{'x' * 64}"[:64]  # a plain str, cut as before


def test_an_account_token_gets_its_session_in_the_dialog_key():
    sub = auth.new_sub()
    a, b = _principal(sub, "ses_0123456789abcdef0123"), _principal(sub, "ses_fedcba98765432100000")
    assert session_store.dialog_key(a, None) == f"{sub}:01234567:solo"
    assert session_store.dialog_key(b, None) == f"{sub}:fedcba98:solo"
    assert session_store.dialog_key(a, "trip") == f"{sub}:01234567:trip"
    # the app's ids are longer than the 18 characters left: they are hashed whole, not cut to a shared head
    one, two = "dlg-1731400000000-abc123", "dlg-1731400000000-abc124"
    k1, k2 = session_store.dialog_key(a, one), session_store.dialog_key(a, two)
    assert k1 != k2 and len(k1) == len(k2) == 64 and k1.startswith(f"{sub}:01234567:")
    assert k1 == session_store.dialog_key(a, one)  # the same dialog on the next turn
    assert session_store.dialog_key(b, one) != k1


def _nudge(client, monkeypatch, phone: dict, mark: str, session_id: str | None = None) -> None:
    """One /api/nudges poll that leaves `mark` in the phone's dialog."""
    import server

    async def offer(req, sess, key):
        sess["pending"] = {"text": mark, "kind": "nudge"}
        return []
    monkeypatch.setattr(server.nudges, "compute", offer)
    body = {**SAC, **({"session_id": session_id} if session_id else {})}
    assert client.post("/api/nudges", json=body, headers=_h(phone)).status_code == 200


def _dialogs(client, sub: str) -> dict[str, str]:
    """session id -> the mark its dialog holds, for every dialog under this sub."""
    rows = _rows(client, "SELECT session_id, state FROM dialog_sessions WHERE driver IS NULL OR driver = ?", sub)
    return {sid: json.loads(state)["pending"]["text"] for sid, state in rows if sid.startswith(f"{sub}:")}


def test_two_phones_of_one_account_hold_separate_dialogs(client, monkeypatch, db_dialogs):
    who = _person()
    a, b = _apple(client, who).json(), _apple(client, who).json()
    sub = _claims(a["access_token"])["sub"]
    sid8 = {name: _claims(p["access_token"])["sid"].split("_")[1][:8] for name, p in (("a", a), ("b", b))}
    _nudge(client, monkeypatch, a, "phone a, no dialog id")
    _nudge(client, monkeypatch, b, "phone b, no dialog id")
    assert _dialogs(client, sub) == {f"{sub}:{sid8['a']}:solo": "phone a, no dialog id", f"{sub}:{sid8['b']}:solo": "phone b, no dialog id"}
    # the same dialog id from both phones is still two dialogs
    _nudge(client, monkeypatch, a, "phone a, trip", "trip")
    _nudge(client, monkeypatch, b, "phone b, trip", "trip")
    dialogs = _dialogs(client, sub)
    assert dialogs[f"{sub}:{sid8['a']}:trip"] == "phone a, trip" and dialogs[f"{sub}:{sid8['b']}:trip"] == "phone b, trip"
    assert len(dialogs) == 4

    # another account's dialogs are not this one's, and clearing the history takes every phone's dialogs of the account
    other = _apple(client, _person()).json()
    other_sub = _claims(other["access_token"])["sub"]
    _nudge(client, monkeypatch, other, "somebody else")
    assert len(_dialogs(client, sub)) == 4 and list(_dialogs(client, other_sub).values()) == ["somebody else"]
    assert client.delete("/api/memory/history", headers=_h(a)).status_code == 200
    assert _dialogs(client, sub) == {} and len(_dialogs(client, other_sub)) == 1


def test_a_device_tokens_dialog_is_stored_where_it_always_was(client, monkeypatch, db_dialogs):
    d = client.post("/api/auth/device").json()
    sub = _claims(d["access_token"])["sub"]
    _nudge(client, monkeypatch, d, "no id")
    _nudge(client, monkeypatch, d, "with id", "dlg-1731400000000-abc123")
    assert _dialogs(client, sub) == {f"{sub}:solo": "no id", f"{sub}:dlg-1731400000000-abc123": "with id"}


# ---- dropping a driver's dialogs does not reach into a neighbour's ------------------------------------------------

def test_drop_prefix_takes_wildcards_in_the_prefix_literally(store, monkeypatch):
    """`_` in LIKE matches any one character and every sub has one ("drv_…"); `%` matches anything."""
    monkeypatch.setenv("SESSION_STORE", "db")
    tail = uuid.uuid4().hex
    mine, neighbour = f"drv_{tail}", f"drvX{tail}"  # differ only where the `_` is
    percent, any_of = f"p%{tail}", f"pZZZ{tail}"

    async def go():
        try:
            dialogs = session_store.DbSessionStore()
            for sub in (mine, neighbour, percent, any_of):
                for n in (1, 2):
                    state = DialogState()
                    state["driver"] = sub
                    await dialogs.save(f"{sub}:dlg-{n}", state)
            dropped = [await session_store.drop_prefix(f"{mine}:"), await session_store.drop_prefix(f"{percent}:")]
            if db.store() == "sqlite":
                left = await db._sq_run(lambda con: con.execute(
                    "SELECT session_id FROM dialog_sessions WHERE session_id LIKE ?", (f"%{tail}:%",)).fetchall())
            else:
                left = await (await db._db()).fetch("SELECT session_id FROM dialog_sessions WHERE session_id LIKE $1", f"%{tail}:%")
            await session_store.drop_prefix(f"{neighbour}:")
            await session_store.drop_prefix(f"{any_of}:")
            return dropped, sorted(r[0] for r in left)
        finally:
            await db.close()
    dropped, left = run(go())
    assert dropped == [2, 2]
    assert left == sorted(f"{sub}:dlg-{n}" for sub in (neighbour, any_of) for n in (1, 2))


# ---- a deleted account's sub never gets rows again -----------------------------------------------------------------
# The org, driver and device rows of an account are made at sign-in only, in the transaction that holds the account
# row. Everything else reads them, so a request, a refresh or a sign-in that was on its way during the deletion
# cannot bring the driver back.

def _nothing_left(client, sub: str) -> None:
    assert _holding(client, {"sub": sub}) == {}


def test_a_sign_in_racing_the_deletion_starts_a_new_account(client, monkeypatch):
    who = _person()
    a = _apple(client, who).json()
    sub = _claims(a["access_token"])["sub"]
    real = accounts.find_or_create

    async def then_deleted(*args, **kw):
        account = await real(*args, **kw)  # phone B's sign-in has found the account ...
        if account.sub == sub:
            await accounts.erase(sub)  # ... and phone A's "Delete account" commits here
        return account
    monkeypatch.setattr(accounts, "find_or_create", then_deleted)
    r = _apple(client, who)
    assert r.status_code == 200, r.text
    b = r.json()
    new_sub = _claims(b["access_token"])["sub"]
    assert new_sub != sub and b["new_account"] is True and b["has_profile"] is False
    _nothing_left(client, sub)
    assert client.get("/api/auth/check", headers=_h(a)).status_code == 401
    assert client.get("/api/auth/check", headers=_h(b)).status_code == 200
    assert _rows(client, "SELECT sub FROM accounts WHERE provider_sub = ?", who) == [(new_sub,)]
    assert _rows(client, "SELECT count(*) FROM devices WHERE sub = ?", new_sub) == [(1,)]


def test_a_sign_in_whose_account_keeps_vanishing_is_503_and_leaves_nothing(client, monkeypatch):
    who = _person()
    real, seen = accounts.find_or_create, []

    async def always_deleted(*args, **kw):
        account = await real(*args, **kw)
        seen.append(account.sub)
        await accounts.erase(account.sub)
        return account
    monkeypatch.setattr(accounts, "find_or_create", always_deleted)
    r = _apple(client, who)
    assert r.status_code == 503 and len(seen) == 2 and seen[0] != seen[1]  # it started over once, with a new sub
    for sub in seen:
        _nothing_left(client, sub)


def test_a_refresh_racing_the_deletion_is_refused_and_leaves_nothing(client, monkeypatch):
    who = _person()
    a, b = _apple(client, who).json(), _apple(client, who).json()
    sub = _claims(a["access_token"])["sub"]
    real = accounts.session_by_refresh

    async def then_deleted(hashed):
        session = await real(hashed)
        await accounts.erase(session.sub)  # the deletion commits after the refresh found its session
        return session
    monkeypatch.setattr(accounts, "session_by_refresh", then_deleted)
    r = client.post("/api/auth/refresh", json={"refresh_token": b["refresh_token"]})
    assert r.status_code == 401 and r.json() == {"detail": GONE}
    _nothing_left(client, sub)


@pytest.mark.parametrize("stale_device", [False, True])
def test_a_renew_racing_the_deletion_is_refused_and_leaves_nothing(client, stale_device):
    """The renew passed the session check on a worker whose copies are from before the deletion."""
    who = _person()
    a = _apple(client, who).json()
    sub, sid = _claims(a["access_token"])["sub"], _claims(a["access_token"])["sid"]
    assert client.get("/api/auth/check", headers=_h(a)).status_code == 200
    stale = dict(accounts._sessions), dict(tenancy._devices)
    client.portal.call(accounts.erase, sub)
    accounts._sessions.update(stale[0])
    if stale_device:
        tenancy._devices.update(stale[1])
    r = client.post("/api/auth/renew", headers=_h(a))
    assert r.status_code == 401 and r.json() == {"detail": GONE}
    # a token without the org claims takes the other way through the principal: no rows from there either
    bare = {"access_token": auth._token("driver", sub, {"sid": sid})}
    tenancy.forget(sub)
    assert client.get("/api/auth/check", headers=_h(bare)).json() == {"detail": GONE}
    _nothing_left(client, sub)


def test_a_sign_in_and_a_deletion_at_the_same_moment_leave_nothing(client):
    async def round_():
        account = await accounts.find_or_create("google", _person(), auth.new_sub())
        await accounts.session_open(account.sub, uuid.uuid4().hex)
        await asyncio.gather(accounts.session_open(account.sub, uuid.uuid4().hex), accounts.erase(account.sub),
                             accounts.session_open(account.sub, uuid.uuid4().hex))
        return account.sub
    for _ in range(5):
        sub = client.portal.call(round_)
        for table in ("accounts", "account_sessions", "devices"):
            assert _rows(client, f"SELECT count(*) FROM {table} WHERE sub = ?", sub) == [(0,)], table


def test_a_deletion_waits_for_a_sign_in_that_holds_the_account(client):
    """Postgres: both start on the account row. A deletion that did not wait there would pass the session table
    while the sign-in's session is not committed yet, and leave it behind."""
    if client.store != "pg":
        pytest.skip("SQLite has one writer at a time")

    def slow_sign_in(sub):
        yield (accounts.ACCOUNT_LOCK, (sub,))
        yield ("SELECT pg_sleep(0.4)", ())  # the sign-in is in the middle of its transaction ...
        return (yield from accounts._sign_in_script(sub, tenancy.new_id("ses"), uuid.uuid4().hex, "ios", accounts._now()))

    async def race():
        account = await accounts.find_or_create("google", _person(), auth.new_sub())
        await accounts.session_open(account.sub, uuid.uuid4().hex)
        signing_in = asyncio.create_task(db.run_script(slow_sign_in(account.sub)))
        await asyncio.sleep(0.1)
        await accounts.erase(account.sub)  # ... when the deletion starts
        return account.sub, await signing_in
    sub, first = client.portal.call(race)
    assert first is False  # the sign-in went through, as an existing account
    for table in ("accounts", "account_sessions", "devices"):
        assert _rows(client, f"SELECT count(*) FROM {table} WHERE sub = ?", sub) == [(0,)], table


def test_a_profile_save_that_lands_after_the_deletion_writes_nothing(client, monkeypatch):
    who = _person()
    a = _apple(client, who, name="Sam Driver").json()
    sub = _claims(a["access_token"])["sub"]
    assert client.get("/api/auth/check", headers=_h(a)).status_code == 200
    stale = dict(accounts._sessions), dict(tenancy._devices)
    assert client.delete("/api/account", headers=_h(a)).status_code == 200
    late = TruckProfile(**{**PROFILE, "driver_name": "Late Sam"})

    async def put():  # a PUT /api/me/profile that passed auth before the deletion
        with pytest.raises(tenancy.Unregistered):
            await tenancy.profile_put(sub, late, create=False)
    client.portal.call(put)
    _nothing_left(client, sub)
    # the endpoint answers it like any request of a deleted account
    accounts._sessions.update(stale[0])
    tenancy._devices.update(stale[1])
    real = tenancy.profile_put

    async def after_the_copy_ran_out(sub, profile, create=True):
        tenancy.forget(sub)
        return await real(sub, profile, create)
    monkeypatch.setattr(tenancy, "profile_put", after_the_copy_ran_out)
    r = client.put("/api/me/profile", json={**PROFILE, "driver_name": "Late Sam"}, headers=_h(a))
    assert r.status_code == 401 and r.json() == {"detail": GONE}
    # and on a worker whose device copy is still there, the write finds no row to change
    monkeypatch.setattr(tenancy, "profile_put", real)
    accounts._sessions.update(stale[0])
    tenancy._devices.update(stale[1])
    assert client.put("/api/me/profile", json={**PROFILE, "driver_name": "Stale Sam"}, headers=_h(a)).status_code == 200
    _nothing_left(client, sub)
    assert _rows(client, "SELECT count(*) FROM drivers WHERE display_name IN ('Late Sam', 'Stale Sam')") == [(0,)]


def test_a_fleet_code_that_lands_after_the_deletion_binds_nothing(client, monkeypatch):
    a = _apple(client, _person()).json()
    sub = _claims(a["access_token"])["sub"]
    code = _fleet_code(client)
    assert client.get("/api/auth/check", headers=_h(a)).status_code == 200
    stale = dict(accounts._sessions), dict(tenancy._devices)
    assert client.delete("/api/account", headers=_h(a)).status_code == 200
    accounts._sessions.update(stale[0])
    tenancy._devices.update(stale[1])
    real = tenancy.bind_fleet_code

    async def after_the_copy_ran_out(sub, code, create=True):
        tenancy.forget(sub)
        return await real(sub, code, create)
    monkeypatch.setattr(tenancy, "bind_fleet_code", after_the_copy_ran_out)
    r = client.post("/api/auth/fleet-code", json={"code": code}, headers=_h(a))
    assert r.status_code == 401 and r.json() == {"detail": GONE}
    _nothing_left(client, sub)
    assert _rows(client, "SELECT used_by FROM fleet_codes WHERE code = ?", code) == [(None,)]  # still good for its driver


def test_a_device_token_still_gets_its_rows_on_first_contact(client):
    d = client.post("/api/auth/device").json()
    sub = _claims(d["access_token"])["sub"]
    _rows(client, "DELETE FROM devices WHERE sub = ?", sub)
    tenancy.forget(sub)
    plain = {"access_token": auth._token("driver", sub)}  # as build 2 holds it: no org claims
    assert client.put("/api/me/profile", json=PROFILE, headers=_h(plain)).status_code == 200
    assert _rows(client, "SELECT count(*) FROM devices WHERE sub = ?", sub) == [(1,)]


# ---- the owner of a fleet ---------------------------------------------------------------------------------------

def _fleet(client) -> SimpleNamespace:
    async def make():
        org = await tenancy.create_org("Acme")
        truck = await tenancy.create_vehicle(org, "Truck 7")
        return SimpleNamespace(org=org, truck=truck)
    return client.portal.call(make)


def _join(client, fleet, body: dict, role: str = "driver") -> dict:
    code = client.portal.call(tenancy.create_fleet_code, fleet.org, fleet.truck if role == "driver" else None, role)
    r = client.post("/api/auth/fleet-code", json={"code": code}, headers=_h(body))
    assert r.status_code == 200 and r.json()["role"] == role, r.text
    return r.json()


def _org_rows(client, org: str) -> dict[str, int]:
    return {t: _rows(client, f"SELECT count(*) FROM {t} WHERE {'id' if t == 'orgs' else 'org_id'} = ?", org)[0][0]
            for t in ("orgs", "vehicles", "fleet_codes", "org_credentials", "devices", "drivers")}


def test_the_only_owner_of_a_fleet_with_drivers_cannot_delete_the_account(client, db_dialogs):
    fleet = _fleet(client)
    owner = _join(client, fleet, _google(client, _person()).json(), "owner")
    driver = _join(client, fleet, _google(client, _person()).json())
    needles = _fill(client, owner)
    r = client.delete("/api/account", headers=_h(owner))
    assert r.status_code == 409 and r.json() == {"detail": account_api_last_owner(), "code": "last_owner"}
    # refused before anything was wiped
    assert len(client.get("/api/memory", headers=_h(owner)).json()["turns"]) == 2
    assert _rows(client, "SELECT count(*) FROM dialog_sessions WHERE driver = ?", needles["sub"]) == [(1,)]
    assert client.get("/api/auth/check", headers=_h(owner)).status_code == 200
    # the driver leaves freely; then the owner is alone in the fleet and it goes with them
    assert client.delete("/api/account", headers=_h(driver)).status_code == 200
    assert _org_rows(client, fleet.org)["orgs"] == 1
    assert client.delete("/api/account", headers=_h(owner)).status_code == 200
    assert _org_rows(client, fleet.org) == {t: 0 for t in ("orgs", "vehicles", "fleet_codes", "org_credentials", "devices", "drivers")}
    assert _holding(client, {**needles, "org": fleet.org, "truck": fleet.truck}) == {}


def account_api_last_owner() -> str:
    from services import account_api
    assert "only owner of a fleet" in account_api.LAST_OWNER
    return account_api.LAST_OWNER


def test_the_only_owner_of_a_fleet_with_a_stored_key_cannot_delete_the_account(client, monkeypatch):
    monkeypatch.setenv("CREDENTIALS_KEY", Fernet.generate_key().decode())
    fleet = _fleet(client)
    owner = _join(client, fleet, _apple(client, _person()).json(), "owner")
    assert client.put("/api/fleet/credentials/motive", json={"key": "motive-key-123456789"}, headers=_h(owner)).status_code == 200
    assert client.delete("/api/account", headers=_h(owner)).status_code == 409
    assert _org_rows(client, fleet.org)["org_credentials"] == 1


def test_one_of_two_owners_and_a_dispatcher_delete_freely(client):
    fleet = _fleet(client)
    first = _join(client, fleet, _google(client, _person()).json(), "owner")
    second = _join(client, fleet, _google(client, _person()).json(), "owner")
    dispatcher = _join(client, fleet, _google(client, _person()).json(), "dispatcher")
    driver = _join(client, fleet, client.post("/api/auth/device").json())  # a build-2 phone on the truck
    assert client.delete("/api/account", headers=_h(first)).status_code == 200  # the fleet still has an owner
    assert client.delete("/api/account", headers=_h(dispatcher)).status_code == 200
    assert _org_rows(client, fleet.org) == {"orgs": 1, "vehicles": 1, "fleet_codes": 4, "org_credentials": 0, "devices": 2, "drivers": 2}
    assert client.delete("/api/account", headers=_h(second)).status_code == 409  # now the last one, and a driver is left
    assert client.get("/api/auth/check", headers=_h(driver)).status_code == 200


def test_a_deletion_that_became_the_last_owners_inside_the_transaction_is_refused(client, monkeypatch):
    """The check before the transaction passed; by the transaction somebody joined."""
    fleet = _fleet(client)
    owner = _join(client, fleet, _google(client, _person()).json(), "owner")

    async def passed(sub):
        return None
    monkeypatch.setattr(accounts, "check_may_delete", passed)
    _join(client, fleet, client.post("/api/auth/device").json())
    needles = _fill(client, owner)
    before = _holding(client, needles)
    assert client.delete("/api/account", headers=_h(owner)).status_code == 409
    assert _holding(client, needles) == before  # the refusal came from the transaction: not a row was touched
    assert client.get("/api/auth/check", headers=_h(owner)).status_code == 200


# ---- dialogs go inside the transaction, and a drop that fails fails the deletion ------------------------------------

def test_a_dialog_without_a_driver_goes_in_the_transaction(client, monkeypatch, db_dialogs):
    a = _apple(client, _person()).json()
    sub = _claims(a["access_token"])["sub"]
    now = datetime.now(timezone.utc)
    _rows(client, "INSERT INTO dialog_sessions (session_id, driver, state, updated_at) VALUES (?, NULL, '{}', ?)",
          f"{sub}:old-poll", now.isoformat() if client.store == "sqlite" else now)

    async def not_run(prefix, strict=False):  # the drop before the transaction did nothing
        return 0
    monkeypatch.setattr(session_store, "drop_prefix", not_run)
    assert client.delete("/api/account", headers=_h(a)).status_code == 200
    assert _rows(client, "SELECT count(*) FROM dialog_sessions WHERE session_id LIKE ?", f"{sub}%") == [(0,)]


def test_the_stored_dialogs_go_with_the_transaction_whatever_the_separate_drop_does(client, monkeypatch, db_dialogs):
    """The dialogs are deleted inside the deletion's transaction; the drop outside it only clears this process's
    copies, after the commit. A store failing there cannot leave a stored dialog behind a 200."""
    a = _apple(client, _person()).json()
    sub = _claims(a["access_token"])["sub"]
    _nudge(client, monkeypatch, a, "nudge text")

    async def boom(self, prefix):
        raise RuntimeError("pool timeout")
    monkeypatch.setattr(session_store.DbSessionStore, "drop_prefix", boom)
    assert client.delete("/api/account", headers=_h(a)).status_code == 200
    _nothing_left(client, sub)


def test_a_dialog_started_by_a_poll_carries_its_driver(client, monkeypatch, db_dialogs):
    a = _apple(client, _person()).json()
    sub = _claims(a["access_token"])["sub"]
    _nudge(client, monkeypatch, a, "nudge text")
    assert _rows(client, "SELECT driver FROM dialog_sessions WHERE session_id LIKE ?", f"{sub}%") == [(sub,)]


def test_deleting_the_account_forgets_the_dialogs_in_this_process(client, monkeypatch):
    monkeypatch.setenv("SESSION_STORE", "memory")
    a = _apple(client, _person()).json()
    sub = _claims(a["access_token"])["sub"]
    _nudge(client, monkeypatch, a, "nudge text")
    assert [k for k in session_store.memory_sessions() if k.startswith(sub)]
    assert client.delete("/api/account", headers=_h(a)).status_code == 200
    assert not [k for k in session_store.memory_sessions() if k.startswith(sub)]


# ---- the agent's turns use the phone's dialog too -------------------------------------------------------------------

def _turn(client, monkeypatch, phone: dict, mark: str, session_id: str | None = None) -> None:
    """One /api/agent turn that leaves `mark` in the dialog it ran in."""
    async def turn(self, req, driver, sess):
        sess["pending"] = {"text": mark, "kind": "nudge"}
        return AgentResponse(status="chat", transcript=req.text, reply="OK.", source="test")
    monkeypatch.setattr(agent.LlmAgentProvider, "_turn", turn)
    body = {"text": "hi", **SAC, **({"session_id": session_id} if session_id else {})}
    assert client.post("/api/agent", json=body, headers=_h(phone)).status_code == 200


def test_two_phones_of_one_account_do_not_share_a_dialog_through_the_agent(client, monkeypatch, db_dialogs):
    who = _person()
    a, b = _apple(client, who).json(), _apple(client, who).json()
    sub = _claims(a["access_token"])["sub"]
    sid8 = {name: _claims(p["access_token"])["sid"].split("_")[1][:8] for name, p in (("a", a), ("b", b))}
    _turn(client, monkeypatch, a, "a typed")  # no dialog id: the agent's own "solo" fallback
    _turn(client, monkeypatch, b, "b typed")
    assert _dialogs(client, sub) == {f"{sub}:{sid8['a']}:solo": "a typed", f"{sub}:{sid8['b']}:solo": "b typed"}
    _turn(client, monkeypatch, a, "a in trip", "trip")  # a dialog id: prefixed by the server
    _turn(client, monkeypatch, b, "b in trip", "trip")
    dialogs = _dialogs(client, sub)
    assert dialogs[f"{sub}:{sid8['a']}:trip"] == "a in trip" and dialogs[f"{sub}:{sid8['b']}:trip"] == "b in trip"
    assert len(dialogs) == 4
    d = client.post("/api/auth/device").json()  # a device token: where it always was
    _turn(client, monkeypatch, d, "device typed")
    _turn(client, monkeypatch, d, "device in trip", "trip")
    dsub = _claims(d["access_token"])["sub"]
    assert _dialogs(client, dsub) == {f"{dsub}:solo": "device typed", f"{dsub}:trip": "device in trip"}


def test_a_dialog_key_never_exceeds_64_characters():
    long_sub = "drv_" + "a" * 60
    a, b = _principal(long_sub, "ses_0123456789abcdef0123"), _principal(long_sub, "ses_fedcba98765432100000")
    keys = {session_store.dialog_key(p, i) for p in (a, b) for i in (None, "trip", "dlg-1731400000000-abc123", "x" * 500)}
    assert len(keys) == 8 and all(len(k) == 64 for k in keys)
    for n in (1, 30, 44, 45, 46, 47, 63, 64, 200):  # every sub length around the points where the form changes
        p = _principal("d" * n, "ses_0123456789abcdef0123")
        for app_id in (None, "t", "x" * 17, "x" * 18, "x" * 19, "dlg-1731400000000-abc123"):
            assert len(session_store.dialog_key(p, app_id)) <= 64
        assert session_store.dialog_key(p, "one-long-dialog-id-0001") != session_store.dialog_key(p, "one-long-dialog-id-0002")
    # the usual sub: the id is hashed whole into the 18 characters left
    sub = auth.new_sub()
    app_id = "dlg-1731400000000-abc123"
    assert session_store.dialog_key(_principal(sub, "ses_0123456789abcdef0123"), app_id) == \
        f"{sub}:01234567:{hashlib.sha256(app_id.encode()).hexdigest()[:18]}"


# ---- what is still running when the memory is cleared or the account goes -------------------------------------------

def _in_thread(call) -> tuple[threading.Thread, dict]:
    out: dict = {}
    t = threading.Thread(target=lambda: out.setdefault("r", call()))
    t.start()
    return t, out


def test_clear_history_waits_for_the_saves_in_flight(client):
    a = _google(client, _person()).json()
    sub = _claims(a["access_token"])["sub"]
    gate = asyncio.Event()

    async def start():
        async def slow_text():
            await gate.wait()
            return "the copilot's line, translated late"
        memory.add_later(sub, [("driver", "a line still being saved"), ("copilot", slow_text)])
    client.portal.call(start)
    t, out = _in_thread(lambda: client.delete("/api/memory/history", headers=_h(a)))
    t.join(0.4)
    assert t.is_alive()  # it waits: a save that landed after the clear would bring the line back
    client.portal.call(gate.set)
    t.join(10)
    assert out["r"].status_code == 200 and out["r"].json()["deleted"] == 2
    assert client.get("/api/memory", headers=_h(a)).json() == {"turns": []}


def test_clearing_forgets_the_memory_reads_in_flight_for_the_drivers_dialogs(client):
    a, other = _google(client, _person()).json(), _google(client, _person()).json()
    sub, other_sub = _claims(a["access_token"])["sub"], _claims(other["access_token"])["sub"]

    async def start():
        tasks = {f"{s}:{n}": asyncio.create_task(asyncio.sleep(60)) for s in (sub, other_sub) for n in ("solo", "trip")}
        agent._past_tasks.update(tasks)
        return tasks
    tasks = client.portal.call(start)
    try:
        assert client.delete("/api/memory/history", headers=_h(a)).status_code == 200
        assert sorted(agent._past_tasks) == sorted(k for k in tasks if k.startswith(other_sub))

        async def cancelled():
            await asyncio.sleep(0)
            return {k: t.cancelled() for k, t in tasks.items()}
        assert client.portal.call(cancelled) == {k: k.startswith(sub) for k in tasks}
        assert client.delete("/api/account", headers=_h(other)).status_code == 200
        assert not agent._past_tasks
    finally:
        agent._past_tasks.clear()


def test_deleting_the_account_stops_its_breakdown_research(client):
    a = _google(client, _person()).json()
    sub = _claims(a["access_token"])["sub"]

    async def start():
        tasks = {rid: asyncio.create_task(asyncio.sleep(60)) for rid in ("rid-mine", "rid-theirs")}
        breakdown._JOBS.update(tasks)
        breakdown._OWNERS.update({"rid-mine": sub, "rid-theirs": "drv_somebody_else"})
        return tasks
    tasks = client.portal.call(start)
    try:
        assert client.delete("/api/account", headers=_h(a)).status_code == 200
        assert list(breakdown._JOBS) == ["rid-theirs"] and breakdown._OWNERS == {"rid-theirs": "drv_somebody_else"}

        async def cancelled():
            await asyncio.sleep(0)
            return tasks["rid-mine"].cancelled(), tasks["rid-theirs"].cancelled()
        assert client.portal.call(cancelled) == (True, False)
    finally:
        client.portal.call(tasks["rid-theirs"].cancel)
        breakdown._JOBS.clear()
        breakdown._OWNERS.clear()


def test_a_research_remembers_who_started_it_and_a_cancelled_one_writes_nothing(client, monkeypatch):
    sub = auth.new_sub()

    async def research(d, req, sess, source):
        await asyncio.sleep(60)
    monkeypatch.setattr(breakdown, "_run", research)

    async def go():
        sess = DialogState()
        sess["driver"] = sub
        res = breakdown.start_research({"kind": "tire"}, AgentRequest(text="flat tire", **SAC), sess, "flat tire", "test")
        rid = res.research_id
        owner, theirs = breakdown._OWNERS[rid], await breakdown.result(rid, "drv_somebody_else")
        await asyncio.sleep(0.05)  # the "researching" row is written
        breakdown.forget(sub)
        await asyncio.sleep(0.05)
        return rid, owner, theirs
    rid, owner, theirs = client.portal.call(go)
    assert owner == sub and theirs is None and rid not in breakdown._JOBS and rid not in breakdown._OWNERS
    assert _rows(client, "SELECT state FROM jobs WHERE rid = ?", rid)[0][0].count("researching") == 1  # no result landed
    _rows(client, "DELETE FROM jobs WHERE rid = ?", rid)


def test_deleting_the_account_clears_this_workers_copies(client):
    a = _google(client, _person()).json()
    claims = _claims(a["access_token"])
    sub = claims["sub"]
    assert client.put("/api/me/profile", json=PROFILE, headers=_h(a)).status_code == 200

    async def warm():
        await tenancy.device_created(sub)
        await tenancy.grace_info(sub)
        await tenancy.touch(sub)
        await tenancy.motive_user(claims["driver_id"])
    client.portal.call(warm)
    caches = (tenancy._devices, tenancy._profiles, tenancy._grace, tenancy._created, tenancy._touched)
    assert all(sub in c for c in caches) and claims["driver_id"] in tenancy._motive_users and claims["sid"] in accounts._sessions
    assert client.delete("/api/account", headers=_h(a)).status_code == 200
    assert not any(sub in c for c in caches) and claims["driver_id"] not in tenancy._motive_users
    assert claims["sid"] not in accounts._sessions and claims["sid"] not in accounts._touched


# ---- Apple: bounded exchanges, and a deletion that meets one in flight ------------------------------------------------

def _held(apple_api, reply) -> asyncio.Event:
    """Apple's token endpoint answers only once the returned event is set."""
    release = asyncio.Event()

    async def slow(request):
        await release.wait()
        return reply(request)
    apple_api.token_reply = slow
    return release


def test_one_identity_token_replayed_starts_one_exchange(client, apple_api):
    release = _held(apple_api, _refused)
    who = _person()
    token = test_accounts._sign(test_accounts._apple_claims(who))
    statuses = {_apple(client, who, token, authorization_code=f"junk-{i}").status_code for i in range(30)}
    client.portal.call(asyncio.sleep, 0.05)
    assert statuses == {200} and len(apple._tasks) == 1 and len(apple_api.exchanges) == 1
    client.portal.call(release.set)
    _settle(client)
    assert not apple._tasks and not apple._inflight and len(apple_api.exchanges) == 1


def test_at_most_four_exchanges_run_at_once(client, apple_api):
    release = _held(apple_api, lambda request: httpx.Response(200, request=request, json={"refresh_token": REFRESH}))
    bodies = [_apple(client, _person(), authorization_code=CODE).json() for _ in range(7)]
    client.portal.call(asyncio.sleep, 0.05)
    assert len(apple._tasks) == 7 and len(apple_api.exchanges) == apple.MAX_EXCHANGES == 4
    client.portal.call(release.set)
    _settle(client)
    assert len(apple_api.exchanges) == 7
    assert all(_sealed(client, _claims(b["access_token"])["sub"]) for b in bodies)


def test_the_exchanges_in_flight_are_drained_at_shutdown(client, apple_api):
    import server
    release = _held(apple_api, lambda request: httpx.Response(200, request=request, json={"refresh_token": REFRESH}))
    a = _apple(client, _person(), authorization_code=CODE).json()
    assert [t for t in server._draining() if t in apple._tasks]
    assert client.portal.call(server.drain, 0.05) >= 1  # still running at the deadline: it is being waited for
    client.portal.call(release.set)
    assert client.portal.call(server.drain, 5) == 0
    assert _sealed(client, _claims(a["access_token"])["sub"])


def test_a_deletion_waits_for_the_accounts_exchange_and_revokes_its_token(client, apple_api):
    release = _held(apple_api, lambda request: httpx.Response(200, request=request, json={"refresh_token": REFRESH}))
    a = _apple(client, _person(), authorization_code=CODE).json()
    sub = _claims(a["access_token"])["sub"]
    t, out = _in_thread(lambda: client.delete("/api/account", headers=_h(a)))
    t.join(0.4)
    assert t.is_alive() and apple_api.revocations == []  # Apple has not issued the token yet
    client.portal.call(release.set)
    t.join(10)
    assert out["r"].status_code == 200 and out["r"].json()["apple_revoked"] is True
    assert [f["token"] for f in apple_api.revocations] == [REFRESH]
    _settle(client)
    _nothing_left(client, sub)


def test_a_deletion_that_fails_after_the_revoke_keeps_the_token_for_the_retry(client, apple_api, monkeypatch):
    a = _signed_in_with_token(client, _person())
    sub = _claims(a["access_token"])["sub"]
    sealed = _sealed(client, sub)
    steps = accounts._erase_steps
    monkeypatch.setattr(accounts, "_erase_steps", lambda *args: [*steps(*args), ("DELETE FROM no_such_table WHERE sub = ?", (sub,))])
    assert client.delete("/api/account", headers=_h(a)).status_code == 503
    assert _sealed(client, sub) == sealed and len(apple_api.revocations) == 1
    monkeypatch.setattr(accounts, "_erase_steps", steps)
    r = client.delete("/api/account", headers=_h(a))
    # Apple answers 200 for a token it already revoked: true means Apple accepted the call
    assert r.status_code == 200 and r.json()["apple_revoked"] is True and len(apple_api.revocations) == 2
    _nothing_left(client, sub)


# ---- re-review of #171: fleet binds and the owner's deletion meet on the same rows -----------------------------------

def test_a_fleet_code_from_a_deleted_account_on_a_stale_worker_is_refused_and_stays_good(client):
    """R2: the worker still holds the session and the device copy; the deletion has committed."""
    a = _apple(client, _person()).json()
    sub = _claims(a["access_token"])["sub"]
    code = _fleet_code(client)
    assert client.get("/api/auth/check", headers=_h(a)).status_code == 200
    stale = dict(accounts._sessions), dict(tenancy._devices)
    assert client.delete("/api/account", headers=_h(a)).status_code == 200
    accounts._sessions.update(stale[0])
    tenancy._devices.update(stale[1])
    r = client.post("/api/auth/fleet-code", json={"code": code}, headers=_h(a))
    assert r.status_code == 401 and r.json() == {"detail": GONE}
    assert _rows(client, "SELECT used_by FROM fleet_codes WHERE code = ?", code) == [(None,)]
    _nothing_left(client, sub)
    newcomer = _google(client, _person()).json()  # the code still binds the driver it was made for
    assert client.post("/api/auth/fleet-code", json={"code": code}, headers=_h(newcomer)).status_code == 200


def test_a_fleet_code_whose_account_is_deleted_halfway_through_the_bind_is_refused(client, monkeypatch):
    """R3: the deletion commits after the endpoint looked the device up and before the bind writes."""
    a = _apple(client, _person()).json()
    sub = _claims(a["access_token"])["sub"]
    code = _fleet_code(client)
    real = db.run_script

    async def deleted_first(script):
        if getattr(script, "__name__", "") == "_bind_script":
            await accounts.erase(sub)
        return await real(script)
    monkeypatch.setattr(db, "run_script", deleted_first)
    r = client.post("/api/auth/fleet-code", json={"code": code}, headers=_h(a))
    assert r.status_code == 401 and r.json() == {"detail": GONE}
    assert _rows(client, "SELECT used_by FROM fleet_codes WHERE code = ?", code) == [(None,)]
    _nothing_left(client, sub)


def test_a_bind_into_a_fleet_its_owner_just_closed_is_refused(client, monkeypatch):
    """R4: the owner, alone with a truck and an unused code, deletes the account while a phone redeems that code."""
    fleet = _fleet(client)
    owner = _join(client, fleet, _google(client, _person()).json(), "owner")
    code = client.portal.call(tenancy.create_fleet_code, fleet.org, fleet.truck, "driver")
    d = client.post("/api/auth/device").json()
    dsub = _claims(d["access_token"])["sub"]
    real = db.run_script

    async def owner_first(script):
        if getattr(script, "__name__", "") == "_bind_script":
            await accounts.erase(_claims(owner["access_token"])["sub"])
        return await real(script)
    monkeypatch.setattr(db, "run_script", owner_first)
    r = client.post("/api/auth/fleet-code", json={"code": code}, headers=_h(d))
    assert r.status_code == 401 and r.json() == {"detail": "That fleet code isn't right or has expired"}
    assert _org_rows(client, fleet.org) == {t: 0 for t in ("orgs", "vehicles", "fleet_codes", "org_credentials", "devices", "drivers")}
    org, vehicle = _rows(client, "SELECT org_id, vehicle_id FROM devices WHERE sub = ?", dsub)[0]
    assert vehicle is None and _rows(client, "SELECT solo FROM orgs WHERE id = ?", org) == [(1,)]  # still its own org


@pytest.mark.parametrize("gone", ["vehicle_id", "org_id"])
def test_a_bind_checks_the_fleet_and_the_truck_inside_its_transaction(client, gone):
    """A code that points at a truck or a fleet no longer there binds nobody. The store would not let such a code be
    made; the test makes one with the foreign keys switched off for one statement."""
    fleet = _fleet(client)
    code = client.portal.call(tenancy.create_fleet_code, fleet.org, fleet.truck, "driver")
    d = client.post("/api/auth/device").json()

    async def dangle():
        sql = f"UPDATE fleet_codes SET {gone} = ? WHERE code = ?"
        if db.store() == "sqlite":
            def _run(con):
                con.execute("PRAGMA foreign_keys = OFF")
                try:
                    con.execute(sql, ("gone_123", code))
                finally:
                    con.execute("PRAGMA foreign_keys = ON")
            await db._sq_run(_run)
        else:
            pool = await db._db()
            async with pool.acquire() as con, con.transaction():
                await con.execute("SET LOCAL session_replication_role = replica")  # no foreign key triggers
                await con.execute(db._numbered(sql), "gone_123", code)
    client.portal.call(dangle)
    r = client.post("/api/auth/fleet-code", json={"code": code}, headers=_h(d))
    assert r.status_code == 401 and r.json() == {"detail": "That fleet code isn't right or has expired"}
    assert _rows(client, "SELECT used_by FROM fleet_codes WHERE code = ?", code) == [(None,)]
    assert _rows(client, "SELECT vehicle_id FROM devices WHERE sub = ?", _claims(d["access_token"])["sub"]) == [(None,)]
    _rows(client, "DELETE FROM fleet_codes WHERE code = ?", code)


def test_two_phones_with_one_code_bind_one(client):
    fleet = _fleet(client)
    code = client.portal.call(tenancy.create_fleet_code, fleet.org, fleet.truck, "driver")
    phones = [client.post("/api/auth/device").json() for _ in range(4)]

    async def race():
        subs = [_claims(p["access_token"])["sub"] for p in phones]
        out = await asyncio.gather(*(tenancy.bind_fleet_code(s, code) for s in subs), return_exceptions=True)
        return subs, out
    subs, out = client.portal.call(race)
    bound = [s for s, o in zip(subs, out) if isinstance(o, tenancy.Device)]
    assert len(bound) == 1 and all(isinstance(o, tenancy.BadFleetCode) for o in out if not isinstance(o, tenancy.Device))
    assert _rows(client, "SELECT used_by FROM fleet_codes WHERE code = ?", code) == [(bound[0],)]
    assert _rows(client, "SELECT count(*) FROM devices WHERE org_id = ?", fleet.org) == [(1,)]


def test_a_refused_deletion_has_wiped_nothing(client, monkeypatch, apple_api, db_dialogs):
    """R5: the check before the deletion passed and somebody joined the fleet before its transaction. The 409 comes
    from the transaction, which deleted nothing. The Apple token was already revoked: it is forgotten, so the next
    sign-in with Apple gets a live one."""
    fleet = _fleet(client)
    who = _person()
    owner = _join(client, fleet, _apple(client, who, authorization_code=CODE).json(), "owner")
    _settle(client)
    sub = _claims(owner["access_token"])["sub"]
    needles = _fill(client, owner)
    before = _holding(client, needles)
    real = accounts.check_may_delete

    async def passes_then_somebody_joins(s):
        await real(s)
        dev = await tenancy.ensure_solo_org(auth.new_sub())
        await tenancy.bind_fleet_code(dev.sub, await tenancy.create_fleet_code(fleet.org, fleet.truck))
    monkeypatch.setattr(accounts, "check_may_delete", passes_then_somebody_joins)
    r = client.delete("/api/account", headers=_h(owner))
    assert r.status_code == 409 and r.json()["code"] == "last_owner"
    after = _holding(client, needles)
    assert {t: n for t, n in after.items() if t != "accounts"} == {t: n for t, n in before.items() if t != "accounts"}
    assert len(client.get("/api/memory", headers=_h(owner)).json()["turns"]) == 2
    assert len(apple_api.revocations) == 1 and _sealed(client, sub) is None
    monkeypatch.setattr(accounts, "check_may_delete", real)
    _apple(client, who, authorization_code=CODE)
    _settle(client)
    assert len(apple_api.exchanges) == 2 and _sealed(client, sub)


def test_a_fleet_join_waits_for_its_owners_deletion(client, monkeypatch):
    """Postgres: the owner's deletion counted nobody else in the fleet and is about to delete it. A bind into the
    fleet in that moment waits on the fleet's row and then finds the fleet gone; without the lock it would join, and
    the deletion would trip over the new member."""
    if client.store != "pg":
        pytest.skip("SQLite has one writer at a time")
    fleet = _fleet(client)
    owner = _join(client, fleet, _google(client, _person()).json(), "owner")
    osub = _claims(owner["access_token"])["sub"]
    real = accounts._device_script

    def counted_then_pause(sub):
        out = yield from real(sub)
        yield ("SELECT pg_sleep(0.4)", ())
        return out
    monkeypatch.setattr(accounts, "_device_script", counted_then_pause)

    async def race():
        dev = await tenancy.ensure_solo_org(auth.new_sub())
        code = await tenancy.create_fleet_code(fleet.org, fleet.truck)
        erasing = asyncio.create_task(accounts.erase(osub))
        await asyncio.sleep(0.15)
        try:
            await tenancy.bind_fleet_code(dev.sub, code)
            joined = True
        except tenancy.BadFleetCode:
            joined = False
        return dev.sub, joined, await erasing
    dsub, joined, erased = client.portal.call(race)
    assert joined is False and erased["sessions"] == 1
    assert _org_rows(client, fleet.org)["orgs"] == 0
    assert _rows(client, "SELECT o.solo FROM devices d JOIN orgs o ON o.id = d.org_id WHERE d.sub = ?", dsub) == [(1,)]


def test_an_accounts_bind_and_its_deletion_do_not_interleave(client, monkeypatch):
    """Postgres: the account binds a code while it is being deleted. The bind holds the account row, so the deletion
    waits and then takes the bind's result with it (the code's used_by becomes 'deleted'). Without that, the
    deletion passes the codes before the bind commits and the dead sub stays on the code."""
    if client.store != "pg":
        pytest.skip("SQLite has one writer at a time")
    a = _apple(client, _person()).json()
    sub = _claims(a["access_token"])["sub"]
    code = _fleet_code(client)
    real = tenancy._bind_script

    def device_locked_then_pause(sub, code, account, now, at):
        script = real(sub, code, account, now, at)
        rows = None
        while True:
            try:
                sql, args = script.send(rows)
            except StopIteration as done:
                return done.value
            rows = yield (sql, args)
            if sql.startswith("SELECT driver_id, org_id FROM devices"):
                yield ("SELECT pg_sleep(0.4)", ())
    monkeypatch.setattr(tenancy, "_bind_script", device_locked_then_pause)

    async def race():
        binding = asyncio.create_task(tenancy.bind_fleet_code(sub, code, create=False))
        await asyncio.sleep(0.15)
        erased = await accounts.erase(sub)
        try:
            await binding
        except tenancy.Unregistered:  # bound, then deleted before the device was read back
            pass
        return erased
    client.portal.call(race)
    assert _rows(client, "SELECT used_by FROM fleet_codes WHERE code = ?", code) == [(accounts.CODE_USER_GONE,)]
    _nothing_left(client, sub)


def test_sqlite_refuses_a_reference_to_a_row_that_is_not_there(client):
    if client.store != "sqlite":
        pytest.skip("Postgres always does")
    with pytest.raises(Exception, match="FOREIGN KEY"):
        _rows(client, "INSERT INTO vehicles (id, org_id, label, created_at) VALUES ('veh_x', 'org_nowhere', 'T', '2026-11-12')")


# ---- an Apple token that arrives after the account is gone ---------------------------------------------------------

def test_an_exchange_that_lands_after_the_deletion_revokes_its_token(client, apple_api, monkeypatch, caplog):
    """R9: the deletion waits for the account's exchange only so long (here every slot is taken)."""
    monkeypatch.setattr(apple, "TIMEOUT_S", 0.2)
    release = _held(apple_api, lambda request: httpx.Response(200, request=request, json={"refresh_token": REFRESH}))
    for _ in range(apple.MAX_EXCHANGES):
        _apple(client, _person(), authorization_code=CODE)
    a = _apple(client, _person(), authorization_code=CODE).json()
    sub = _claims(a["access_token"])["sub"]
    r = client.delete("/api/account", headers=_h(a))
    assert r.status_code == 200 and r.json()["apple_revoked"] is None and apple_api.revocations == []
    client.portal.call(release.set)
    _settle(client)
    assert len(apple_api.exchanges) == apple.MAX_EXCHANGES + 1
    assert [f["token"] for f in apple_api.revocations] == [REFRESH]  # the late token, revoked as it arrived
    form = apple_api.revocations[0]
    assert form["client_id"] == BUNDLE and form["token_type_hint"] == "refresh_token" and _secret_claims(form["client_secret"])["sub"] == BUNDLE
    assert f"apple refresh token arrived after account {sub} was deleted: revoked=True" in caplog.text
    _nothing_left(client, sub)
    _no_secrets_in(caplog, form["client_secret"])


def test_a_late_token_whose_revoke_fails_is_logged_and_dropped(client, apple_api, caplog):
    release = _held(apple_api, lambda request: httpx.Response(200, request=request, json={"refresh_token": REFRESH}))
    apple_api.revoke_reply = _revoke_refused
    a = _apple(client, _person(), authorization_code=CODE).json()
    sub = _claims(a["access_token"])["sub"]
    client.portal.call(accounts.erase, sub)  # gone before Apple answered, without waiting
    client.portal.call(release.set)
    _settle(client)
    assert len(apple_api.revocations) == 1
    assert "apple revoke refused" in caplog.text and f"account {sub} was deleted: revoked=False" in caplog.text
    _nothing_left(client, sub)
    _no_secrets_in(caplog)


# ---- the answers the app reads ------------------------------------------------------------------------------------

def test_a_deletion_waits_for_the_saves_in_flight(client):
    """A turn's memory save still on its way when the account goes would land after the transaction."""
    a = _google(client, _person()).json()
    sub = _claims(a["access_token"])["sub"]
    gate = asyncio.Event()

    async def start():
        async def slow_text():
            await gate.wait()
            return "the copilot's line, translated late"
        memory.add_later(sub, [("driver", "a line still being saved"), ("copilot", slow_text)])
    client.portal.call(start)
    t, out = _in_thread(lambda: client.delete("/api/account", headers=_h(a)))
    t.join(0.4)
    assert t.is_alive()
    client.portal.call(gate.set)
    t.join(10)
    assert out["r"].status_code == 200 and out["r"].json()["deleted"] == 2
    client.portal.call(asyncio.sleep, 0.05)
    _nothing_left(client, sub)


def test_the_account_endpoints_publish_their_answers_in_the_schema(client):
    spec = client.get("/api/openapi.json").json()
    schemas = spec["components"]["schemas"]

    def ok_schema(path, method):
        ref = spec["paths"][path][method]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        return schemas[ref.rsplit("/", 1)[-1]]
    delete = ok_schema("/api/account", "delete")
    assert set(delete["properties"]) == {"ok", "deleted", "trip_events", "profile", "sessions", "apple_revoked"}
    assert set(ok_schema("/api/memory/history", "delete")["properties"]) == {"deleted", "facts"}
    assert set(ok_schema("/api/auth/logout-all", "post")["properties"]) == {"ok", "closed"}
    refusal = spec["paths"]["/api/account"]["delete"]["responses"]["409"]["content"]["application/json"]["schema"]["$ref"]
    refusal = schemas[refusal.rsplit("/", 1)[-1]]
    assert set(refusal["properties"]) == {"detail", "code"}
    assert set(refusal["properties"]["code"]["enum"]) == {"last_owner", "not_an_account"}


def test_logout_all_answers_its_model(client):
    a = _apple(client, _person()).json()
    assert client.post("/api/auth/logout-all", headers=_h(a)).json() == {"ok": True, "closed": 1}
    d = client.post("/api/auth/device").json()
    assert client.post("/api/auth/logout-all", headers=_h(d)).json() == {"ok": True, "closed": 0}
