"""Sign in with Apple, the server's half: Apple's refresh token for an account, kept so that deleting the account can
revoke it. Apple requires the revocation of an app that lets people delete their account (App Review 5.1.1(v)):
without it the app stays listed under "Sign in with Apple" in the driver's Apple ID settings.

At sign-in the app sends the one-time authorization code that came with the identity token. `remember_later` trades
it at Apple for a refresh token and stores that on the account, encrypted (services/secrets.py). It runs after the
sign-in has been answered, as a background task: Apple's round trip is not on the driver's way in, and whatever goes
wrong there cannot fail a sign-in that already succeeded. `revoke` is called by DELETE /api/account.

Both calls authenticate with a client secret: a short-lived ES256 JWT signed with the Sign in with Apple key (.p8) of
the developer team, APPLE_TEAM_ID / APPLE_KEY_ID / APPLE_PRIVATE_KEY. Without them (or without CREDENTIALS_KEY)
nothing is exchanged or stored, and deleting an account does not revoke anything at Apple.

The code, the tokens, the client secret and the key never reach a log: failures are logged as an exception class or
as Apple's status and error code."""
import asyncio
import json
import logging
import os
import re
import time
from typing import Optional

import jwt

from services import accounts, http, secrets as sec

logger = logging.getLogger("truckbuddy.apple")

TOKEN_URL = "https://appleid.apple.com/auth/token"
REVOKE_URL = "https://appleid.apple.com/auth/revoke"
AUDIENCE = "https://appleid.apple.com"
SECRET_MINUTES = 5  # Apple allows up to six months; the secret is made per call and needs to live for one
TIMEOUT_S = 5.0
KEY_ENV = ("APPLE_TEAM_ID", "APPLE_KEY_ID", "APPLE_PRIVATE_KEY")
_ERROR_CODE = re.compile(r"^[a-z_]{1,40}$")

_tasks: set = set()  # exchanges in flight (server.drain waits for them at shutdown)


def missing() -> list[str]:
    """The variables the exchange and the revocation need that are not set (empty = ready)."""
    return [name for name in (*KEY_ENV, "CREDENTIALS_KEY") if not os.environ.get(name, "").strip()]


def client_secret(client_id: str) -> str:
    """The JWT Apple takes as this app's password: issued by the team, about the app (`client_id`, the bundle id the
    identity token was made for), signed with the team's Sign in with Apple key."""
    team, kid, pem = (os.environ[name].strip() for name in KEY_ENV)
    now = int(time.time())
    claims = {"iss": team, "iat": now, "exp": now + SECRET_MINUTES * 60, "aud": AUDIENCE, "sub": client_id}
    # A .p8 pasted into one line of an env file keeps its line breaks as the two characters "\n".
    return jwt.encode(claims, pem.replace("\\n", "\n"), algorithm="ES256", headers={"kid": kid})


def _refused(r) -> str:
    """Apple's status and error code ("400 invalid_grant"), and nothing else of the body."""
    try:
        code = r.json().get("error")
    except Exception:  # noqa: BLE001 - not JSON, not an object
        code = None
    return f"{r.status_code} {code if isinstance(code, str) and _ERROR_CODE.match(code) else '-'}"


async def _exchange(code: str, client_id: str) -> Optional[str]:
    """Apple's refresh token for an authorization code; None (and a log line) when Apple did not give one."""
    try:
        r = await http.client().post(TOKEN_URL, timeout=TIMEOUT_S, data={
            "grant_type": "authorization_code", "code": code, "client_id": client_id,
            "client_secret": client_secret(client_id)})
        token = r.json().get("refresh_token") if r.status_code == 200 else None
    except Exception as e:  # noqa: BLE001 - network, a key that does not load, a body that is not JSON
        logger.warning("apple code exchange failed: %s", type(e).__name__)
        return None
    if not isinstance(token, str) or not token:
        logger.warning("apple code exchange refused: %s", _refused(r))
        return None
    return token


async def _remember(sub: str, code: str, client_id: str) -> None:
    token = await _exchange(code, client_id)
    if token is None:
        return
    try:
        # The client id goes in with the token: Apple revokes a token only for the client it was issued to, and
        # APPLE_AUDIENCES may list more than one.
        sealed = sec.encrypt(json.dumps({"token": token, "client_id": client_id}))
        await accounts.apple_refresh_set(sub, sealed)
    except Exception as e:  # noqa: BLE001
        logger.warning("apple refresh token not stored for %s: %s", sub, type(e).__name__)


def remember_later(sub: str, code: Optional[str], client_id: Optional[str]) -> None:
    """Starts the exchange of this sign-in's authorization code in the background; the newest token replaces the
    stored one. One warning and no call when the key is not configured."""
    if not code or not client_id:
        return
    absent = missing()
    if absent:
        logger.warning("apple refresh token not kept for %s: %s not set, deleting this account will not revoke it at Apple",
                       sub, ", ".join(absent))
        return
    t = asyncio.create_task(_remember(sub, code, client_id))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


async def settle() -> None:
    """Waits for the exchanges in flight (tests)."""
    if _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


async def revoke(sealed: Optional[str], sub: str) -> Optional[bool]:
    """Revokes the stored refresh token at Apple. True when Apple confirmed, False when the call failed, None when
    there was nothing to try with: no stored token, or the key is not configured. Never raises: the caller deletes
    the account whatever happens here."""
    if not sealed:
        logger.warning("apple account %s has no stored refresh token: nothing revoked at Apple", sub)
        return None
    absent = missing()
    if absent:
        logger.warning("apple account %s not revoked at Apple: %s not set", sub, ", ".join(absent))
        return None
    try:
        kept = json.loads(sec.decrypt(sealed))
        token, client_id = kept["token"], kept["client_id"]
        r = await http.client().post(REVOKE_URL, timeout=TIMEOUT_S, data={
            "token": token, "token_type_hint": "refresh_token", "client_id": client_id,
            "client_secret": client_secret(client_id)})
    except Exception as e:  # noqa: BLE001 - network, CREDENTIALS_KEY changed since, a key that does not load
        logger.warning("apple revoke failed for %s: %s", sub, type(e).__name__)
        return False
    if r.status_code != 200:
        logger.warning("apple revoke refused for %s: %s", sub, _refused(r))
        return False
    return True
