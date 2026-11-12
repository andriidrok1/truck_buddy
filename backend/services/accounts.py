"""Accounts and their sessions (migration 0014): who signed in with Apple or Google, and on which phones.

An account's `sub` is the key the rest of the store already hangs a driver's data on (memory, trips, profile), so
the same person signing in on another phone gets a token with the same sub and finds everything there. Each phone
is one account_sessions row with its own refresh token; the access token carries the row's id as `sid` and is
refused once the row is gone (services/auth.py), which is how signing out works.

Callers check db.store() first: nothing here works with the store off."""
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Optional

from services import db
from services.db import iso as _iso, utcnow as _now
from services.tenancy import new_id

logger = logging.getLogger("truckbuddy.accounts")


@dataclass(frozen=True)
class Account:
    sub: str
    provider: str
    email: Optional[str]
    name: Optional[str]


async def find_or_create(provider: str, provider_sub: str, new_sub: str, email: Optional[str] = None,
                         name: Optional[str] = None) -> tuple[Account, bool]:
    """The account of this provider identity, made with `new_sub` as its data key when there is none; True when
    this call made it. Two first sign-ins at once both insert, the unique (provider, provider_sub) keeps one row and
    both read that row back, so they leave with the same sub. An email the provider sent replaces the stored one;
    the name is kept once set (Apple sends it on the first authorization only, and the driver may have changed it)."""
    acc_id, now = new_id("acc"), _now()
    insert = ("INSERT INTO accounts (id, sub, provider, provider_sub, email, name, created_at) "
              "VALUES ({p}) ON CONFLICT (provider, provider_sub) DO NOTHING")
    update = ("UPDATE accounts SET email = COALESCE({e}, email), name = COALESCE(name, {n}) "
              "WHERE provider = {p} AND provider_sub = {s}")
    select = "SELECT sub, provider, email, name FROM accounts WHERE provider = {p} AND provider_sub = {s}"
    if db.store() == "sqlite":
        def _upsert(con):
            con.execute(insert.format(p="?, ?, ?, ?, ?, ?, ?"), (acc_id, new_sub, provider, provider_sub, email, name, _iso(now)))
            con.execute(update.format(e="?", n="?", p="?", s="?"), (email, name, provider, provider_sub))
            return con.execute(select.format(p="?", s="?"), (provider, provider_sub)).fetchone()
        row = await db._sq_run(_upsert)
    else:
        pool = await db._db()
        async with pool.acquire() as con:
            await con.execute(insert.format(p="$1, $2, $3, $4, $5, $6, $7"), acc_id, new_sub, provider, provider_sub, email, name, now)
            await con.execute(update.format(e="$1", n="$2", p="$3", s="$4"), email, name, provider, provider_sub)
            row = tuple(await con.fetchrow(select.format(p="$1", s="$2"), provider, provider_sub))
    acc = Account(*row)
    return acc, acc.sub == new_sub


# ---- sessions ------------------------------------------------------------------------------------------------

# sid -> (the session's sub, or None when the row is gone; read at), per process. Signing out drops this worker's
# entry; the other workers re-read the row after SESSION_TTL_S, so a signed-out phone is refused within a minute.
_sessions: dict[str, tuple[Optional[str], float]] = {}
SESSION_TTL_S = 60.0
_touched: dict[str, float] = {}  # sid -> when this worker last wrote seen_at
TOUCH_EVERY_S = 3600.0


async def session_open(sub: str, platform: Optional[str] = None) -> str:
    """A new session for this phone; returns its id. The row starts with the hash slot filled by random bytes no
    token hashes to; auth.issue puts the real refresh token's hash there a moment later."""
    sid, now, placeholder = new_id("ses"), _now(), "unset:" + secrets.token_hex(16)
    if db.store() == "sqlite":
        await db._sq_run(lambda con: con.execute(
            "INSERT INTO account_sessions (id, sub, refresh_hash, platform, created_at, seen_at) VALUES (?, ?, ?, ?, ?, ?)",
            (sid, sub, placeholder, platform, _iso(now), _iso(now))))
    else:
        pool = await db._db()
        await pool.execute("INSERT INTO account_sessions (id, sub, refresh_hash, platform, created_at, seen_at) "
                           "VALUES ($1, $2, $3, $4, $5, $5)", sid, sub, placeholder, platform, now)
    return sid


async def session_rotate(sid: str, refresh_hash: str) -> bool:
    """The session's refresh token becomes this one (the previous stops working); False when the session is gone."""
    now = _now()
    if db.store() == "sqlite":
        n = await db._sq_run(lambda con: con.execute(
            "UPDATE account_sessions SET refresh_hash = ?, seen_at = ? WHERE id = ?", (refresh_hash, _iso(now), sid)).rowcount)
    else:
        pool = await db._db()
        status = await pool.execute("UPDATE account_sessions SET refresh_hash = $1, seen_at = $2 WHERE id = $3", refresh_hash, now, sid)
        n = int(status.split()[-1])
    return n > 0


async def session_by_refresh(refresh_hash: str) -> Optional[tuple[str, str]]:
    """(sid, sub) of the session whose current refresh token hashes to this, or None."""
    if db.store() == "sqlite":
        row = await db._sq_run(lambda con: con.execute(
            "SELECT id, sub FROM account_sessions WHERE refresh_hash = ?", (refresh_hash,)).fetchone())
    else:
        pool = await db._db()
        r = await pool.fetchrow("SELECT id, sub FROM account_sessions WHERE refresh_hash = $1", refresh_hash)
        row = tuple(r) if r else None
    return (row[0], row[1]) if row else None


async def session_alive(sid: str, sub: str) -> bool:
    """Whether this session still exists for this sub. Cached, so a token costs one small read per worker per
    SESSION_TTL_S."""
    hit = _sessions.get(sid)
    if hit is None or time.monotonic() - hit[1] >= SESSION_TTL_S:
        if db.store() == "sqlite":
            row = await db._sq_run(lambda con: con.execute("SELECT sub FROM account_sessions WHERE id = ?", (sid,)).fetchone())
            owner = row[0] if row else None
        else:
            pool = await db._db()
            owner = await pool.fetchval("SELECT sub FROM account_sessions WHERE id = $1", sid)
        if len(_sessions) > 50_000:
            _sessions.clear()
        hit = _sessions[sid] = (owner, time.monotonic())
    return hit[0] == sub


async def session_touch(sid: str) -> None:
    """seen_at = now for a session in use (at most once an hour per worker), so idle phones can be told apart."""
    now_m = time.monotonic()
    last = _touched.get(sid)
    if last is not None and now_m - last < TOUCH_EVERY_S:
        return
    if len(_touched) > 50_000:
        _touched.clear()
    _touched[sid] = now_m
    now = _now()
    if db.store() == "sqlite":
        await db._sq_run(lambda con: con.execute("UPDATE account_sessions SET seen_at = ? WHERE id = ?", (_iso(now), sid)))
    else:
        pool = await db._db()
        await pool.execute("UPDATE account_sessions SET seen_at = $1 WHERE id = $2", now, sid)


async def session_close(sid: str) -> None:
    """Sign this phone out: its session row goes, and so does its refresh token. The account's other phones stay."""
    if db.store() == "sqlite":
        await db._sq_run(lambda con: con.execute("DELETE FROM account_sessions WHERE id = ?", (sid,)))
    else:
        pool = await db._db()
        await pool.execute("DELETE FROM account_sessions WHERE id = $1", sid)
    _sessions.pop(sid, None)
    _touched.pop(sid, None)


def reset_caches() -> None:
    """Tests: forget the per-process copies."""
    _sessions.clear()
    _touched.clear()
