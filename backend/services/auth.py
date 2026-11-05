"""Device tokens: a JWT per device (sub = the device's driver id) plus a refresh token kept hashed on the
device row. Open sign-up issues one without a code (OPEN_ACCESS, on by default); the shared codes still
work (ACCESS_CODE, and TESTER_CODE for the test-scenario screen); a fleet code binds the device to an
organisation and a truck (services/tenancy.py). Each driver is rate limited.

`require_driver` hands endpoints a `Principal`: a str (the sub, so every place that keys on the driver id
keeps working) that also knows the driver's org, driver row, vehicle and role. Tokens from before tenancy
carry no org claims; the org is looked up (or created as an org of one) on the way in."""
import hashlib
import hmac
import logging
import os
import secrets as _secrets
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from services.settings import settings
from typing import Optional

from services import db, tenancy

JWT_DAYS = 30  # access token; the refresh token gets a new one


class Principal(str):
    """The authenticated device: the sub as a str, with the tenancy around it."""
    driver_id: Optional[str]
    org_id: Optional[str]
    vehicle_id: Optional[str]
    role: str

    def __new__(cls, sub: str, role: str = "driver", driver_id: Optional[str] = None, org_id: Optional[str] = None,
                vehicle_id: Optional[str] = None):
        p = super().__new__(cls, sub)
        p.role, p.driver_id, p.org_id, p.vehicle_id = role, driver_id, org_id, vehicle_id
        return p

    @property
    def sub(self) -> str:
        return str(self)


MAX_ATTEMPTS, WINDOW_S = 5, 600
MAX_DEVICES_PER_IP = 20  # new device tokens per IP per WINDOW_S
_attempts: "defaultdict[str, deque]" = defaultdict(deque)
_signups: "defaultdict[str, deque]" = defaultdict(deque)
_calls: "defaultdict[str, tuple[deque, deque]]" = defaultdict(lambda: (deque(), deque()))
_bearer = HTTPBearer(auto_error=False)

# Per-driver request budget on every authenticated route (token bucket). Every call behind the token
# costs money (STT, LLM, vision, TTS, HERE, Google), and the access code is shared, so one leaked
# token must not be able to drain the keys. 0 disables (test backends).
RATE_PER_MIN = int(os.environ.get("RATE_LIMIT_PER_MIN", "40"))
_buckets: dict[str, list[float]] = {}  # driver -> [tokens, last refill (monotonic)]


def _secret() -> str:
    return os.environ["JWT_SECRET"]


def _client_ip(request: Request) -> str:
    # The last X-Forwarded-For hop is the one our ingress appended (earlier ones are client-supplied).
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[-1].strip() if fwd else (request.client.host if request.client else "unknown")


def _window(store: "defaultdict[str, deque]", key: str, window_s: float, now: float) -> deque:
    """The key's timestamps within the last window_s seconds (old entries and idle keys are dropped)."""
    if len(store) > 10_000:  # one entry per key ever seen: drop the ones with no recent hits
        for k in [k for k, d in store.items() if not d or now - d[-1] > window_s]:
            del store[k]
    q = store[key]
    while q and now - q[0] > window_s:
        q.popleft()
    return q


def _token(role: str, sub: Optional[str] = None, claims: Optional[dict] = None, days: int = JWT_DAYS) -> str:
    now_dt = datetime.now(timezone.utc)
    payload = {"sub": sub or f"drv_{uuid.uuid4().hex}", "role": role, "iat": now_dt, "exp": now_dt + timedelta(days=days),
               **(claims or {})}
    return jwt.encode(payload, _secret(), algorithm="HS256")


def sub_of(token: str) -> str:
    """The sub of a token this server issued (no expiry check: it was minted a moment ago)."""
    return jwt.decode(token, _secret(), algorithms=["HS256"], options={"verify_exp": False})["sub"]


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue(sub: str, role: str) -> dict:
    """Access token with the device's tenancy claims plus a fresh refresh token (stored hashed; the previous one
    stops working). Without a store the access token alone comes back, as before."""
    dev = await tenancy.ensure_solo_org(sub, role)
    claims = {}
    if dev is not None:
        claims = {"org_id": dev.org_id, "driver_id": dev.driver_id}
        if dev.vehicle_id:
            claims["vehicle_id"] = dev.vehicle_id
        role = dev.role or role
    out = {"access_token": _token(role, sub, claims), "token_type": "bearer", "role": role}
    if dev is not None:
        refresh = _secrets.token_urlsafe(32)
        await tenancy.set_refresh(sub, _hash(refresh))
        out["refresh_token"] = refresh
        out["expires_in"] = JWT_DAYS * 86400
    return out


async def refresh(refresh_token: str) -> dict:
    """A new access token (and a new refresh token) for the device whose refresh token this is."""
    sub = await tenancy.sub_by_refresh(_hash(refresh_token))
    if not sub:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    dev = await tenancy.device_of(sub)
    return await issue(sub, dev.role if dev else "driver")


def open_access() -> bool:
    return os.environ.get("OPEN_ACCESS", "1").strip().lower() not in ("0", "false", "no", "")


def register_device(request: Request) -> str:
    """Open sign-up: a driver token without a code, at most MAX_DEVICES_PER_IP per IP per 10 min."""
    if not open_access():
        raise HTTPException(status_code=403, detail="Access code required")
    q = _window(_signups, _client_ip(request), WINDOW_S, time.monotonic())
    if len(q) >= MAX_DEVICES_PER_IP:
        raise HTTPException(status_code=429, detail="Too many new devices. Try again in a few minutes.")
    q.append(time.monotonic())
    return _token("driver")


def redeem(code: str, request: Request) -> tuple[str, str]:
    """Checks the code (5 tries per IP per 10 min); returns (device token, role).
    ACCESS_CODE -> "driver"; TESTER_CODE -> "tester" (same app plus the test-scenario screen)."""
    now = time.monotonic()
    q = _window(_attempts, _client_ip(request), WINDOW_S, now)
    if len(q) >= MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many attempts. Try again in a few minutes.")
    q.append(now)
    supplied = hashlib.sha256(code.strip().upper().encode()).digest()
    role = None
    for env, r in (("ACCESS_CODE", "driver"), ("TESTER_CODE", "tester")):
        value = os.environ.get(env, "").strip()
        if value and hmac.compare_digest(supplied, hashlib.sha256(value.upper().encode()).digest()):
            role = r
    if not role:
        raise HTTPException(status_code=401, detail="Invalid access code")
    q.clear()
    return _token(role), role


def per_worker(total: int) -> int:
    """A per-driver budget as this worker's share (the buckets live per process; WORKERS workers share a box)."""
    return settings().per_worker(total)


def take_token(driver: str, now: float, per_min: Optional[int] = None) -> bool:
    """One request from the driver's bucket; False when the budget for this minute is spent. The default is this
    worker's share of RATE_LIMIT_PER_MIN (module RATE_PER_MIN, 0 = off)."""
    if per_min is None:
        per_min = per_worker(RATE_PER_MIN)
    if per_min <= 0:
        return True
    if len(_buckets) > 10_000:  # drop buckets that are full again (idle for a minute or more)
        for d in [d for d, (tokens, last) in _buckets.items() if now - last >= 60]:
            del _buckets[d]
    tokens, last = _buckets.get(driver, (float(per_min), now))
    tokens = min(float(per_min), tokens + (now - last) * per_min / 60.0)
    if tokens < 1.0:
        _buckets[driver] = [tokens, now]
        return False
    _buckets[driver] = [tokens - 1.0, now]
    return True


def _claims(credentials: HTTPAuthorizationCredentials | None) -> dict:
    """The Bearer token's claims; 401 when missing or invalid."""
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Access code required")
    try:
        return jwt.decode(credentials.credentials, _secret(), algorithms=["HS256"],
                          options={"require": ["sub", "iat", "exp"]})
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


async def _principal(claims: dict) -> Principal:
    """The token's sub with its tenancy: from the claims when the token carries them, else from the device row
    (created as an org of one for a token from before tenancy, or for a store that was just turned on)."""
    sub, role = claims["sub"], str(claims.get("role") or "driver")
    if claims.get("org_id"):
        try:
            dev = await tenancy.device_of(sub)  # a bind since the token was issued wins over the claims
        except Exception as e:  # noqa: BLE001 - a store hiccup must not lock every driver out
            logging.getLogger("truckbuddy.auth").warning("device lookup failed for %s: %s", sub, e)
            return Principal(sub, role, claims.get("driver_id"), claims["org_id"], claims.get("vehicle_id"))
        if dev is not None:
            return Principal(sub, dev.role or role, dev.driver_id, dev.org_id, dev.vehicle_id)
        if db.store() != "off":
            # The store answered and this phone is not in it any more: its fleet claims are no longer true
            # (review 2026-11-02, bug 6). The app drops the token on 401 and signs in again.
            raise HTTPException(status_code=401, detail="This device is no longer registered")
        return Principal(sub, role, claims.get("driver_id"), claims["org_id"], claims.get("vehicle_id"))
    try:
        dev = await tenancy.ensure_solo_org(sub, role)
    except Exception as e:  # noqa: BLE001 - a store hiccup must not lock every driver out
        logging.getLogger("truckbuddy.auth").warning("tenancy lookup failed for %s: %s", sub, e)
        dev = None
    if dev is None:
        return Principal(sub, role)
    return Principal(sub, dev.role or role, dev.driver_id, dev.org_id, dev.vehicle_id)


async def require_driver(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> Principal:
    """The device behind the Bearer token (a Principal: the driver id as a str, plus org/vehicle/role); 401 when
    missing or invalid."""
    claims = _claims(credentials)
    if claims.get("role") != "tester":
        _limit(claims["sub"])
    return await _principal(claims)


async def require_tester(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> Principal:
    """A token issued for TESTER_CODE; 403 for a plain driver (the tester screen reads across drivers)."""
    claims = _claims(credentials)
    if claims.get("role") != "tester":
        raise HTTPException(status_code=403, detail="Tester access required")
    return await _principal(claims)


def require_org_member(*roles: str):
    """Dependency: the principal must belong to an organisation and, when roles are given, hold one of them
    (owner | dispatcher | driver). A plain driver token on a fleet route gets 403."""
    async def _dep(principal: Principal = Depends(require_driver)) -> Principal:
        if not principal.org_id:
            raise HTTPException(status_code=403, detail="Fleet access required")
        if roles and principal.role not in roles:
            raise HTTPException(status_code=403, detail="Not allowed for this role")
        return principal
    return _dep


def _limit(driver: str) -> None:
    """Caps each driver's API calls (RATE_PER_10MIN, RATE_PER_DAY) so a leaked token can't run up the AI bill."""
    now = time.monotonic()
    if len(_calls) > 50_000:  # drop drivers idle for a day
        for k in [k for k, (_, d) in _calls.items() if not d or now - d[-1] > 86_400]:
            del _calls[k]
    short, day = _calls[driver]
    for q, window in ((short, WINDOW_S), (day, 86_400)):
        while q and now - q[0] > window:
            q.popleft()
    s = settings()
    if len(short) >= per_worker(s.rate_per_10min) or len(day) >= per_worker(s.rate_per_day):
        raise HTTPException(status_code=429, detail="Too many requests. Try again in a few minutes.")
    short.append(now)
    day.append(now)


def rate_limited(driver: Principal = Depends(require_driver)) -> Principal:
    """require_driver (resolved once per request by FastAPI, so any check inside it runs once even when an
    endpoint also declares Depends(require_driver)) plus the per-driver budget; 429 with a line the app can speak."""
    if not take_token(driver, time.monotonic()):
        raise HTTPException(status_code=429, detail="Slow down a little.")
    return driver
