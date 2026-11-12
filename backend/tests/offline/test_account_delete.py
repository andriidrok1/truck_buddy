"""Deleting an account (DELETE /api/account) with the Apple token revocation, clearing the history only
(DELETE /api/memory/history) and a dialog per signed-in phone.

Same harness as test_accounts.py: a test RSA key behind a stubbed JWKS, Apple's token and revoke endpoints stubbed,
every test on SQLite and, with TEST_DATABASE_URL, on Postgres."""
import asyncio
import json
import logging
import re
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
from services import accounts, apple, auth, db, kv, memory, secrets as sec, session_store, tenancy, trip_events
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
    assert _rows(client, "SELECT count(*) FROM jobs WHERE driver = ?", sub) == [(1,)]  # deleted inside the transaction: back
    assert _rows(client, "SELECT count(*) FROM conversation WHERE driver = ?", sub) == [(0,)]  # the wipe before it had run
    assert client.get("/api/auth/check", headers=_h(a)).status_code == 200  # still signed in, so it can ask again
    monkeypatch.setattr(accounts, "_erase_steps", steps)
    r = client.delete("/api/account", headers=_h(a))
    assert r.status_code == 200 and r.json()["deleted"] == 0 and r.json()["sessions"] == 1
    assert _holding(client, needles) == {}


def test_a_device_token_has_no_account_to_delete(client):
    for token in (client.post("/api/auth/device").json(), client.post("/api/auth/renew", headers=_h(client.post("/api/auth/device").json())).json()):
        sub = _claims(token["access_token"])["sub"]
        client.portal.call(memory.add, sub, [("driver", "hi"), ("copilot", "Hello.")])
        r = client.delete("/api/account", headers=_h(token))
        assert r.status_code == 409
        assert r.json() == {"detail": "This sign-in has no account. Use DELETE /api/memory to delete its data."}
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
    raise httpx.ConnectError("no route to appleid.apple.com")


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


def test_a_later_sign_in_replaces_the_stored_token(client, apple_api):
    who = _person()
    a = _signed_in_with_token(client, who)
    apple_api.token_reply = lambda request: httpx.Response(200, request=request, json={"refresh_token": "r.newer"})
    _apple(client, who, authorization_code=CODE)
    _settle(client)
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
