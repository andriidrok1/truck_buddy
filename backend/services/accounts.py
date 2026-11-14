"""Accounts and their sessions (migration 0014): who signed in with Apple or Google, and on which phones.

An account's `sub` is the key the rest of the store already hangs a driver's data on (memory, trips, profile), so
the same person signing in on another phone gets a token with the same sub and finds everything there. Each phone
is one account_sessions row with its own refresh token; the access token carries the row's id as `sid` and is
refused once the row is gone (services/auth.py), which is how signing out works. A session nobody used for
ACCOUNT_SESSION_IDLE_DAYS is deleted the next time it is looked at.

Callers check db.store() first: nothing here works with the store off."""
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from services import db
from services.db import iso as _iso, parse_at as _at, utcnow as _now
from services.tenancy import new_id

logger = logging.getLogger("truckbuddy.accounts")


@dataclass(frozen=True)
class Account:
    sub: str
    provider: str
    email: Optional[str]
    name: Optional[str]


async def find_or_create(provider: str, provider_sub: str, new_sub: str, email: Optional[str] = None,
                         name: Optional[str] = None) -> Account:
    """The account of this provider identity, made with `new_sub` as its data key when there is none. Two first
    sign-ins at once both insert, the unique (provider, provider_sub) keeps one row and both read that row back, so
    they leave with the same sub. An email the provider sent replaces the stored one; the name is kept once set
    (Apple sends it on the first authorization only, and the driver may have changed it)."""
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
    return Account(*row)


# ---- sessions ------------------------------------------------------------------------------------------------

# sid -> (the session's sub, or None when the row is gone; read at), per process. Signing out drops this worker's
# entry; the other workers re-read the row after SESSION_TTL_S, so a signed-out phone is refused within a minute.
_sessions: dict[str, tuple[Optional[str], float]] = {}
SESSION_TTL_S = 60.0
_touched: dict[str, float] = {}  # sid -> when this worker last wrote seen_at
TOUCH_EVERY_S = 3600.0
MAX_SESSIONS = 20  # per account; a sign-in past it drops the ones used longest ago (phones that never signed out)
IDLE_DAYS_DEFAULT = 180


@dataclass(frozen=True)
class Session:
    sid: str
    sub: str
    current: bool  # the hash asked about is the session's refresh token now (False: the one before it)
    seen_at: datetime
    rotated_at: Optional[datetime]


def idle_days() -> int:
    """ACCOUNT_SESSION_IDLE_DAYS: a session not used for this long is gone (0 = sessions never expire)."""
    try:
        return max(0, int(os.environ.get("ACCOUNT_SESSION_IDLE_DAYS", "") or IDLE_DAYS_DEFAULT))
    except ValueError:  # a typo must not make every session immortal or dead
        return IDLE_DAYS_DEFAULT


def idle(seen_at: datetime) -> bool:
    days = idle_days()
    return days > 0 and _now() - seen_at > timedelta(days=days)


async def session_open(sub: str, refresh_hash: str, platform: Optional[str] = None) -> str:
    """A new session for this phone, holding its first refresh token; returns its id. Past MAX_SESSIONS the
    account's least recently used sessions go."""
    sid, now = new_id("ses"), _now()
    insert = ("INSERT INTO account_sessions (id, sub, refresh_hash, platform, created_at, seen_at) "
              "VALUES ({p1}, {p2}, {p3}, {p4}, {p5}, {p5})")
    prune = ("DELETE FROM account_sessions WHERE sub = {p1} AND id NOT IN "
             "(SELECT id FROM account_sessions WHERE sub = {p1} ORDER BY seen_at DESC, created_at DESC LIMIT {n})")
    if db.store() == "sqlite":
        def _open(con):
            con.execute(insert.format(p1="?", p2="?", p3="?", p4="?", p5="?"), (sid, sub, refresh_hash, platform, _iso(now), _iso(now)))
            con.execute(prune.format(p1="?", n=MAX_SESSIONS), (sub, sub))
        await db._sq_run(_open)
    else:
        pool = await db._db()
        await pool.execute(insert.format(p1="$1", p2="$2", p3="$3", p4="$4", p5="$5"), sid, sub, refresh_hash, platform, now)
        await pool.execute(prune.format(p1="$1", n=MAX_SESSIONS), sub)
    return sid


async def session_rotate(sid: str, refresh_hash: str, spent: Optional[str] = None) -> bool:
    """The session's refresh token becomes this one and the one it replaces is remembered as the previous. With
    `spent` (the hash of the token being traded in) the swap happens only while that token is still the current
    one, so of two refreshes racing with the same token exactly one gets a new pair. False when nothing changed:
    the session is gone, or another refresh got there first."""
    now = _now()
    sql = ("UPDATE account_sessions SET prev_refresh_hash = refresh_hash, refresh_hash = {p1}, rotated_at = {p2}, seen_at = {p2} "
           "WHERE id = {p3}" + (" AND refresh_hash = {p4}" if spent else ""))
    if db.store() == "sqlite":
        params = (refresh_hash, _iso(now), _iso(now), sid) + ((spent,) if spent else ())
        n = await db._sq_run(lambda con: con.execute(sql.format(p1="?", p2="?", p3="?", p4="?"), params).rowcount)
    else:
        pool = await db._db()
        status = await pool.execute(sql.format(p1="$1", p2="$2", p3="$3", p4="$4"), refresh_hash, now, sid, *([spent] if spent else []))
        n = int(status.split()[-1])
    return n > 0


async def session_by_refresh(refresh_hash: str) -> Optional[Session]:
    """The session this refresh token belongs to: as its current token, or as the one it had before the last
    rotation (`current` says which). None when no session knows it."""
    sql = ("SELECT id, sub, refresh_hash, seen_at, rotated_at FROM account_sessions "
           "WHERE refresh_hash = {p1} OR prev_refresh_hash = {p2}")
    if db.store() == "sqlite":
        row = await db._sq_run(lambda con: con.execute(sql.format(p1="?", p2="?"), (refresh_hash, refresh_hash)).fetchone())
    else:
        pool = await db._db()
        r = await pool.fetchrow(sql.format(p1="$1", p2="$1"), refresh_hash)
        row = tuple(r) if r else None
    if not row:
        return None
    return Session(row[0], row[1], row[2] == refresh_hash, _at(row[3]), _at(row[4]) if row[4] is not None else None)


async def session_alive(sid: str, sub: str) -> bool:
    """Whether this session still exists for this sub. Cached, so a token costs one small read per worker per
    SESSION_TTL_S. A session idle past ACCOUNT_SESSION_IDLE_DAYS is deleted here and counts as gone."""
    hit = _sessions.get(sid)
    if hit is None or time.monotonic() - hit[1] >= SESSION_TTL_S:
        if db.store() == "sqlite":
            row = await db._sq_run(lambda con: con.execute("SELECT sub, seen_at FROM account_sessions WHERE id = ?", (sid,)).fetchone())
        else:
            pool = await db._db()
            r = await pool.fetchrow("SELECT sub, seen_at FROM account_sessions WHERE id = $1", sid)
            row = tuple(r) if r else None
        owner = row[0] if row else None
        if owner is not None and idle(_at(row[1])):
            await session_close(sid)
            owner = None
        if len(_sessions) > 50_000:
            _sessions.clear()
        hit = _sessions[sid] = (owner, time.monotonic())
    return hit[0] == sub


async def session_touch(sid: str) -> None:
    """seen_at = now for a session in use (at most once an hour per worker): what the idle expiry goes by."""
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


async def close_all(sub: str) -> int:
    """Sign every phone of the account out; returns how many sessions there were. This worker refuses their tokens
    at once, the others within SESSION_TTL_S."""
    if db.store() == "sqlite":
        def _close(con):
            ids = [r[0] for r in con.execute("SELECT id FROM account_sessions WHERE sub = ?", (sub,)).fetchall()]
            con.execute("DELETE FROM account_sessions WHERE sub = ?", (sub,))
            return ids
        ids = await db._sq_run(_close)
    else:
        pool = await db._db()
        ids = [r["id"] for r in await pool.fetch("DELETE FROM account_sessions WHERE sub = $1 RETURNING id", sub)]
    for sid in ids:
        _sessions.pop(sid, None)
        _touched.pop(sid, None)
    return len(ids)


def reset_caches() -> None:
    """Tests: forget the per-process copies."""
    _sessions.clear()
    _touched.clear()
