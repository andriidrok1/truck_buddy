"""Device tokens: a long-lived JWT per device (sub = driver id).
Open sign-up issues one without a code (OPEN_ACCESS, on by default); the shared codes still work
(ACCESS_CODE, and TESTER_CODE for the test-scenario screen). Each driver is rate limited."""
import hashlib
import hmac
import os
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

JWT_DAYS = 365
MAX_ATTEMPTS, WINDOW_S = 5, 600
MAX_DEVICES_PER_IP = 20  # new device tokens per IP per WINDOW_S
_attempts: "defaultdict[str, deque]" = defaultdict(deque)
_signups: "defaultdict[str, deque]" = defaultdict(deque)
_calls: "defaultdict[str, tuple[deque, deque]]" = defaultdict(lambda: (deque(), deque()))
_bearer = HTTPBearer(auto_error=False)


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


def _token(role: str) -> str:
    now_dt = datetime.now(timezone.utc)
    payload = {"sub": f"drv_{uuid.uuid4().hex}", "role": role, "iat": now_dt, "exp": now_dt + timedelta(days=JWT_DAYS)}
    return jwt.encode(payload, _secret(), algorithm="HS256")


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


def require_driver(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    """Driver id from the Bearer token; 401 when missing or invalid."""
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Access code required")
    try:
        claims = jwt.decode(credentials.credentials, _secret(), algorithms=["HS256"],
                            options={"require": ["sub", "iat", "exp"]})
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if claims.get("role") != "tester":
        _limit(claims["sub"])
    return claims["sub"]


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
    if len(short) >= int(os.environ.get("RATE_PER_10MIN", "300")) or len(day) >= int(os.environ.get("RATE_PER_DAY", "3000")):
        raise HTTPException(status_code=429, detail="Too many requests. Try again in a few minutes.")
    short.append(now)
    day.append(now)
