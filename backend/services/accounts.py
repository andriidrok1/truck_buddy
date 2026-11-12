"""Accounts and their sessions (migration 0014): who signed in with Apple or Google, and on which phones.

An account's `sub` is the key the rest of the store already hangs a driver's data on (memory, trips, profile), so
the same person signing in on another phone gets a token with the same sub and finds everything there. Each phone
is one account_sessions row with its own refresh token; the access token carries the row's id as `sid` and is
refused once the row is gone (services/auth.py), which is how signing out works. A session nobody used for
ACCOUNT_SESSION_IDLE_DAYS is deleted the next time it is looked at, and so is one older than ACCOUNT_SESSION_MAX_DAYS
however much it is used: no token of a session is valid past that age (see session_end), so a token that leaked
cannot be renewed for ever, and the phone signs in again once a year.

Callers check db.store() first: nothing here works with the store off."""
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from services import db, session_store, tenancy
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


async def by_sub(sub: str) -> Optional[tuple[str, Optional[str]]]:
    """(provider, the sealed Apple refresh token or None) of the account behind this sub; None when there is none."""
    sql = "SELECT provider, apple_refresh_enc FROM accounts WHERE sub = {p}"
    if db.store() == "sqlite":
        return await db._sq_run(lambda con: con.execute(sql.format(p="?"), (sub,)).fetchone())
    pool = await db._db()
    r = await pool.fetchrow(sql.format(p="$1"), sub)
    return tuple(r) if r else None


async def apple_refresh_set(sub: str, sealed: Optional[str]) -> bool:
    """Keeps Apple's refresh token for the account (encrypted by the caller, services/apple.py; None forgets it).
    False when there is no account under this sub any more."""
    if db.store() == "sqlite":
        n = await db._sq_run(lambda con: con.execute("UPDATE accounts SET apple_refresh_enc = ? WHERE sub = ?", (sealed, sub)).rowcount)
    else:
        pool = await db._db()
        n = int((await pool.execute("UPDATE accounts SET apple_refresh_enc = $1 WHERE sub = $2", sealed, sub)).split()[-1])
    return n > 0


# ---- transactions on either engine ---------------------------------------------------------------------------

# Signing in, deleting and an account's fleet bind start here. Whichever comes second waits for the first and then
# sees what it did: a sign-in finds the account gone and starts over, a deletion takes the session the sign-in just
# opened with it. A deletion that may close a fleet then takes the fleet's row, which every bind into it takes too.
ACCOUNT_LOCK, ORG_LOCK = tenancy.ACCOUNT_LOCK, tenancy.ORG_LOCK


def _stamp(at: datetime):
    """A timestamp in the store's own form."""
    return _iso(at) if db.store() == "sqlite" else at


# ---- deleting an account -------------------------------------------------------------------------------------

# Every table with a column that holds a driver's sub. A migration that adds another one adds it here:
# tests/offline/test_account_delete.py reads every table of the store after a deletion and fails on a leftover.
SUB_COLUMNS = (("conversation", "driver"), ("driver_facts", "driver"), ("driver_visits", "driver"),
               ("driver_visit_trips", "driver"), ("trip_events", "driver"), ("jobs", "driver"),
               ("dialog_sessions", "driver"))
CODE_USER_GONE = "deleted"  # fleet_codes.used_by once the driver who used the code deleted the account


class LastOwner(Exception):
    """The account is the only owner of a fleet that still has people or a stored key in it."""


# For the org of a device: (solo, owners besides this sub, what else lives in it: other devices, other driver rows,
# stored credentials). Args: sub, sub, driver id, org id.
ORG_FACTS = ("SELECT o.solo, (SELECT count(*) FROM devices WHERE org_id = o.id AND role = 'owner' AND sub <> ?), "
             "(SELECT count(*) FROM devices WHERE org_id = o.id AND sub <> ?) + "
             "(SELECT count(*) FROM drivers WHERE org_id = o.id AND id <> ?) + "
             "(SELECT count(*) FROM org_credentials WHERE org_id = o.id) FROM orgs o WHERE o.id = ?")


def _closes_fleet(role: Optional[str], facts: list[tuple]) -> bool:
    """Whether the fleet goes with this account: its only owner is leaving and nobody else is in it. LastOwner when
    somebody is, or a key is stored: the fleet would be left with drivers and a Motive key and nobody who can manage
    them, and there is no way to add an owner afterwards. Dispatchers and drivers leave freely."""
    if role != "owner" or not facts:
        return False
    solo, other_owners, others = facts[0]
    if solo or other_owners:
        return False
    if others:
        raise LastOwner()
    return True


def _device_script(sub: str):
    """(driver id, org id, whether the org is a fleet this deletion closes) for the sub's device row."""
    rows = yield ("SELECT driver_id, org_id, role FROM devices WHERE sub = ?", (sub,))
    if not rows:
        return None, None, False
    driver_id, org_id, role = rows[0]
    if role == "owner":
        yield (ORG_LOCK, (org_id,))  # nobody joins the fleet between this count and the deletes after it
    facts = yield (ORG_FACTS, (sub, sub, driver_id, org_id))
    return driver_id, org_id, _closes_fleet(role, facts)


async def check_may_delete(sub: str) -> None:
    """LastOwner when deleting this account would be refused (see _closes_fleet). Asked before the Apple token is
    revoked, the only step of a deletion outside its transaction; the transaction asks again, and that answer is
    the one that holds."""
    await db.run_script(_device_script(sub))


def _erase_steps(sub: str, driver_id: Optional[str], org_id: Optional[str], close_fleet: bool = False) -> list[tuple[str, tuple]]:
    """The deletes, in the order the foreign keys allow (device, then its driver row, then the org), the account
    row last. `?` placeholders."""
    steps = [(f"DELETE FROM {table} WHERE {column} = ?", (sub,)) for table, column in SUB_COLUMNS]
    # A dialog saved by a /api/nudges poll may carry no driver; its id always starts with the sub.
    steps.append((f"DELETE FROM dialog_sessions WHERE session_id LIKE ? ESCAPE '{session_store.LIKE_ESCAPE}'",
                  (session_store.like_prefix(f"{sub}:"),)))
    # A used code must stay used (used_by empty would let it bind another phone to the fleet), without the sub.
    steps.append(("UPDATE fleet_codes SET used_by = ? WHERE used_by = ?", (CODE_USER_GONE, sub)))
    steps.append(("DELETE FROM devices WHERE sub = ?", (sub,)))
    if driver_id:
        # In a fleet too: the row is the driver's name, profile and Motive mapping. The fleet keeps its org, its
        # trucks and their profiles.
        steps.append(("DELETE FROM drivers WHERE id = ? AND NOT EXISTS (SELECT 1 FROM devices WHERE driver_id = drivers.id)",
                      (driver_id,)))
    if org_id and close_fleet:
        # The last owner of a fleet with nobody else in it: the fleet is theirs alone and goes with them.
        steps += [(f"DELETE FROM {table} WHERE org_id = ?", (org_id,))
                  for table in ("fleet_codes", "vehicles", "org_credentials", "users")]
        steps.append(("DELETE FROM orgs WHERE id = ? AND solo = 0", (org_id,)))
    elif org_id:
        steps.append((tenancy.DROP_EMPTY_SOLO_ORG.format(p="?"), (org_id,)))  # the org of one made for this sub
    steps.append(("DELETE FROM account_sessions WHERE sub = ?", (sub,)))
    steps.append(("DELETE FROM accounts WHERE sub = ?", (sub,)))
    return steps


def _count(rows: list[tuple]) -> int:
    return int(rows[0][0] or 0) if rows else 0


def _erase_script(sub: str):
    yield (ACCOUNT_LOCK, (sub,))
    # Read under the lock: an Apple token stored after the caller revoked the one it saw is in this row now, and
    # one stored later finds no row (services/apple.py revokes it then).
    apple_row = yield ("SELECT apple_refresh_enc FROM accounts WHERE sub = ?", (sub,))
    driver_id, org_id, close_fleet = yield from _device_script(sub)
    sids = [r[0] for r in (yield ("SELECT id FROM account_sessions WHERE sub = ?", (sub,)))]
    # What the answer reports, in the shape of DELETE /api/memory's: turns, trip events, an own stored profile.
    counts = {"deleted": _count((yield ("SELECT count(*) FROM conversation WHERE driver = ?", (sub,)))),
              "trip_events": _count((yield ("SELECT count(*) FROM trip_events WHERE driver = ?", (sub,)))),
              "profile": bool(driver_id) and bool((yield ("SELECT 1 FROM drivers WHERE id = ? AND profile IS NOT NULL", (driver_id,))))}
    for step in _erase_steps(sub, driver_id, org_id, close_fleet):
        yield step
    return driver_id, sids, counts, (apple_row[0][0] if apple_row else None)


async def erase(sub: str) -> dict:
    """erase_account without the Apple token."""
    return (await erase_account(sub))[0]


async def erase_account(sub: str) -> tuple[dict, Optional[str]]:
    """Deletes the account and every row keyed on its sub, in one transaction: either all of it is gone or the
    account is still whole and the same token can ask again. The check that the account may go (LastOwner) is made
    inside the same transaction, under the fleet's row lock, so a refusal leaves every row as it was and a fleet
    cannot gain a member between the check and the deletes. Returns {deleted, trip_events, profile, sessions}.

    The phones' tokens are refused at once on this worker and within SESSION_TTL_S on the others; signing in again
    with the same Apple or Google identity finds no account and starts a new one, under a new sub.

    Also returns the sealed Apple refresh token the account row held when it was deleted (None when there was none):
    the caller revoked the one it read before, and a token stored since would otherwise go unrevoked."""
    driver_id, sids, counts, sealed = await db.run_script(_erase_script(sub))
    for sid in sids:
        _sessions.pop(sid, None)
        _touched.pop(sid, None)
    tenancy.forget(sub, driver_id)
    return {**counts, "sessions": len(sids)}, sealed


# ---- sessions ------------------------------------------------------------------------------------------------

# sid -> (the session's sub, or None when the row is gone; read at), per process. Signing out drops this worker's
# entry; the other workers re-read the row after SESSION_TTL_S, so a signed-out phone is refused within a minute.
_sessions: dict[str, tuple[Optional[str], float]] = {}
SESSION_TTL_S = 60.0
_touched: dict[str, float] = {}  # sid -> when this worker last wrote seen_at
TOUCH_EVERY_S = 3600.0
MAX_SESSIONS = 20  # per account; a sign-in past it drops the ones used longest ago (phones that never signed out)
IDLE_DAYS_DEFAULT = 180
MAX_DAYS_DEFAULT = 365


@dataclass(frozen=True)
class Session:
    sid: str
    sub: str
    current: bool  # the hash asked about is the session's refresh token now (False: the one before it)
    seen_at: datetime
    rotated_at: Optional[datetime]
    created_at: datetime


def idle_days() -> int:
    """ACCOUNT_SESSION_IDLE_DAYS: a session not used for this long is gone (0 = sessions never expire)."""
    try:
        return max(0, int(os.environ.get("ACCOUNT_SESSION_IDLE_DAYS", "") or IDLE_DAYS_DEFAULT))
    except ValueError:  # a typo must not make every session immortal or dead
        return IDLE_DAYS_DEFAULT


def idle(seen_at: datetime) -> bool:
    days = idle_days()
    return days > 0 and _now() - seen_at > timedelta(days=days)


def max_days() -> int:
    """ACCOUNT_SESSION_MAX_DAYS: how long a session lives from its sign-in, used or not (0 = no limit)."""
    try:
        days = int(os.environ.get("ACCOUNT_SESSION_MAX_DAYS", "") or MAX_DAYS_DEFAULT)
    except ValueError:  # a typo must not make every session immortal or dead
        return MAX_DAYS_DEFAULT
    return days if days >= 0 else MAX_DAYS_DEFAULT  # "-1" is a typo too, not "no limit"


def session_end(created_at: datetime) -> Optional[datetime]:
    """When a session that started at `created_at` ends for good: no token of it may expire later. None = never."""
    days = max_days()
    return created_at + timedelta(days=days) if days > 0 else None


def ended(created_at: datetime) -> bool:
    end = session_end(created_at)
    return end is not None and _now() >= end


def _sign_in_script(sub: str, sid: str, refresh_hash: str, platform: Optional[str], at):
    if not (yield (ACCOUNT_LOCK, (sub,))):
        return None  # deleted since the caller looked the account up
    first = not (yield ("SELECT 1 FROM devices WHERE sub = ?", (sub,)))
    if first:
        for step in tenancy.solo_org_statements(sub, "driver", at):
            yield step
    yield ("INSERT INTO account_sessions (id, sub, refresh_hash, platform, created_at, seen_at) VALUES (?, ?, ?, ?, ?, ?)",
           (sid, sub, refresh_hash, platform, at, at))
    yield ("DELETE FROM account_sessions WHERE sub = ? AND id NOT IN (SELECT id FROM account_sessions WHERE sub = ? "
           f"ORDER BY seen_at DESC, created_at DESC LIMIT {MAX_SESSIONS})", (sub, sub))
    return first


async def session_open(sub: str, refresh_hash: str, platform: Optional[str] = None) -> Optional[tuple[str, bool]]:
    """Signs the account in on one phone, in one transaction: the account row is still there (and is held against
    a deletion running beside this one), the org, driver and device rows are made when the sub has none, and a new
    session holds the phone's first refresh token. Past MAX_SESSIONS the account's least recently used sessions go.

    Returns (session id, whether the rows were made now: the account's first time through), or None when the
    account is gone. This is the only place an account's sub gets its tenancy rows, so nothing can make them for
    an account that was deleted."""
    sid = new_id("ses")
    first = await db.run_script(_sign_in_script(sub, sid, refresh_hash, platform, _stamp(_now())))
    return None if first is None else (sid, first)


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
    sql = ("SELECT id, sub, refresh_hash, seen_at, rotated_at, created_at FROM account_sessions "
           "WHERE refresh_hash = {p1} OR prev_refresh_hash = {p2}")
    if db.store() == "sqlite":
        row = await db._sq_run(lambda con: con.execute(sql.format(p1="?", p2="?"), (refresh_hash, refresh_hash)).fetchone())
    else:
        pool = await db._db()
        r = await pool.fetchrow(sql.format(p1="$1", p2="$1"), refresh_hash)
        row = tuple(r) if r else None
    if not row:
        return None
    return Session(row[0], row[1], row[2] == refresh_hash, _at(row[3]), _at(row[4]) if row[4] is not None else None, _at(row[5]))


ALIVE, CLOSED, ACCOUNT_GONE = "alive", "session_closed", "account_gone"


async def account_exists(sub: str) -> bool:
    sql = "SELECT 1 FROM accounts WHERE sub = {p}"
    if db.store() == "sqlite":
        return bool(await db._sq_run(lambda con: con.execute(sql.format(p="?"), (sub,)).fetchone()))
    pool = await db._db()
    return bool(await pool.fetchval(sql.format(p="$1"), sub))


async def gone_why(sub: str) -> str:
    """For a session that is gone: ACCOUNT_GONE when the account behind it is gone too, else CLOSED (signed out on
    this phone or on all of them, closed as stolen, idle, past its end). CLOSED when the store cannot say: the app
    must not tell a driver whose account is there that it was deleted."""
    try:
        return CLOSED if await account_exists(sub) else ACCOUNT_GONE
    except Exception as e:  # noqa: BLE001
        logger.warning("account lookup failed for %s: %s", sub, type(e).__name__)
        return CLOSED


async def session_state(sid: str, sub: str) -> str:
    """ALIVE while this session exists for this sub; else CLOSED or ACCOUNT_GONE (gone_why). Cached, so a token
    costs one small read per worker per SESSION_TTL_S (two when the session is gone). A session idle past
    ACCOUNT_SESSION_IDLE_DAYS, or older than ACCOUNT_SESSION_MAX_DAYS, is deleted here and counts as CLOSED.

    The cached answer is what the store said when it was read: another worker's deletion is seen within
    SESSION_TTL_S, and a session already known closed whose account goes later keeps saying CLOSED until then."""
    hit = _sessions.get(sid)
    if hit is None or time.monotonic() - hit[1] >= SESSION_TTL_S:
        if db.store() == "sqlite":
            row = await db._sq_run(lambda con: con.execute(
                "SELECT sub, seen_at, created_at FROM account_sessions WHERE id = ?", (sid,)).fetchone())
        else:
            pool = await db._db()
            r = await pool.fetchrow("SELECT sub, seen_at, created_at FROM account_sessions WHERE id = $1", sid)
            row = tuple(r) if r else None
        owner, why = (row[0], ALIVE) if row else (None, None)
        if owner is not None and (idle(_at(row[1])) or ended(_at(row[2]))):
            await session_close(sid)
            owner, why = None, CLOSED
        if owner is None and why is None:
            why = await gone_why(sub)
        if len(_sessions) > 50_000:
            _sessions.clear()
        hit = _sessions[sid] = (owner, time.monotonic(), why)
    if hit[0] == sub:
        return ALIVE
    return hit[2] if hit[0] is None else CLOSED  # a session of another sub: this token's is not it


async def session_alive(sid: str, sub: str) -> bool:
    """Whether this session still exists for this sub (session_state)."""
    return await session_state(sid, sub) == ALIVE


async def session_started(sid: str, sub: str) -> Optional[datetime]:
    """When this session of this sub was opened; None when it is gone. What the store says now, not the cache of
    session_alive."""
    sql = "SELECT created_at FROM account_sessions WHERE id = {p1} AND sub = {p2}"
    if db.store() == "sqlite":
        row = await db._sq_run(lambda con: con.execute(sql.format(p1="?", p2="?"), (sid, sub)).fetchone())
        return _at(row[0]) if row else None
    pool = await db._db()
    at = await pool.fetchval(sql.format(p1="$1", p2="$2"), sid, sub)
    return _at(at) if at is not None else None


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
