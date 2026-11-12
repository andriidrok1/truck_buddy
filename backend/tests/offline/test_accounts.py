"""Sign in with Apple and Google (services/identity.py, services/accounts.py, /api/auth/apple|google|logout).

The provider is a test RSA key and a stubbed JWKS endpoint: no call leaves the process. Every test runs on SQLite
and, when TEST_DATABASE_URL points at a throwaway Postgres, on Postgres too (the store production uses)."""
import base64
import hashlib
import hmac
import json
import logging
import os
import asyncio
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from services import accounts, auth, db, identity, memory, tenancy

APPLE_HOST, GOOGLE_HOST = "appleid.apple.com", "www.googleapis.com"
BUNDLE = "io.truckbuddy.ios"
GOOGLE_IOS, GOOGLE_WEB = "ios-123.apps.googleusercontent.com", "web-456.apps.googleusercontent.com"
NONCE = "raw-nonce-from-the-phone"
PROFILE = {"height_in": 162, "weight_lbs": 80000, "length_ft": 70, "trailer_ft": 53, "axles": 5, "hazmat": False}
GONE = "This device is no longer registered"
CLOSED = {"detail": GONE, "code": "session_closed"}  # the account is there, this session is not
ACCOUNT_GONE = {"detail": GONE, "code": "account_gone"}  # the account was deleted

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
    auth._logouts.clear()
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


def _expired(body: dict, days_ago: float, **over) -> dict:
    """The same access token, issued so that it expired `days_ago` days ago."""
    claims = jwt.decode(body["access_token"], auth._secret(), algorithms=["HS256"])
    now = datetime.now(timezone.utc)
    claims.update(iat=now - timedelta(days=auth.JWT_DAYS + days_ago), exp=now - timedelta(days=days_ago), **over)
    return {"access_token": jwt.encode(claims, auth._secret(), algorithm="HS256")}


def _jwks_later(seconds: float, provider=identity.APPLE) -> None:
    """Move this provider's key cache `seconds` into the past (as if that much time went by)."""
    c = identity._jwks[provider.jwks_url]
    c.tried, c.at = c.tried - seconds, (c.at - seconds if c.at is not None else None)


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
        "ES256 under our kid": jwt.encode(good, ec.generate_private_key(ec.SECP256R1()), algorithm="ES256", headers={"kid": "k1"}),
        "not valid yet (nbf)": _sign({**good, "nbf": int(time.time()) + 3600}),
        "issued in the future (iat)": _sign({**good, "iat": int(time.time()) + 3600}),
        "no sub": _sign({k: v for k, v in good.items() if k != "sub"}),
        "empty sub": _sign({**good, "sub": ""}),
        "sub is a number": _sign({**good, "sub": 12345}),
        "sub is a list": _sign({**good, "sub": [who]}),
        "nonce with non-ASCII characters": _sign({**good, "nonce": "\u00e9" * 10}),
        "nonce is not a string": _sign({**good, "nonce": 7}),
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
    assert _rows(client, "SELECT 1 FROM accounts WHERE provider = 'apple' AND provider_sub IN ('', '12345')") == []
    refused = [rec.getMessage() for rec in caplog.records if "sign-in refused" in rec.getMessage()]
    assert len(refused) == 1 and token not in caplog.text  # the reason is logged, the token never


def test_a_wrong_nonce_from_the_phone_is_401(client):
    who = _person()
    r = client.post("/api/auth/apple", json={"identity_token": _sign(_apple_claims(who)), "nonce": "not-the-nonce"})
    assert r.status_code == 401 and r.json() == {"detail": "Sign-in failed"}


def test_a_nonce_that_cannot_be_encoded_is_a_refusal_not_a_crash():
    """A lone surrogate. Over HTTP pydantic turns it away before the endpoint runs; this is the function itself."""
    async def check():
        async def claims(provider, token):
            return _apple_claims("u-1")
        orig, identity._verify = identity._verify, claims
        try:
            with pytest.raises(identity.Invalid):
                await identity.verify_apple("token", "\ud800" + "a" * 10)
        finally:
            identity._verify = orig
    asyncio.run(check())


EVIL = "evil.apps.googleusercontent.com"


@pytest.mark.parametrize("over", [{"email_verified": False}, {"email_verified": "false"}, {"aud": "other.apps.googleusercontent.com"},
                                  {"iss": "https://appleid.apple.com"}, {"exp": 1}, {"sub": ""}, {"sub": 12345}, {"sub": None},
                                  {"azp": EVIL}, {"aud": [GOOGLE_IOS, EVIL], "azp": EVIL}, {"aud": [GOOGLE_IOS, EVIL]},
                                  {"nbf": int(time.time()) + 3600}, {"iat": int(time.time()) + 3600}])
def test_a_bad_google_token_is_401(client, over):
    who = _person()
    r = _google(client, who, _sign(_google_claims(who, **over)))
    assert r.status_code == 401 and r.json() == {"detail": "Sign-in failed"}
    assert _rows(client, "SELECT 1 FROM accounts WHERE provider_sub = ?", who) == []


def test_google_without_email_verified_is_401(client):
    who = _person()
    claims = {k: v for k, v in _google_claims(who).items() if k != "email_verified"}
    assert _google(client, who, _sign(claims)).status_code == 401


def test_google_azp_of_our_own_clients_passes(client):
    """Android: the audience is the web client, the token was issued to the Android one. Both are in the list."""
    who = _person()
    assert _google(client, who, _sign(_google_claims(who, aud=GOOGLE_WEB, azp=GOOGLE_IOS))).status_code == 200
    assert _google(client, who, _sign(_google_claims(who, aud=[GOOGLE_WEB, GOOGLE_IOS], azp=GOOGLE_IOS))).status_code == 200


def test_odd_email_and_name_claims_are_dropped_not_stored(client):
    who = _person()
    body = _google(client, who, _sign(_google_claims(who, email=["a@b.c"], name={"x": 1}))).json()
    assert body["email"] is None and body["name"] is None


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
    _jwks_later(identity.REFETCH_MIN_S + 1)
    who = _person()
    assert _apple(client, who, _sign(_apple_claims(who), key=ROTATED, kid="k2")).status_code == 200
    assert jwks.fetches() == 2
    assert _apple(client, who, _sign(_apple_claims(who), key=ROTATED, kid="k2")).status_code == 200
    assert jwks.fetches() == 2  # k2 is cached now
    _jwks_later(identity.REFETCH_MIN_S + 1)
    assert _apple(client, who, _sign(_apple_claims(who))).status_code == 401  # k1 is retired: one fruitless refetch
    assert jwks.fetches() == 3


def test_a_made_up_kid_does_not_refetch_in_a_loop(client, jwks):
    assert _apple(client, _person()).status_code == 200 and jwks.fetches() == 1
    who = _person()
    for _ in range(5):
        assert _apple(client, who, _sign(_apple_claims(who), kid="made-up")).status_code == 401
    assert jwks.fetches() == 1  # the keys were fetched within the last minute: not asked again
    _jwks_later(identity.REFETCH_MIN_S + 1)
    for _ in range(5):
        assert _apple(client, who, _sign(_apple_claims(who), kid=f"made-up-{_}")).status_code == 401
    assert jwks.fetches() == 2  # one refetch a minute, however many unknown kids arrive
    assert _apple(client, who).status_code == 200  # known kids are not affected


def test_the_keys_are_refetched_after_six_hours(client, jwks):
    assert _apple(client, _person()).status_code == 200
    _jwks_later(identity.JWKS_TTL_S - 60)
    assert _apple(client, _person()).status_code == 200 and jwks.fetches() == 1
    _jwks_later(120)
    assert _apple(client, _person()).status_code == 200 and jwks.fetches() == 2


def test_provider_keys_down(client, jwks):
    jwks.down = True
    for _ in range(10):
        r = _apple(client, _person())
        assert r.status_code == 503 and r.json() == {"detail": "Sign-in is unavailable right now"}  # nothing to check against
    assert jwks.fetches() == 1  # one attempt per throttle window; the rest are answered at once
    jwks.down = False
    assert _apple(client, _person()).status_code == 503  # still inside the window
    _jwks_later(identity.REFETCH_MIN_S + 1)
    assert _apple(client, _person()).status_code == 200 and jwks.fetches() == 2
    # the keys go stale while the provider is down: the old ones keep signing people in, one attempt a minute
    jwks.down = True
    _jwks_later(identity.JWKS_TTL_S + 1)
    for _ in range(10):
        assert _apple(client, _person()).status_code == 200
    assert jwks.fetches() == 3
    who = _person()  # a kid we cannot ask about is "unavailable", not "invalid": the token was not judged
    assert _apple(client, who, _sign(_apple_claims(who), key=ROTATED, kid="k2")).status_code == 503
    assert jwks.fetches() == 3
    assert not any(auth._signins.values())  # and none of it counted as a failed sign-in


def test_concurrent_sign_ins_share_one_key_fetch(client, monkeypatch):
    fetches = []

    async def slow(url):
        fetches.append(url)
        await asyncio.sleep(0.05)
        return {"k1": KEY.public_key()}
    monkeypatch.setattr(identity, "_fetch", slow)

    async def flood(kids):
        return await asyncio.gather(*[identity._key(identity.APPLE, k) for k in kids], return_exceptions=True)
    got = client.portal.call(flood, ["k1"] * 10 + [f"made-up-{i}" for i in range(10)])  # cold cache
    assert len(fetches) == 1
    assert [type(g).__name__ for g in got] == ["RSAPublicKey"] * 10 + ["Invalid"] * 10
    client.portal.call(flood, [f"another-{i}" for i in range(20)])
    assert len(fetches) == 1  # and the throttle holds under concurrency

    async def down(url):
        fetches.append(url)
        await asyncio.sleep(0.05)
        raise RuntimeError("down")
    monkeypatch.setattr(identity, "_fetch", down)
    identity.reset()
    fetches.clear()
    got = client.portal.call(flood, [f"kid-{i}" for i in range(20)])
    assert len(fetches) == 1 and {type(g).__name__ for g in got} == {"Unavailable"}


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


def _fleet_code(client) -> str:
    async def fleet():
        org = await tenancy.create_org("Acme")
        return await tenancy.create_fleet_code(org, await tenancy.create_vehicle(org, "Truck 7"))
    return client.portal.call(fleet)


def test_has_profile_means_setup_is_done(client):
    """Two ways to be done: an own profile (above) or a fleet truck. A stored name alone is not setup."""
    # a fleet driver on a fleet truck: the sizes are the fleet's, profile_put stores nothing of theirs but the name
    who = _person()
    a = _apple(client, who).json()
    bound = client.post("/api/auth/fleet-code", json={"code": _fleet_code(client)}, headers=_h(a)).json()
    assert _apple(client, who).json()["has_profile"] is True
    assert client.put("/api/me/profile", json={**PROFILE, "driver_name": "Sam"}, headers=_h(bound)).status_code == 200
    again = _apple(client, who).json()
    assert again["has_profile"] is True and again["new_account"] is False
    # a name alone: GET /api/me/profile has nothing to give, so the app must still run onboarding
    who = _person()
    b = _apple(client, who).json()
    assert _apple(client, who).json()["has_profile"] is False
    _rows(client, "UPDATE drivers SET display_name = ? WHERE id = ?", "Sam", _claims(b["access_token"])["driver_id"])
    assert _apple(client, who).json()["has_profile"] is False
    assert client.get("/api/me/profile", headers=_h(b)).status_code == 404
    assert client.put("/api/me/profile", json={**PROFILE, "driver_name": "Sam"}, headers=_h(b)).status_code == 200
    assert _apple(client, who).json()["has_profile"] is True


def test_a_first_sign_in_that_fails_halfway_leaves_no_session_and_is_still_first(client, monkeypatch):
    who = _person()
    real = tenancy.solo_org_statements

    def boom(sub, role, at):  # the last of the tenancy inserts fails: the org and driver rows before it are rolled back
        return [*real(sub, role, at)[:2], ("INSERT INTO no_such_table (sub) VALUES (?)", (sub,))]
    monkeypatch.setattr(tenancy, "solo_org_statements", boom)
    r = _apple(client, who, name="Sam")
    assert r.status_code == 503 and r.json() == {"detail": "Sign-in is unavailable right now"}
    assert _rows(client, "SELECT 1 FROM account_sessions s JOIN accounts a ON a.sub = s.sub WHERE a.provider_sub = ?", who) == []
    (sub,), = _rows(client, "SELECT sub FROM accounts WHERE provider_sub = ?", who)
    assert _rows(client, "SELECT 1 FROM devices WHERE sub = ?", sub) == []
    monkeypatch.setattr(tenancy, "solo_org_statements", real)
    body = _apple(client, who).json()
    assert body["new_account"] is True and body["has_profile"] is False and body["name"] == "Sam"
    sub = _claims(body["access_token"])["sub"]
    assert len(_rows(client, "SELECT 1 FROM account_sessions WHERE sub = ?", sub)) == 1
    assert _apple(client, who).json()["new_account"] is False


def test_no_session_ever_holds_a_placeholder_hash(client):
    who = _person()
    body = _apple(client, who).json()
    (stored,), = _rows(client, "SELECT refresh_hash FROM account_sessions WHERE id = ?", _claims(body["access_token"])["sid"])
    assert stored == hashlib.sha256(body["refresh_token"].encode()).hexdigest()


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
    assert any(r.json()["new_account"] for r in got)  # whoever got in before the device row existed
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
    assert len({acc.sub for acc in got}) == 1
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
    assert r.status_code == 401 and r.json() == CLOSED
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


def test_a_session_closed_by_another_worker_is_refused_within_the_cache_minute(client, monkeypatch):
    who = _person()
    a = _apple(client, who).json()
    assert client.get("/api/auth/check", headers=_h(a)).status_code == 200
    _rows(client, "DELETE FROM account_sessions WHERE id = ?", _claims(a["access_token"])["sid"])  # not through this worker
    assert client.get("/api/auth/check", headers=_h(a)).status_code == 200  # this worker's copy, up to a minute old
    r = client.post("/api/auth/renew", headers=_h(a))  # but no new token is minted for a session that is gone
    assert r.status_code == 401 and r.json() == CLOSED
    clock = {"now": time.monotonic()}
    monkeypatch.setattr(accounts, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    clock["now"] += accounts.SESSION_TTL_S - 2
    assert client.get("/api/auth/check", headers=_h(a)).status_code == 200
    clock["now"] += 3  # the minute passed: the row is read again
    r = client.get("/api/auth/check", headers=_h(a))
    assert r.status_code == 401 and r.json() == CLOSED


def test_a_closed_session_and_a_deleted_account_answer_different_codes(client, monkeypatch):
    """The app shows "account deleted" only on account_gone. A phone signed out from another one, with the account
    still there, gets session_closed, also from DELETE /api/account (review of #173)."""
    who = _person()
    a, b = _apple(client, who).json(), _apple(client, who).json()
    assert client.post("/api/auth/logout-all", headers=_h(b)).status_code == 200
    for call in (lambda: client.delete("/api/account", headers=_h(a)), lambda: client.get("/api/auth/check", headers=_h(a)),
                 lambda: client.post("/api/auth/renew", headers=_h(a))):
        r = call()
        assert r.status_code == 401 and r.json() == CLOSED
        assert r.headers["content-type"].startswith("application/json")
    assert _apple(client, who).json()["new_account"] is False  # the account is indeed there

    # deleted on this worker: account_gone at once
    c = _apple(client, who).json()
    d = _apple(client, who).json()
    assert client.delete("/api/account", headers=_h(c)).status_code == 200
    for body in (c, d):
        assert client.get("/api/auth/check", headers=_h(body)).json() == ACCOUNT_GONE
        assert client.post("/api/auth/renew", headers=_h(body)).json() == ACCOUNT_GONE


def test_the_cached_answer_says_closed_when_in_doubt(client, monkeypatch):
    """Another worker's copy: a session it already knows closed stays session_closed until the copy is a minute old,
    even when the account goes meanwhile; then the store is read again and says account_gone. A store that cannot
    say whether the account is there answers session_closed."""
    a = _apple(client, _person()).json()
    sub = _claims(a["access_token"])["sub"]
    _rows(client, "DELETE FROM account_sessions WHERE id = ?", _claims(a["access_token"])["sid"])  # closed elsewhere
    accounts.reset_caches()
    assert client.get("/api/auth/check", headers=_h(a)).json() == CLOSED  # read now: the account is there
    clock = {"now": time.monotonic()}
    monkeypatch.setattr(accounts, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    client.portal.call(accounts.erase, sub)  # as another worker deletes the account
    accounts._sessions[_claims(a["access_token"])["sid"]] = (None, clock["now"], accounts.CLOSED)  # this worker's copy
    assert client.get("/api/auth/check", headers=_h(a)).json() == CLOSED
    clock["now"] += accounts.SESSION_TTL_S + 1
    assert client.get("/api/auth/check", headers=_h(a)).json() == ACCOUNT_GONE
    # the account lookup fails: closed, not gone
    c = _apple(client, _person()).json()
    _rows(client, "DELETE FROM account_sessions WHERE id = ?", _claims(c["access_token"])["sid"])
    accounts.reset_caches()

    async def down(sub):
        raise RuntimeError("store is down")
    monkeypatch.setattr(accounts, "account_exists", down)
    assert client.get("/api/auth/check", headers=_h(c)).json() == CLOSED


def test_a_store_error_in_the_session_check_lets_the_request_through(client, monkeypatch):
    """Deliberate: a database hiccup must not sign every driver out (the app drops its token on this 401). The
    price is that a closed session keeps working on this worker while the store is down. Change it knowingly."""
    who = _person()
    a = _apple(client, who).json()
    accounts.reset_caches()

    async def down(sid, sub):
        raise RuntimeError("store is down")
    monkeypatch.setattr(accounts, "session_state", down)
    assert client.get("/api/auth/check", headers=_h(a)).status_code == 200


def test_a_dead_token_does_not_spend_the_accounts_request_budget(client, monkeypatch):
    from services import settings
    monkeypatch.setenv("RATE_PER_10MIN", "20")
    monkeypatch.setattr(settings, "_settings", None)  # read the limit again, and again after the test
    who = _person()
    a, b = _apple(client, who).json(), _apple(client, who).json()
    sub = _claims(a["access_token"])["sub"]
    client.post("/api/auth/logout", headers=_h(a))
    before = len(auth._calls[sub][0])
    assert {client.get("/api/auth/check", headers=_h(a)).status_code for _ in range(40)} == {401}
    assert len(auth._calls[sub][0]) == before  # nothing was counted against the account
    assert client.get("/api/auth/check", headers=_h(b)).status_code == 200  # the live phone is not run into a 429
    # the same for a device whose row is gone
    d = client.post("/api/auth/device").json()
    dsub = _claims(d["access_token"])["sub"]
    assert client.get("/api/auth/check", headers=_h(d)).status_code == 200
    _rows(client, "DELETE FROM devices WHERE sub = ?", dsub)
    tenancy.reset_caches()
    before = len(auth._calls[dsub][0])
    assert {client.get("/api/auth/check", headers=_h(d)).status_code for _ in range(5)} == {401}
    assert len(auth._calls[dsub][0]) == before


# ---- refresh tokens: single use, theft, idle sessions ------------------------------------------------------------

def _session_row(client, body) -> list[tuple]:
    return _rows(client, "SELECT 1 FROM account_sessions WHERE id = ?", _claims(body["access_token"])["sid"])


def _age_session(client, body, column: str, by: timedelta) -> None:
    at = datetime.now(timezone.utc) - by
    _rows(client, f"UPDATE account_sessions SET {column} = ? WHERE id = ?", db.iso(at) if client.store == "sqlite" else at,
          _claims(body["access_token"])["sid"])


def test_a_spent_refresh_token_used_later_closes_the_session(client, caplog):
    """Somebody copied the refresh token. Whoever uses it second shows that two parties hold it: both are out."""
    who = _person()
    victim = _apple(client, who).json()
    other_phone = _apple(client, who).json()
    thief = _refresh(client, victim).json()  # the thief trades the stolen token in first
    assert client.get("/api/auth/check", headers=_h(thief)).status_code == 200
    _age_session(client, victim, "rotated_at", timedelta(seconds=auth.REUSE_RACE_S + 5))
    with caplog.at_level(logging.WARNING, logger="truckbuddy.auth"):
        r = _refresh(client, victim)  # the victim's phone comes later with the token it still has
    assert r.status_code == 401 and r.json() == {"detail": "Invalid or expired refresh token"}
    assert _session_row(client, victim) == [] and "spent refresh token reused" in caplog.text
    assert victim["refresh_token"] not in caplog.text and thief["refresh_token"] not in caplog.text
    assert _refresh(client, thief).status_code == 401  # the thief's pair died with the session
    assert client.get("/api/auth/check", headers=_h(thief)).json() == CLOSED
    assert _refresh(client, other_phone).status_code == 200  # the account's other phone is not touched
    assert _apple(client, who).json()["new_account"] is False  # and the victim signs in again


def test_a_spent_refresh_token_right_after_the_rotation_is_a_race_not_a_theft(client):
    who = _person()
    a = _apple(client, who).json()
    fresh = _refresh(client, a)
    assert fresh.status_code == 200
    assert _refresh(client, a).status_code == 401  # the same phone's second request, a moment later
    assert _session_row(client, a) != []  # the session stays
    assert _refresh(client, fresh.json()).status_code == 200  # and the pair the first request got works


def test_a_token_two_rotations_old_is_just_invalid(client):
    who = _person()
    a = _apple(client, who).json()
    b = _refresh(client, a).json()
    _refresh(client, b)
    _age_session(client, a, "rotated_at", timedelta(minutes=5))
    assert _refresh(client, a).status_code == 401 and _session_row(client, a) != []


def test_concurrent_refreshes_have_exactly_one_winner(client):
    who = _person()
    a = _apple(client, who).json()

    async def all_at_once():
        return await asyncio.gather(*[auth.refresh(a["refresh_token"]) for _ in range(6)], return_exceptions=True)
    got = client.portal.call(all_at_once)
    won = [g for g in got if isinstance(g, dict)]
    lost = [g for g in got if not isinstance(g, dict)]
    assert len(won) == 1 and len(lost) == 5
    assert {(e.status_code, e.detail) for e in lost} == {(401, "Invalid or expired refresh token")}
    assert _session_row(client, a) != []  # nobody was signed out
    assert client.get("/api/auth/check", headers=_h(won[0])).status_code == 200
    assert _refresh(client, won[0]).status_code == 200  # the one pair handed out is the live one


def test_renew_leaves_an_accounts_refresh_token_as_it_was(client):
    """A renew answer that never reached the phone must not cost the session: the phone still holds its refresh token,
    and the server still takes it."""
    who = _person()
    a = _apple(client, who).json()
    before = _session_row(client, a)
    renewed = client.post("/api/auth/renew", headers=_h(a))
    assert renewed.status_code == 200 and "refresh_token" not in renewed.json()
    assert renewed.json()["expires_in"] == auth.JWT_DAYS * 86400 and renewed.json()["role"] == "driver"
    assert _session_row(client, a) == before  # nothing rotated
    for _ in range(3):  # lost answers, one after another
        assert client.post("/api/auth/renew", headers=_h(a)).status_code == 200
    _age_session(client, a, "seen_at", timedelta(minutes=1))
    r = _refresh(client, a)  # much later, the refresh token the phone kept all along
    assert r.status_code == 200 and _session_row(client, a) != []


def test_a_fleet_code_on_an_account_keeps_its_refresh_token(client, caplog):
    """The fleet bind answers a token with the fleet's org and no refresh_token: the phone keeps the one it holds, so
    an answer lost on the way costs nothing. A device token still gets a new pair."""
    who = _person()
    a = _apple(client, who).json()
    before = _session_row(client, a) + _rows(client, "SELECT refresh_hash, prev_refresh_hash FROM account_sessions WHERE id = ?",
                                             _claims(a["access_token"])["sid"])
    r = client.post("/api/auth/fleet-code", json={"code": _fleet_code(client)}, headers=_h(a))
    assert r.status_code == 200 and "refresh_token" not in r.json(), r.text
    bound = r.json()
    assert _claims(bound["access_token"])["org_id"] == bound["org_id"] and _claims(bound["access_token"])["sid"] == _claims(a["access_token"])["sid"]
    after = _session_row(client, a) + _rows(client, "SELECT refresh_hash, prev_refresh_hash FROM account_sessions WHERE id = ?",
                                            _claims(a["access_token"])["sid"])
    assert after == before  # nothing rotated
    _age_session(client, a, "rotated_at", timedelta(seconds=auth.REUSE_RACE_S + 5))
    again = _refresh(client, a)  # long after: the refresh token the phone kept
    assert again.status_code == 200 and _session_row(client, a) != []
    assert _claims(again.json()["access_token"])["org_id"] == bound["org_id"]
    # a device token: a new pair, as before
    d = client.post("/api/auth/device").json()
    r = client.post("/api/auth/fleet-code", json={"code": _fleet_code(client)}, headers=_h(d))
    assert r.status_code == 200 and r.json()["refresh_token"]


@pytest.mark.parametrize("where", ["bind", "token"])
def test_a_fleet_code_that_fails_unexpectedly_is_a_503_without_a_trace(client, monkeypatch, caplog, where):
    who = _person()
    a = _apple(client, who).json()

    async def boom(*args, **kwargs):
        raise ValueError("secret detail from inside")
    monkeypatch.setattr(*((tenancy, "bind_fleet_code") if where == "bind" else (auth, "renew")), boom)
    with caplog.at_level(logging.WARNING, logger="truckbuddy"):
        r = client.post("/api/auth/fleet-code", json={"code": _fleet_code(client)}, headers=_h(a))
    assert r.status_code == 503 and r.json() == {"detail": "Fleet codes are unavailable right now"}
    assert "secret detail" not in r.text and "secret detail" not in caplog.text and "ValueError" in caplog.text


def test_renew_refuses_a_session_another_worker_closed(client):
    """renew reads the session from the store, not from this worker's minute-old copy."""
    who = _person()
    a = _apple(client, who).json()
    assert client.get("/api/auth/check", headers=_h(a)).status_code == 200
    _rows(client, "DELETE FROM account_sessions WHERE id = ?", _claims(a["access_token"])["sid"])
    r = client.post("/api/auth/renew", headers=_h(a))
    assert r.status_code == 401 and r.json() == CLOSED


def test_an_idle_session_is_refused_and_deleted(client, monkeypatch):
    who = _person()
    a, b, c = (_apple(client, who).json() for _ in range(3))
    for body in (a, b):
        _age_session(client, body, "seen_at", timedelta(days=181))
    _age_session(client, c, "seen_at", timedelta(days=179))
    # on refresh
    assert _refresh(client, a).status_code == 401 and _session_row(client, a) == []
    # on the session check
    r = client.get("/api/auth/check", headers=_h(b))
    assert r.status_code == 401 and r.json() == CLOSED and _session_row(client, b) == []
    # one day short of the limit is fine, and using it moves seen_at
    assert client.get("/api/auth/check", headers=_h(c)).status_code == 200 and _refresh(client, c).status_code == 200
    # ACCOUNT_SESSION_IDLE_DAYS sets the limit; 0 turns the expiry off
    d = _apple(client, who).json()
    _age_session(client, d, "seen_at", timedelta(days=40))
    monkeypatch.setenv("ACCOUNT_SESSION_IDLE_DAYS", "0")
    assert _refresh(client, d).status_code == 200
    e = _apple(client, who).json()
    _age_session(client, e, "seen_at", timedelta(days=40))
    monkeypatch.setenv("ACCOUNT_SESSION_IDLE_DAYS", "30")
    assert _refresh(client, e).status_code == 401
    monkeypatch.setenv("ACCOUNT_SESSION_IDLE_DAYS", "soon")  # a typo falls back to the default
    assert accounts.idle_days() == 180


def test_a_session_ends_for_good_a_year_after_its_sign_in(client, monkeypatch):
    """Renew needs no refresh token, so a copied access token could otherwise be renewed for ever. No token of a
    session expires past ACCOUNT_SESSION_MAX_DAYS from its sign-in, and after that the session is closed."""
    who = _person()
    near, past, used = (_apple(client, who).json() for _ in range(3))
    _age_session(client, near, "created_at", timedelta(days=accounts.MAX_DAYS_DEFAULT - 2))
    for body in (past, used):
        _age_session(client, body, "created_at", timedelta(days=accounts.MAX_DAYS_DEFAULT, minutes=1))
    end = datetime.now(timezone.utc) + timedelta(days=2)
    # two days before the end: renew and refresh hand out tokens that run out with the session, not 30 days on
    renewed = client.post("/api/auth/renew", headers=_h(near)).json()
    refreshed = _refresh(client, near).json()
    for body in (renewed, refreshed):
        assert abs(_claims(body["access_token"])["exp"] - end.timestamp()) < 60
        assert abs(body["expires_in"] - 2 * 86400) < 60
    # past the end: refused and closed, on every way in, however recently the phone was used
    assert _refresh(client, past).status_code == 401 and _session_row(client, past) == []
    accounts.reset_caches()
    assert client.post("/api/auth/renew", headers=_h(used)).status_code == 401 and _session_row(client, used) == []
    late = _apple(client, who).json()
    _age_session(client, late, "created_at", timedelta(days=400))
    accounts.reset_caches()
    assert client.get("/api/auth/check", headers=_h(late)).status_code == 401 and _session_row(client, late) == []
    # signing in again is a new session with a full year
    again = _apple(client, who).json()
    assert client.post("/api/auth/renew", headers=_h(again)).json()["expires_in"] == auth.JWT_DAYS * 86400
    # 0 turns the limit off; a typo falls back to the default
    old = _apple(client, who).json()
    _age_session(client, old, "created_at", timedelta(days=800))
    monkeypatch.setenv("ACCOUNT_SESSION_MAX_DAYS", "0")
    assert client.post("/api/auth/renew", headers=_h(old)).json()["expires_in"] == auth.JWT_DAYS * 86400
    assert _refresh(client, old).status_code == 200
    monkeypatch.setenv("ACCOUNT_SESSION_MAX_DAYS", "a year")
    assert accounts.max_days() == accounts.MAX_DAYS_DEFAULT


def test_an_expired_account_token_gets_no_grace_but_a_device_token_does(client):
    who = _person()
    a = _apple(client, who).json()
    for days in (0.01, 1, 100):
        r = client.get("/api/auth/check", headers=_h(_expired(a, days)))
        assert r.status_code == 401 and r.json() == {"detail": "Invalid or expired token"}
        assert client.post("/api/auth/renew", headers=_h(_expired(a, days))).status_code == 401
    assert _refresh(client, a).status_code == 200  # what the app does instead
    d = client.post("/api/auth/device").json()  # build 2 never refreshes: its expired token rides the grace
    assert client.get("/api/auth/check", headers=_h(_expired(d, 100))).status_code == 200
    assert client.post("/api/auth/renew", headers=_h(_expired(d, 100))).status_code == 200
    assert client.get("/api/auth/check", headers=_h(_expired(d, 400))).status_code == 401


def test_sessions_past_the_cap_drop_the_least_recently_used(client, monkeypatch):
    monkeypatch.setattr(accounts, "MAX_SESSIONS", 3)
    who = _person()
    phones = [_apple(client, who).json() for _ in range(3)]
    _age_session(client, phones[1], "seen_at", timedelta(days=2))  # the one nobody has used lately
    newest = _apple(client, who).json()
    sub = _claims(newest["access_token"])["sub"]
    left = {r[0] for r in _rows(client, "SELECT id FROM account_sessions WHERE sub = ?", sub)}
    assert left == {_claims(p["access_token"])["sid"] for p in (phones[0], phones[2], newest)}
    assert _refresh(client, phones[1]).status_code == 401 and _refresh(client, phones[0]).status_code == 200


def test_logout_all_signs_every_phone_out(client):
    who, somebody_else = _person(), _person()
    a, b, c = (_apple(client, who).json() for _ in range(3))
    other = _apple(client, somebody_else).json()
    r = client.post("/api/auth/logout-all", headers=_h(a))
    assert r.status_code == 200 and r.json() == {"ok": True, "closed": 3}
    for body in (a, b, c):
        assert client.get("/api/auth/check", headers=_h(body)).json() == CLOSED
        assert _refresh(client, body).status_code == 401
    assert client.post("/api/auth/logout-all", headers=_h(a)).status_code == 401  # it needs a live session
    assert client.post("/api/auth/logout-all").status_code == 401
    assert client.get("/api/auth/check", headers=_h(other)).status_code == 200  # another account is untouched
    back = _apple(client, who).json()
    assert back["new_account"] is False and client.get("/api/auth/check", headers=_h(back)).status_code == 200
    # a device token has no account: nothing to close, and it keeps working
    d = client.post("/api/auth/device").json()
    assert client.post("/api/auth/logout-all", headers=_h(d)).json() == {"ok": True, "closed": 0}
    assert client.get("/api/auth/check", headers=_h(d)).status_code == 200


def test_logout_works_with_any_token_this_server_signed(client):
    """However long expired and whatever its role: it only ever closes the session named in the token."""
    who = _person()
    for over in ({}, {"role": "owner"}, {"role": "tester"}):
        a = _apple(client, who).json()
        old = _expired(a, 400, **over)
        assert client.get("/api/auth/check", headers=_h(old)).status_code in (401, 403)
        assert client.post("/api/auth/logout", headers=_h(old)).json() == {"ok": True}
        assert _session_row(client, a) == []
        assert client.get("/api/auth/check", headers=_h(a)).json() == CLOSED
    forged = jwt.encode({"sub": "x", "sid": "ses_x", "exp": int(time.time()) + 60}, "another-secret-another-secret-123", algorithm="HS256")
    assert client.post("/api/auth/logout", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_logout_is_limited_per_ip(client, monkeypatch):
    monkeypatch.setattr(auth, "MAX_SIGNINS_PER_IP", 3)
    a = _apple(client, _person()).json()
    codes = [client.post("/api/auth/logout", headers=_h(a)).status_code for _ in range(2)]
    codes.append(client.post("/api/auth/logout", headers={"Authorization": "Bearer nonsense"}).status_code)
    codes.append(client.post("/api/auth/logout", headers=_h(a)).status_code)
    assert codes == [200, 200, 401, 429]
    assert client.post("/api/auth/logout", headers={**_h(a), "X-Forwarded-For": "203.0.113.9"}).status_code == 200


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
    assert (now["sub"], now["sid"]) == (was["sub"], was["sid"]) and now["org_id"] == was["org_id"]
    assert "refresh_token" not in r.json() and _refresh(client, a).status_code == 200


def test_a_fleet_code_keeps_the_session_in_the_new_token(client):
    who = _person()
    a = _apple(client, who).json()
    r = client.post("/api/auth/fleet-code", json={"code": _fleet_code(client)}, headers=_h(a))
    assert r.status_code == 200, r.text
    bound = _claims(r.json()["access_token"])
    assert bound["sid"] == _claims(a["access_token"])["sid"] and bound["vehicle_id"] == r.json()["vehicle_id"]
    assert client.post("/api/auth/logout", headers=_h(r.json())).status_code == 200
    assert client.get("/api/auth/check", headers=_h(r.json())).status_code == 401


def test_seen_at_moves_at_most_once_an_hour(client):
    who = _person()
    a = _apple(client, who).json()
    sid = _claims(a["access_token"])["sid"]
    old = datetime.now(timezone.utc) - timedelta(days=30)

    def age():
        _rows(client, "UPDATE account_sessions SET seen_at = ? WHERE id = ?", db.iso(old) if client.store == "sqlite" else old, sid)

    def seen() -> str:
        return str(_rows(client, "SELECT seen_at FROM account_sessions WHERE id = ?", sid)[0][0])
    today = datetime.now(timezone.utc).date().isoformat()
    age()
    assert client.get("/api/auth/check", headers=_h(a)).status_code == 200
    assert seen().startswith(today)
    age()
    assert client.get("/api/auth/check", headers=_h(a)).status_code == 200
    assert not seen().startswith(today)  # written a moment ago: not again for an hour


# ---- the per-IP limit -------------------------------------------------------------------------------------------

def test_failed_sign_ins_are_limited_per_ip(client, monkeypatch):
    monkeypatch.setattr(auth, "MAX_SIGNINS_PER_IP", 3)
    who = _person()
    for _ in range(10):  # sign-ins that succeed are not counted: drivers behind one NAT do not lock each other out
        assert _apple(client, who).status_code == 200
    assert _apple(client, who, _sign(_apple_claims(who, aud="other"))).status_code == 401
    assert _google(client, who, "x" * 40).status_code == 401  # both providers share the window
    assert _apple(client, who).status_code == 200  # a success does not wipe the failures either
    assert _google(client, who, _sign(_google_claims(who, email_verified=False))).status_code == 401
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
