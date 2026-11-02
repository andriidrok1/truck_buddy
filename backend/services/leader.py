"""One worker does the polling for all. `try_lead(name)` is a Postgres advisory lock on a connection this process
keeps for its lifetime: the worker that gets it polls NWS / Caltrans / TPIMS and writes the result to feed_cache;
the others read the cache. Without Postgres (SQLite dev box, tests, store off) every process leads: that is
today's single-process behaviour unchanged."""
import asyncio
import logging
import os
from typing import Optional

import asyncpg

from services import db

logger = logging.getLogger("truckbuddy.leader")

_conn: Optional[asyncpg.Connection] = None
_lock: Optional[asyncio.Lock] = None
_held: set[str] = set()


def _key(name: str) -> int:
    """A stable 63-bit key for pg_try_advisory_lock(bigint)."""
    h = 1469598103934665603
    for b in name.encode():
        h = ((h ^ b) * 1099511628211) & 0x7FFFFFFFFFFFFFFF
    return h


def _guard() -> asyncio.Lock:
    """One asyncpg connection serves every feed's lock call; asyncpg allows one operation at a time on it, so
    the calls (connect, try, release) are serialised here (three feeds asking at startup once raised
    InterfaceError "another operation is in progress" and fell back to polling locally)."""
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


async def _connection_locked() -> asyncpg.Connection:
    """The dedicated connection; the caller holds _guard()."""
    global _conn
    if _conn is None or _conn.is_closed():
        _conn = await asyncpg.connect(db._dsn(), timeout=db.CONNECT_TIMEOUT_S)
        _held.clear()  # a new session holds no locks
    return _conn


async def try_lead(name: str) -> bool:
    """True when this process should run the poller `name`. Idempotent: a lock already held stays held."""
    if db.store() != "pg" or not os.environ.get("DATABASE_URL"):
        return True
    if name in _held:
        return True
    try:
        async with _guard():
            if name in _held:
                return True
            con = await _connection_locked()
            got = bool(await con.fetchval("SELECT pg_try_advisory_lock($1)", _key(name)))
            if got:
                _held.add(name)
    except Exception as e:  # noqa: BLE001 - no database: poll locally rather than not at all
        logger.warning("leader lock %s unavailable (%s): polling locally", name, type(e).__name__)
        return True
    if got:
        logger.info("leader: this worker polls %s", name)
    return got


async def release(name: str) -> None:
    if name in _held and _conn is not None and not _conn.is_closed():
        try:
            async with _guard():
                await _conn.fetchval("SELECT pg_advisory_unlock($1)", _key(name))
        except Exception:  # noqa: BLE001
            pass
    _held.discard(name)


async def close() -> None:
    global _conn
    async with _guard():
        if _conn is not None and not _conn.is_closed():
            await _conn.close()
        _conn = None
        _held.clear()
