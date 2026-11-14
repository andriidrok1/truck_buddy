"""Sign in with Apple, the server's half: Apple's refresh token for an account, kept so that deleting the account can
revoke it. Apple requires the revocation of an app that lets people delete their account (App Review 5.1.1(v)):
without it the app stays listed under "Sign in with Apple" in the driver's Apple ID settings.

At sign-in the app sends the one-time authorization code that came with the identity token. `remember_later` trades
it at Apple for a refresh token and stores that on the account, encrypted (services/secrets.py). It runs after the
sign-in has been answered, as a background task: Apple's round trip is not on the driver's way in, and whatever goes
wrong there cannot fail a sign-in that already succeeded. `revoke` is called by DELETE /api/account.

The exchanges are bounded, because each one is a call signed with the team's key that anybody holding one valid
identity token could otherwise start by the hundred: one in flight per account (a sign-in that arrives meanwhile
starts none), MAX_EXCHANGES at once in the process, and none at all for an account that already holds a token for
this client (signing in again does not need a new one).

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

MAX_EXCHANGES = 4  # calls to Apple's token endpoint at once, per process

_tasks: set = set()  # exchanges in flight (server.drain waits for them at shutdown)
_inflight: dict[str, asyncio.Task] = {}  # sub -> its exchange
_slots: tuple = (None, None)  # (loop, semaphore): a semaphore belongs to the loop it first waited on


def _slot() -> asyncio.Semaphore:
    global _slots
    loop = asyncio.get_running_loop()
    if _slots[0] is not loop:
        _slots = (loop, asyncio.Semaphore(MAX_EXCHANGES))
    return _slots[1]


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


async def _holds_token(sub: str, client_id: str) -> bool:
    """Whether the account already has a refresh token for this client that still opens with CREDENTIALS_KEY."""
    try:
        account = await accounts.by_sub(sub)
        return bool(account and account[1]) and json.loads(sec.decrypt(account[1])).get("client_id") == client_id
    except Exception:  # noqa: BLE001 - unreadable (the key was rotated): get a new one
        return False


async def _remember(sub: str, code: str, client_id: str) -> None:
    async with _slot():
        if await _holds_token(sub, client_id):
            return
        token = await _exchange(code, client_id)
    if token is None:
        return
    try:
        # The client id goes in with the token: Apple revokes a token only for the client it was issued to, and
        # APPLE_AUDIENCES may list more than one.
        sealed = sec.encrypt(json.dumps({"token": token, "client_id": client_id}))
        kept = await accounts.apple_refresh_set(sub, sealed)
    except Exception as e:  # noqa: BLE001
        logger.warning("apple refresh token not stored for %s: %s", sub, type(e).__name__)
        return
    if not kept:
        # The account was deleted while Apple was answering (the deletion waits for an exchange, but only so long):
        # nothing will ever revoke this token unless it is revoked now, while it is in hand.
        outcome = await _revoke(token, client_id, sub)
        logger.warning("apple refresh token arrived after account %s was deleted: revoked=%s", sub, outcome)


def remember_later(sub: str, code: Optional[str], client_id: Optional[str]) -> None:
    """Starts the exchange of this sign-in's authorization code in the background, unless one is already running for
    the account. One warning and no call when the key is not configured."""
    if not code or not client_id or sub in _inflight:
        return
    absent = missing()
    if absent:
        logger.warning("apple refresh token not kept for %s: %s not set, deleting this account will not revoke it at Apple",
                       sub, ", ".join(absent))
        return
    t = _inflight[sub] = asyncio.create_task(_remember(sub, code, client_id))
    _tasks.add(t)

    def _done(task):
        _tasks.discard(task)
        if _inflight.get(sub) is task:
            del _inflight[sub]
    t.add_done_callback(_done)


async def settle() -> None:
    """Waits for the exchanges in flight (tests)."""
    if _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


async def settled(sub: str) -> None:
    """Waits for this account's exchange in flight, if there is one (it is bounded by TIMEOUT_S once it has a slot).
    A deletion asks first, so the token Apple is about to issue is stored and gets revoked with the rest."""
    task = _inflight.get(sub)
    if task is not None:
        await asyncio.wait({task}, timeout=2 * TIMEOUT_S)


async def revoke(sealed: Optional[str], sub: str) -> Optional[bool]:
    """Revokes the stored refresh token at Apple. True when Apple accepted the call (it answers 200 for a token it
    no longer knows as well, so a repeated deletion says true again), False when the call failed, None when there
    was nothing to try with: no stored token, or the key is not configured. Never raises: the caller deletes the
    account whatever happens here. The stored token is not touched; it goes with the account row."""
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
    except Exception as e:  # noqa: BLE001 - CREDENTIALS_KEY changed since
        logger.warning("apple revoke failed for %s: %s", sub, type(e).__name__)
        return False
    return await _revoke(token, client_id, sub)


async def _revoke(token: str, client_id: str, sub: str) -> bool:
    """One revoke call at Apple; True when Apple accepted it. Never raises."""
    try:
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
