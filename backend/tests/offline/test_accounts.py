"""Sign in with Apple and Google (services/identity.py, services/accounts.py, /api/auth/apple|google|logout).

The provider is a test RSA key and a stubbed JWKS endpoint: no call leaves the process. Every test runs on SQLite
and, when TEST_DATABASE_URL points at a throwaway Postgres, on Postgres too (the store production uses)."""
import base64
import hashlib
import hmac
import json
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from services import accounts, auth, db, identity, memory, tenancy

APPLE_HOST, GOOGLE_HOST = "appleid.apple.com", "www.googleapis.com"
BUNDLE = "io.truckbuddy.ios"
GOOGLE_IOS, GOOGLE_WEB = "ios-123.apps.googleusercontent.com", "web-456.apps.googleusercontent.com"
NONCE = "raw-nonce-from-the-phone"
PROFILE = {"height_in": 162, "weight_lbs": 80000, "length_ft": 70, "trailer_ft": 53, "axles": 5, "hazmat": False}
GONE = "This device is no longer registered"

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)  # the provider's signing key
ROTATED = rsa.generate_private_key(public_exponent=65537, key_size=2048)  # the one it rotates to
STRANGER = rsa.generate_private_key(public_exponent=65537, key_size=2048)  # somebody else's


def _jwk(key, kid: str) -> dict:
    return {**json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())), "kid": kid, "alg": "RS256", "use": "sig"}


class Jwks:
    """What the stubbed providers serve; tests swap `keys` to rotate and set `down` to fail the fetch."""

    def __init__(self, fake_http):
        self.http, self.keys, self.down = fake_http, {"k1": KEY}, False
        for host, path in ((APPLE_HOST, "/auth/keys"), (GOOGLE_HOST, "/oauth2/v3/certs")):
            fake_http.add(host, path, fn=self._serve)

    def _serve(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            return httpx.Response(503, text="down", request=request)
        return httpx.Response(200, json={"keys": [_jwk(k, kid) for kid, k in self.keys.items()]}, request=request)

    def fetches(self, host: str = APPLE_HOST) -> int:
        return len(self.http.calls_to(host))


@pytest.fixture
def jwks(fake_http):
    identity.reset()
    yield Jwks(fake_http)
    identity.reset()


@pytest.fixture(params=["sqlite", "pg"])
def store(request, tmp_path, monkeypatch):
    if request.param == "sqlite":
        monkeypatch.setenv("MEMORY_STORE", "sqlite")
        monkeypatch.setenv("MEMORY_SQLITE_PATH", str(tmp_path / "accounts.sqlite"))
    else:
        if not os.environ.get("TEST_DATABASE_URL"):
            pytest.skip("needs TEST_DATABASE_URL (a throwaway Postgres)")
        monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
        monkeypatch.setenv("MEMORY_STORE", "pg")
    yield request.param
    db._pool, db._pool_lock = None, None  # the pool died with the client's loop


@pytest.fixture
def client(store, jwks, monkeypatch):
    """The app with a store, both providers configured and the per-IP limits off."""
    from fastapi.testclient import TestClient
    import server
    monkeypatch.setenv("APPLE_AUDIENCES", BUNDLE)
    monkeypatch.setenv("GOOGLE_CLIENT_IDS", f"{GOOGLE_IOS}, {GOOGLE_WEB}")
    monkeypatch.setattr(auth, "MAX_SIGNINS_PER_IP", 0)
    monkeypatch.setattr(auth, "MAX_DEVICES_PER_IP", 0)
    monkeypatch.setenv("CHAIN_CONTROLS", "0")  # its poller starts with the app and would knock on fake_http
    tenancy.reset_caches()
    accounts.reset_caches()
    auth._signins.clear()
    with TestClient(server.app) as c:
        c.store = store
        yield c
    tenancy.reset_caches()
    accounts.reset_caches()


# ---- helpers -------------------------------------------------------------------------------------------------

def _person() -> str:
    """A provider id no other test (or earlier run against the same Postgres) has used."""
    return f"u-{uuid.uuid4().hex}"


def _sign(claims: dict, key=KEY, kid="k1") -> str:
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


def _apple_claims(who: str, **over) -> dict:
    now = int(time.time())
    return {"iss": "https://appleid.apple.com", "aud": BUNDLE, "sub": who, "iat": now, "exp": now + 600,
            "nonce": hashlib.sha256(NONCE.encode()).hexdigest(), "email": f"{who}@privaterelay.appleid.com",
            "email_verified": "true", **over}


def _google_claims(who: str, **over) -> dict:
    now = int(time.time())
    return {"iss": "https://accounts.google.com", "aud": GOOGLE_IOS, "sub": who, "iat": now, "exp": now + 600,
            "email": f"{who}@gmail.com", "email_verified": True, "name": "Sam Driver", **over}


def _apple(c, who: str, token: str | None = None, **body):
    return c.post("/api/auth/apple", json={"identity_token": token or _sign(_apple_claims(who)), "nonce": NONCE, **body})


def _google(c, who: str, token: str | None = None, **body):
    return c.post("/api/auth/google", json={"id_token": token or _sign(_google_claims(who)), **body})


def _claims(access_token: str) -> dict:
    return jwt.decode(access_token, auth._secret(), algorithms=["HS256"])


def _h(body: dict) -> dict:
    return {"Authorization": f"Bearer {body['access_token']}"}


def _rows(c, sql: str, *args) -> list[tuple]:
    """A query against the store the client runs on, on the client's event loop (`?` placeholders)."""
    async def q():
        if c.store == "sqlite":
            return await db._sq_run(lambda con: con.execute(sql, args).fetchall())
        pg_sql = sql
        for i in range(len(args)):
            pg_sql = pg_sql.replace("?", f"${i + 1}", 1)
        return [tuple(r) for r in await (await db._db()).fetch(pg_sql, *args)]
    return c.portal.call(q)


def _hs256(claims: dict, secret: bytes, kid="k1") -> str:
    """A token signed with HMAC by hand (PyJWT refuses to use a public key as an HMAC secret, an attacker does not)."""
    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    head = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": kid}).encode())
    body = b64(json.dumps(claims).encode())
    return f"{head}.{body}.{b64(hmac.new(secret, f'{head}.{body}'.encode(), hashlib.sha256).digest())}"


def _public_pem() -> bytes:
    return KEY.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


# ---- a valid token signs the driver in ------------------------------------------------------------------------

def test_apple_sign_in_creates_an_account_and_a_session(client):
    who = _person()
    r = _apple(client, who, name="  Sam Driver ", authorization_code="c" * 60, platform="ios")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["new_account"] is True and body["has_profile"] is False
    assert body["name"] == "Sam Driver" and body["email"] == f"{who}@privaterelay.appleid.com"
    assert body["token_type"] == "bearer" and body["role"] == "driver" and body["refresh_token"]
    assert body["expires_in"] == auth.JWT_DAYS * 86400
    claims = _claims(body["access_token"])
    assert claims["sub"].startswith("drv_") and len(claims["sub"]) == 36  # the same format as a device token's sub
    assert claims["sid"].startswith("ses_") and claims["org_id"] and claims["driver_id"]
    assert client.get("/api/auth/check", headers=_h(body)).status_code == 200
    # one account, one session for this phone, and the usual device/driver/org rows for the sub
    assert _rows(client, "SELECT sub, provider, apple_refresh_enc FROM accounts WHERE provider_sub = ?", who) == \
        [(claims["sub"], "apple", None)]
    assert _rows(client, "SELECT id, platform FROM account_sessions WHERE sub = ?", claims["sub"]) == [(claims["sid"], "ios")]
    assert _rows(client, "SELECT role FROM devices WHERE sub = ?", claims["sub"]) == [("driver",)]


def test_google_sign_in_takes_name_and_email_from_the_token(client):
    who = _person()
    body = _google(client, who).json()
    assert body["new_account"] is True and body["name"] == "Sam Driver" and body["email"] == f"{who}@gmail.com"
    # the other client id of the list and the other spelling of the issuer are Google too
    again = _google(client, who, _sign(_google_claims(who, aud=GOOGLE_WEB, iss="accounts.google.com")))
    assert again.status_code == 200 and again.json()["new_account"] is False
    assert _claims(again.json()["access_token"])["sub"] == _claims(body["access_token"])["sub"]


def test_a_token_a_minute_past_its_exp_still_passes(client):
    who = _person()
    now = int(time.time())
    assert _apple(client, who, _sign(_apple_claims(who, exp=now - 30))).status_code == 200  # clock skew
    assert _apple(client, who, _sign(_apple_claims(who, exp=now - 300))).status_code == 401


def test_the_same_id_at_apple_and_at_google_are_two_accounts(client):
    who = _person()
    a, g = _apple(client, who).json(), _google(client, who).json()
    assert a["new_account"] and g["new_account"]
    assert _claims(a["access_token"])["sub"] != _claims(g["access_token"])["sub"]


# ---- a token that proves nothing is refused, always the same way -----------------------------------------------

def _bad_apple_tokens(who: str) -> dict[str, str]:
    good = _apple_claims(who)
    return {
        "another app's audience": _sign({**good, "aud": "com.someone.else"}),
        "expired": _sign({**good, "exp": int(time.time()) - 3600}),
        "another issuer": _sign({**good, "iss": "https://accounts.google.com"}),
        "nonce of another sign-in": _sign({**good, "nonce": hashlib.sha256(b"other").hexdigest()}),
        "the raw nonce, not its hash": _sign({**good, "nonce": NONCE}),
        "no nonce": _sign({k: v for k, v in good.items() if k != "nonce"}),
        "no exp": _sign({k: v for k, v in good.items() if k != "exp"}),
        "alg none": jwt.encode(good, None, algorithm="none", headers={"kid": "k1"}),
        "HS256 keyed with the public key": _hs256(good, _public_pem()),
        "signed by a stranger under our kid": _sign(good, key=STRANGER),
        "no kid": jwt.encode(good, KEY, algorithm="RS256"),
        "not a JWT": "x" * 40,
    }


@pytest.mark.parametrize("case", list(_bad_apple_tokens("x")))
def test_a_bad_apple_token_is_401_without_details(client, case, caplog):
    who = _person()
    token = _bad_apple_tokens(who)[case]
    with caplog.at_level(logging.INFO, logger="truckbuddy"):
        r = _apple(client, who, token)
    assert r.status_code == 401 and r.json() == {"detail": "Sign-in failed"}, case
    assert _rows(client, "SELECT 1 FROM accounts WHERE provider_sub = ?", who) == []
    refused = [rec.getMessage() for rec in caplog.records if "sign-in refused" in rec.getMessage()]
    assert len(refused) == 1 and token not in caplog.text  # the reason is logged, the token never


def test_a_wrong_nonce_from_the_phone_is_401(client):
    who = _person()
    r = client.post("/api/auth/apple", json={"identity_token": _sign(_apple_claims(who)), "nonce": "not-the-nonce"})
    assert r.status_code == 401 and r.json() == {"detail": "Sign-in failed"}


@pytest.mark.parametrize("over", [{"email_verified": False}, {"email_verified": "false"}, {"aud": "other.apps.googleusercontent.com"},
                                  {"iss": "https://appleid.apple.com"}, {"exp": 1}])
def test_a_bad_google_token_is_401(client, over):
    who = _person()
    r = _google(client, who, _sign(_google_claims(who, **over)))
    assert r.status_code == 401 and r.json() == {"detail": "Sign-in failed"}
    assert _rows(client, "SELECT 1 FROM accounts WHERE provider_sub = ?", who) == []


def test_google_without_email_verified_is_401(client):
    who = _person()
    claims = {k: v for k, v in _google_claims(who).items() if k != "email_verified"}
    assert _google(client, who, _sign(claims)).status_code == 401


def test_an_apple_token_does_not_sign_in_at_google(client):
    who = _person()
    assert _google(client, who, _sign(_apple_claims(who))).status_code == 401


def test_oversized_fields_are_rejected_before_any_work(client, jwks):
    assert _apple(client, "x", "a" * 5000).status_code == 422
    assert client.post("/api/auth/apple", json={"identity_token": "a" * 100, "nonce": "n" * 200}).status_code == 422
    assert _apple(client, "x", name="n" * 121).status_code == 422
    assert _apple(client, "x", authorization_code="c" * 3000).status_code == 422
    assert _google(client, "x", "a" * 5000).status_code == 422
    assert _google(client, "x", platform="windows").status_code == 422
    assert jwks.fetches() == 0 and jwks.fetches(GOOGLE_HOST) == 0


# ---- the provider's keys ---------------------------------------------------------------------------------------

def test_the_keys_are_fetched_once_and_cached(client, jwks):
    for _ in range(3):
        assert _apple(client, _person()).status_code == 200
    assert jwks.fetches() == 1 and jwks.fetches(GOOGLE_HOST) == 0
    assert _google(client, _person()).status_code == 200
    assert jwks.fetches(GOOGLE_HOST) == 1  # each provider has its own keys


def test_an_unknown_kid_refetches_once_and_then_signs_in(client, jwks):
    assert _apple(client, _person()).status_code == 200 and jwks.fetches() == 1
    jwks.keys = {"k2": ROTATED}  # the provider rotated
    who = _person()
    assert _apple(client, who, _sign(_apple_claims(who), key=ROTATED, kid="k2")).status_code == 200
    assert jwks.fetches() == 2
    assert _apple(client, who, _sign(_apple_claims(who), key=ROTATED, kid="k2")).status_code == 200
    assert jwks.fetches() == 2  # k2 is cached now
    assert _apple(client, who, _sign(_apple_claims(who))).status_code == 401  # k1 is retired: one fruitless refetch
    assert jwks.fetches() == 3


def test_a_made_up_kid_does_not_refetch_in_a_loop(client, jwks):
    assert _apple(client, _person()).status_code == 200 and jwks.fetches() == 1
    who = _person()
    for _ in range(5):
        assert _apple(client, who, _sign(_apple_claims(who), kid="made-up")).status_code == 401
    assert jwks.fetches() == 2  # asked once; not again within the minute
    assert _apple(client, who).status_code == 200  # known kids are not affected
    identity._jwks[identity.APPLE.jwks_url].missed -= identity.REFETCH_MIN_S + 1  # a minute later
    assert _apple(client, who, _sign(_apple_claims(who), kid="made-up")).status_code == 401
    assert jwks.fetches() == 3


def test_the_keys_are_refetched_after_six_hours(client, jwks):
    assert _apple(client, _person()).status_code == 200
    identity._jwks[identity.APPLE.jwks_url].at -= identity.JWKS_TTL_S + 1
    assert _apple(client, _person()).status_code == 200 and jwks.fetches() == 2


def test_provider_keys_down(client, jwks):
    jwks.down = True
    r = _apple(client, _person())
    assert r.status_code == 503 and r.json() == {"detail": "Sign-in is unavailable right now"}  # nothing to check against
    jwks.down = False
    assert _apple(client, _person()).status_code == 200  # a failed fetch is retried by the next sign-in
    jwks.down = True
    identity._jwks[identity.APPLE.jwks_url].at -= identity.JWKS_TTL_S + 1
    assert _apple(client, _person()).status_code == 200  # old keys beat no sign-in while the provider is down


@pytest.mark.parametrize("value", [None, "", " , "])
def test_without_audiences_sign_in_is_503_and_nothing_is_accepted(client, jwks, monkeypatch, value):
    for env in ("APPLE_AUDIENCES", "GOOGLE_CLIENT_IDS"):
        monkeypatch.delenv(env) if value is None else monkeypatch.setenv(env, value)
    who = _person()
    for r in (_apple(client, who), _google(client, who)):
        assert r.status_code == 503 and r.json() == {"detail": "Sign-in is not configured"}
    assert jwks.fetches() == 0 and _rows(client, "SELECT 1 FROM accounts WHERE provider_sub = ?", who) == []


def test_without_a_store_sign_in_is_503(client, monkeypatch):
    monkeypatch.setenv("MEMORY_STORE", "off")
    assert _apple(client, _person()).status_code == 503


# ---- the account's sub is where its data lives -------------------------------------------------------------------

def test_signing_in_again_returns_the_same_sub_and_its_data(client):
    who = _person()
    first = _apple(client, who).json()
    sub = _claims(first["access_token"])["sub"]
    assert client.put("/api/me/profile", json=PROFILE, headers=_h(first)).status_code == 200
    client.portal.call(memory.add, sub, [("driver", "find me diesel"), ("copilot", "Pilot is 4 miles ahead.")])

    tenancy.reset_caches()  # another phone, another worker
    second = _apple(client, who).json()
    assert second["new_account"] is False and second["has_profile"] is True
    assert _claims(second["access_token"])["sub"] == sub
    assert _claims(second["access_token"])["sid"] != _claims(first["access_token"])["sid"]
    assert client.get("/api/me/profile", headers=_h(second)).json()["height_in"] == 162
    turns = client.get("/api/memory", headers=_h(second)).json()["turns"]
    assert [t["text"] for t in turns] == ["find me diesel", "Pilot is 4 miles ahead."]
    assert len(_rows(client, "SELECT 1 FROM accounts WHERE provider_sub = ?", who)) == 1
    assert len(_rows(client, "SELECT 1 FROM devices WHERE sub = ?", sub)) == 1


def test_has_profile_turns_true_once_a_profile_is_saved(client):
    who = _person()
    first = _google(client, who).json()
    assert first["has_profile"] is False
    assert _google(client, who).json()["has_profile"] is False
    assert client.put("/api/me/profile", json=PROFILE, headers=_h(first)).status_code == 200
    assert _google(client, who).json()["has_profile"] is True


def test_has_profile_reads_the_store_not_this_workers_copy(client):
    who = _person()
    first = _google(client, who).json()
    sub = _claims(first["access_token"])["sub"]
    assert client.get("/api/me/profile", headers=_h(first)).status_code == 404  # "no profile" is cached for a minute
    driver_id = _claims(first["access_token"])["driver_id"]
    _rows(client, "UPDATE drivers SET profile = ? WHERE id = ?", json.dumps(PROFILE), driver_id)  # saved through another worker
    assert _google(client, who).json()["has_profile"] is True and sub


def test_the_first_name_is_kept_and_the_email_follows_the_provider(client):
    who = _person()
    assert _apple(client, who, name="Sam").json()["name"] == "Sam"
    assert _apple(client, who).json()["name"] == "Sam"  # Apple sends no name after the first authorization
    assert _apple(client, who, name="Somebody Else").json()["name"] == "Sam"
    moved = _apple(client, who, _sign(_apple_claims(who, email="new@example.com"))).json()
    assert moved["email"] == "new@example.com" and moved["name"] == "Sam"
    no_email = {k: v for k, v in _apple_claims(who).items() if k != "email"}
    assert _apple(client, who, _sign(no_email)).json()["email"] == "new@example.com"  # a token without one changes nothing


def test_a_name_can_arrive_on_a_later_sign_in(client):
    who = _person()
    assert _apple(client, who).json()["name"] is None
    assert _apple(client, who, name="Sam").json()["name"] == "Sam"


def test_two_first_sign_ins_at_once_make_one_account(client):
    who = _person()
    token = _sign(_google_claims(who))
    n = 8
    gate = threading.Barrier(n)

    def sign_in(_):
        gate.wait()
        return _google(client, who, token)
    with ThreadPoolExecutor(n) as pool:
        got = list(pool.map(sign_in, range(n)))
    assert [r.status_code for r in got] == [200] * n
    assert len({_claims(r.json()["access_token"])["sub"] for r in got}) == 1
    assert sum(r.json()["new_account"] for r in got) == 1  # exactly one of them made it
    assert len({_claims(r.json()["access_token"])["sid"] for r in got}) == n  # but each is a session of its own
    sub = _claims(got[0].json()["access_token"])["sub"]
    assert _rows(client, "SELECT sub FROM accounts WHERE provider_sub = ?", who) == [(sub,)]
    assert len(_rows(client, "SELECT 1 FROM devices WHERE sub = ?", sub)) == 1
    assert len(_rows(client, "SELECT 1 FROM account_sessions WHERE sub = ?", sub)) == n


def test_find_or_create_under_a_race_hands_everyone_the_winners_sub(client):
    """The same race one layer down, with the inserts interleaved on one event loop."""
    import asyncio
    who = _person()

    async def race():
        return await asyncio.gather(*[accounts.find_or_create("apple", who, auth.new_sub(), None, None) for _ in range(10)])
    got = client.portal.call(race)
    assert len({acc.sub for acc, _ in got}) == 1 and sum(new for _, new in got) == 1
    assert len(_rows(client, "SELECT 1 FROM accounts WHERE provider_sub = ?", who)) == 1


# ---- sessions: one per phone -----------------------------------------------------------------------------------

def _refresh(c, body: dict):
    return c.post("/api/auth/refresh", json={"refresh_token": body["refresh_token"]})


def test_two_phones_refresh_independently(client):
    who = _person()
    a, b = _apple(client, who).json(), _apple(client, who).json()
    sub, sid_a, sid_b = _claims(a["access_token"])["sub"], _claims(a["access_token"])["sid"], _claims(b["access_token"])["sid"]
    a2 = _refresh(client, a)
    assert a2.status_code == 200
    assert _claims(a2.json()["access_token"])["sub"] == sub and _claims(a2.json()["access_token"])["sid"] == sid_a
    assert _refresh(client, a).status_code == 401  # rotated: the old one is spent
    b2 = _refresh(client, b)  # phone B's token is untouched by A's rotation
    assert b2.status_code == 200 and _claims(b2.json()["access_token"])["sid"] == sid_b
    assert _refresh(client, a2.json()).status_code == 200
    assert _rows(client, "SELECT refresh_hash FROM devices WHERE sub = ?", sub) == [(None,)]  # nothing on the device row
    for body in (a, b, a2.json(), b2.json()):
        assert client.get("/api/auth/check", headers=_h(body)).status_code == 200


def test_logout_ends_only_its_own_session(client):
    who = _person()
    a, b = _apple(client, who).json(), _apple(client, who).json()
    assert client.post("/api/auth/logout", headers=_h(a)).json() == {"ok": True}
    r = client.get("/api/auth/check", headers=_h(a))
    assert r.status_code == 401 and r.json() == {"detail": GONE}
    assert _refresh(client, a).status_code == 401  # its refresh token went with the session
    assert client.post("/api/auth/renew", headers=_h(a)).status_code == 401
    assert client.post("/api/auth/logout", headers=_h(a)).json() == {"ok": True}  # again: nothing left to do
    # the other phone never notices
    assert client.get("/api/auth/check", headers=_h(b)).status_code == 200
    assert _refresh(client, b).status_code == 200
    sub = _claims(b["access_token"])["sub"]
    assert _rows(client, "SELECT id FROM account_sessions WHERE sub = ?", sub) == [(_claims(b["access_token"])["sid"],)]
    # signing in again on the first phone is a new session on the same account
    back = _apple(client, who).json()
    assert back["new_account"] is False and _claims(back["access_token"])["sub"] == sub


def test_logout_needs_a_token_this_server_signed(client):
    assert client.post("/api/auth/logout").status_code == 401
    assert client.post("/api/auth/logout", headers={"Authorization": "Bearer nonsense"}).status_code == 401


def test_a_session_closed_by_another_worker_is_refused_within_the_cache_minute(client):
    who = _person()
    a = _apple(client, who).json()
    assert client.get("/api/auth/check", headers=_h(a)).status_code == 200
    _rows(client, "DELETE FROM account_sessions WHERE id = ?", _claims(a["access_token"])["sid"])  # not through this worker
    assert client.get("/api/auth/check", headers=_h(a)).status_code == 200  # this worker's copy, up to a minute old
    r = client.post("/api/auth/renew", headers=_h(a))  # but no new token is minted for a session that is gone
    assert r.status_code == 401 and r.json() == {"detail": GONE}
    accounts.reset_caches()  # the minute passed
    r = client.get("/api/auth/check", headers=_h(a))
    assert r.status_code == 401 and r.json() == {"detail": GONE}


def test_a_session_does_not_vouch_for_another_sub(client):
    who = _person()
    a = _apple(client, who).json()
    forged = auth._token("driver", "drv_" + "0" * 32, {"sid": _claims(a["access_token"])["sid"]})
    assert client.get("/api/auth/check", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_renew_keeps_the_account_and_the_session(client):
    who = _person()
    a = _apple(client, who).json()
    r = client.post("/api/auth/renew", headers=_h(a))
    assert r.status_code == 200
    was, now = _claims(a["access_token"]), _claims(r.json()["access_token"])
    assert (now["sub"], now["sid"]) == (was["sub"], was["sid"])
    assert _refresh(client, a).status_code == 401 and _refresh(client, r.json()).status_code == 200


def test_a_fleet_code_keeps_the_session_in_the_new_token(client):
    who = _person()
    a = _apple(client, who).json()

    async def fleet():
        org = await tenancy.create_org("Acme")
        return await tenancy.create_fleet_code(org, await tenancy.create_vehicle(org, "Truck 7"))
    r = client.post("/api/auth/fleet-code", json={"code": client.portal.call(fleet)}, headers=_h(a))
    assert r.status_code == 200, r.text
    bound = _claims(r.json()["access_token"])
    assert bound["sid"] == _claims(a["access_token"])["sid"] and bound["vehicle_id"] == r.json()["vehicle_id"]
    assert client.post("/api/auth/logout", headers=_h(r.json())).status_code == 200
    assert client.get("/api/auth/check", headers=_h(r.json())).status_code == 401


def test_seen_at_moves_at_most_once_an_hour(client):
    who = _person()
    a = _apple(client, who).json()
    sid = _claims(a["access_token"])["sid"]
    old = datetime(2025, 1, 1, tzinfo=timezone.utc)

    def age():
        _rows(client, "UPDATE account_sessions SET seen_at = ? WHERE id = ?", db.iso(old) if client.store == "sqlite" else old, sid)

    def seen() -> str:
        return str(_rows(client, "SELECT seen_at FROM account_sessions WHERE id = ?", sid)[0][0])
    age()
    assert client.get("/api/auth/check", headers=_h(a)).status_code == 200
    assert seen() > "2026"
    age()
    assert client.get("/api/auth/check", headers=_h(a)).status_code == 200
    assert seen().startswith("2025")  # written a moment ago: not again for an hour


# ---- the per-IP limit -------------------------------------------------------------------------------------------

def test_sign_ins_are_limited_per_ip(client, monkeypatch):
    monkeypatch.setattr(auth, "MAX_SIGNINS_PER_IP", 3)
    who = _person()
    assert _apple(client, who).status_code == 200
    assert _apple(client, who, _sign(_apple_claims(who, aud="other"))).status_code == 401  # failures count too
    assert _google(client, who).status_code == 200  # and both providers share the window
    r = _apple(client, who)
    assert r.status_code == 429 and r.json() == {"detail": "Too many attempts. Try again in a few minutes."}
    assert _google(client, who).status_code == 429
    other_ip = client.post("/api/auth/google", json={"id_token": _sign(_google_claims(who))},
                           headers={"X-Forwarded-For": "203.0.113.9"})
    assert other_ip.status_code == 200
    monkeypatch.setattr(auth, "MAX_SIGNINS_PER_IP", 0)  # 0 = off
    assert _apple(client, who).status_code == 200


# ---- app build 2 and access codes: device tokens work as before --------------------------------------------------

def test_device_tokens_are_untouched_by_accounts(client):
    body = client.post("/api/auth/device").json()
    claims = _claims(body["access_token"])
    assert "sid" not in claims
    assert client.get("/api/auth/check", headers=_h(body)).status_code == 200
    refreshed = _refresh(client, body)
    assert refreshed.status_code == 200 and _claims(refreshed.json()["access_token"])["sub"] == claims["sub"]
    assert "sid" not in _claims(refreshed.json()["access_token"])
    assert _refresh(client, body).status_code == 401  # rotated on the device row, as before
    renewed = client.post("/api/auth/renew", headers=_h(refreshed.json()))
    assert renewed.status_code == 200 and _claims(renewed.json()["access_token"])["sub"] == claims["sub"]
    assert _refresh(client, renewed.json()).status_code == 200
    # a device has no session: logout answers ok and changes nothing
    assert client.post("/api/auth/logout", headers=_h(body)).json() == {"ok": True}
    assert client.get("/api/auth/check", headers=_h(body)).status_code == 200
    assert _rows(client, "SELECT 1 FROM account_sessions WHERE sub = ?", claims["sub"]) == []
    assert _rows(client, "SELECT 1 FROM accounts WHERE sub = ?", claims["sub"]) == []
