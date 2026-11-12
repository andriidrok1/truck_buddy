"""Who the phone says it is: checks an Apple identity token or a Google id token against the provider's public keys.

Both are RS256 JWTs. The keys come from the provider's JWKS endpoint through the shared HTTP client and are cached
in the process. The token must be signed by one of them, come from the provider (iss), be meant for this app (aud,
from APPLE_AUDIENCES / GOOGLE_CLIENT_IDS) and not be expired. Apple's must also carry the nonce the app made for this
sign-in, Google's a verified email.

Every refusal is an `Invalid` with a short reason for the log; the caller answers one generic 401, and the token
itself is never logged."""
import asyncio
import hashlib
import hmac
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import jwt

from services import http

logger = logging.getLogger("truckbuddy.identity")

JWKS_TTL_S = 6 * 3600.0
REFETCH_MIN_S = 60.0  # between two fetches from one provider, whatever the reason and however the last one went
FETCH_TIMEOUT_S = 5.0
LEEWAY_S = 60  # clock skew between the provider and this box


class Invalid(Exception):
    """The token does not prove the identity (the reason is for the log only)."""


class NotConfigured(Exception):
    """No audience is set for this provider, so no token can be meant for this app."""


class Unavailable(Exception):
    """The provider's keys could not be fetched and there is no cached copy to check against."""


@dataclass(frozen=True)
class Provider:
    name: str
    jwks_url: str
    issuers: tuple[str, ...]
    audiences_env: str  # comma-separated: the iOS bundle id, or the OAuth client ids of every platform

    def audiences(self) -> list[str]:
        return [a.strip() for a in os.environ.get(self.audiences_env, "").split(",") if a.strip()]


APPLE = Provider("apple", "https://appleid.apple.com/auth/keys", ("https://appleid.apple.com",), "APPLE_AUDIENCES")
GOOGLE = Provider("google", "https://www.googleapis.com/oauth2/v3/certs",
                  ("accounts.google.com", "https://accounts.google.com"), "GOOGLE_CLIENT_IDS")


@dataclass(frozen=True)
class Identity:
    provider: str
    sub: str  # the provider's stable id for this person
    email: Optional[str]
    name: Optional[str]
    audience: Optional[str] = None  # the client the token was issued to (Apple: the bundle id)


@dataclass
class _Jwks:
    keys: dict = field(default_factory=dict)  # kid -> public key
    at: Optional[float] = None  # monotonic time of the last successful fetch
    tried: Optional[float] = None  # ... of the last fetch attempt, successful or not
    failed: bool = False  # the last attempt did not get the keys
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)  # one fetch at a time per provider


_jwks: dict[str, _Jwks] = {}  # per provider URL, per process


def reset() -> None:
    """Tests: forget the cached keys."""
    _jwks.clear()


async def _fetch(url: str) -> dict:
    r = await http.client().get(url, timeout=FETCH_TIMEOUT_S)
    r.raise_for_status()
    keys = {}
    for k in r.json().get("keys", []):
        try:
            if k.get("kty") == "RSA" and k.get("kid"):
                keys[k["kid"]] = jwt.PyJWK(k, algorithm="RS256").key
        except jwt.PyJWTError:  # one key we cannot read must not cost us the others
            continue
    if not keys:
        raise ValueError("no usable keys in the JWKS")
    return keys


def _fetch_due(c: _Jwks, kid: str) -> bool:
    """Time to ask the provider: there are no keys, they are older than JWKS_TTL_S, or this kid is not among them
    (the provider rotated), and the last attempt was at least REFETCH_MIN_S ago. The throttle counts failed attempts
    too, so neither made-up kids nor an outage make every sign-in hold a connection waiting on the provider."""
    now = time.monotonic()
    if c.tried is not None and now - c.tried < REFETCH_MIN_S:
        return False
    return c.at is None or now - c.at >= JWKS_TTL_S or kid not in c.keys


async def _key(provider: Provider, kid: str):
    """The provider's public key with this kid, from the cache; fetched first when _fetch_due says so. Sign-ins that
    arrive while a fetch is running wait for it instead of starting their own."""
    c = _jwks.setdefault(provider.jwks_url, _Jwks())
    if _fetch_due(c, kid):
        async with c.lock:
            if _fetch_due(c, kid):  # the one that held the lock may have fetched already
                try:
                    c.keys, c.failed = await _fetch(provider.jwks_url), False
                    c.at = time.monotonic()
                except Exception as e:  # noqa: BLE001 - network, status, bad JSON
                    logger.warning("%s JWKS fetch failed: %s", provider.name, type(e).__name__)
                    c.failed = True  # keys we already have stay: providers rotate rarely, keep signing people in
                # Stamped when the attempt ends, not when it starts: sign-ins that arrive meanwhile must queue on
                # the lock and use its result, not see a fresh stamp and be turned away with an empty cache.
                c.tried = time.monotonic()
    key = c.keys.get(kid)
    if key is None:
        if c.failed or c.at is None:
            raise Unavailable(provider.name)  # we could not ask: the token was not judged
        raise Invalid("unknown kid")
    return key


async def _verify(provider: Provider, token: str) -> dict:
    audiences = provider.audiences()
    if not audiences:  # never fall back to "any audience": a token minted for another app would sign in here
        raise NotConfigured(provider.audiences_env)
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise Invalid("not a JWT")
    # Checked before any key is looked up: "none" and the HMAC algorithms must never reach the verifier.
    if header.get("alg") != "RS256":
        raise Invalid("alg is not RS256")
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise Invalid("no kid")
    key = await _key(provider, kid)
    try:
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=audiences, issuer=list(provider.issuers),
                            leeway=LEEWAY_S, options={"require": ["iss", "aud", "exp", "sub"]})
    except jwt.PyJWTError as e:
        raise Invalid(type(e).__name__)  # the class says which check failed; the message could quote a claim
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise Invalid("no sub")
    return claims


def _text(v, limit: int) -> Optional[str]:
    return v.strip()[:limit] or None if isinstance(v, str) else None


def _true(v) -> bool:
    return v is True or v == "true"  # Apple sends its booleans as strings


async def verify_apple(identity_token: str, raw_nonce: str) -> Identity:
    """The Apple user behind an identity token. `raw_nonce` is what the app generated for this sign-in; the app
    gave Apple its SHA-256, which Apple put in the token, so a token lifted from another sign-in does not pass."""
    claims = await _verify(APPLE, identity_token)
    got = claims.get("nonce")
    try:
        want = hashlib.sha256(raw_nonce.encode()).hexdigest()
        # bytes on both sides: compare_digest refuses a str with non-ASCII characters
        same = isinstance(got, str) and hmac.compare_digest(got.encode(), want.encode())
    except UnicodeError:  # a lone surrogate in either nonce cannot be encoded
        same = False
    if not same:
        raise Invalid("nonce mismatch")
    aud = claims.get("aud")
    return Identity("apple", claims["sub"], _text(claims.get("email"), 320), None, aud if isinstance(aud, str) else None)


async def verify_google(id_token: str) -> Identity:
    claims = await _verify(GOOGLE, id_token)
    # azp is the client the token was issued to. Google sets it when it differs from the audience (Android: aud is
    # the web client, azp the Android one) and whenever aud is a list; it has to be one of ours as well.
    if (isinstance(claims.get("aud"), list) or "azp" in claims) and claims.get("azp") not in GOOGLE.audiences():
        raise Invalid("azp is not one of ours")
    if not _true(claims.get("email_verified")):
        raise Invalid("email not verified")
    return Identity("google", claims["sub"], _text(claims.get("email"), 320), _text(claims.get("name"), 120))
