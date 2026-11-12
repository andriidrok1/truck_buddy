"""Who the phone says it is: checks an Apple identity token or a Google id token against the provider's public keys.

Both are RS256 JWTs. The keys come from the provider's JWKS endpoint through the shared HTTP client and are cached
in the process. The token must be signed by one of them, come from the provider (iss), be meant for this app (aud,
from APPLE_AUDIENCES / GOOGLE_CLIENT_IDS) and not be expired. Apple's must also carry the nonce the app made for this
sign-in, Google's a verified email.

Every refusal is an `Invalid` with a short reason for the log; the caller answers one generic 401, and the token
itself is never logged."""
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
REFETCH_MIN_S = 60.0  # a kid the keys do not have: ask the provider again at most this often
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


@dataclass
class _Jwks:
    keys: dict = field(default_factory=dict)  # kid -> public key
    at: Optional[float] = None  # monotonic time of the last successful fetch
    missed: Optional[float] = None  # ... of the last fetch made for a kid that was not there afterwards


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


async def _key(provider: Provider, kid: str):
    """The provider's public key with this kid. The keys are fetched when there are none or they are older than
    JWKS_TTL_S, and once more when the kid is not among them (the provider rotated); a kid that is still unknown
    after that is not asked about again for REFETCH_MIN_S, so made-up kids cannot make us hammer the provider."""
    c = _jwks.setdefault(provider.jwks_url, _Jwks())
    now = time.monotonic()
    stale = c.at is None or now - c.at >= JWKS_TTL_S
    unknown = kid not in c.keys
    if stale or (unknown and (c.missed is None or now - c.missed >= REFETCH_MIN_S)):
        try:
            c.keys, c.at = await _fetch(provider.jwks_url), now
        except Exception as e:  # noqa: BLE001 - network, status, bad JSON
            logger.warning("%s JWKS fetch failed: %s", provider.name, type(e).__name__)
            if unknown:
                raise Unavailable(provider.name)
            # the keys we have are old but the kid is one of them: providers rotate rarely, keep signing people in
        else:
            if kid not in c.keys:
                c.missed = now
    key = c.keys.get(kid)
    if key is None:
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
    want = hashlib.sha256(raw_nonce.encode()).hexdigest()
    if not hmac.compare_digest(str(claims.get("nonce") or ""), want):
        raise Invalid("nonce mismatch")
    return Identity("apple", claims["sub"], _text(claims.get("email"), 320), None)


async def verify_google(id_token: str) -> Identity:
    claims = await _verify(GOOGLE, id_token)
    if not _true(claims.get("email_verified")):
        raise Invalid("email not verified")
    return Identity("google", claims["sub"], _text(claims.get("email"), 320), _text(claims.get("name"), 120))
