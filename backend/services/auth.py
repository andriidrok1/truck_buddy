"""Access-code gate: one shared invite code -> long-lived JWT per device (sub = driver id)."""
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
_attempts: "defaultdict[str, deque]" = defaultdict(deque)
_bearer = HTTPBearer(auto_error=False)


def _secret() -> str:
    return os.environ["JWT_SECRET"]


def _client_ip(request: Request) -> str:
    # The last X-Forwarded-For hop is the one our ingress appended (earlier ones are client-supplied).
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[-1].strip() if fwd else (request.client.host if request.client else "unknown")


def redeem(code: str, request: Request) -> tuple[str, str]:
    """Checks the code (5 tries per IP per 10 min); returns (device token, role).
    ACCESS_CODE -> "driver"; TESTER_CODE -> "tester" (same app plus the test-scenario screen)."""
    now = time.monotonic()
    if len(_attempts) > 10_000:  # one entry per IP ever seen: drop the ones with no recent tries
        for ip in [ip for ip, d in _attempts.items() if not d or now - d[-1] > WINDOW_S]:
            del _attempts[ip]
    q = _attempts[_client_ip(request)]
    while q and now - q[0] > WINDOW_S:
        q.popleft()
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
    now_dt = datetime.now(timezone.utc)
    payload = {"sub": f"drv_{uuid.uuid4().hex}", "role": role, "iat": now_dt, "exp": now_dt + timedelta(days=JWT_DAYS)}
    return jwt.encode(payload, _secret(), algorithm="HS256"), role


def require_driver(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    """Driver id from the Bearer token; 401 when missing or invalid."""
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Access code required")
    try:
        claims = jwt.decode(credentials.credentials, _secret(), algorithms=["HS256"],
                            options={"require": ["sub", "iat", "exp"]})
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return claims["sub"]
