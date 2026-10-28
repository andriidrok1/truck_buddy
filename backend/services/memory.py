"""Long-term conversation memory per driver (Postgres, e.g. Neon), kept until the driver clears it.

The database is never on the hot path of a voice turn: writes go through add_later() (background task),
and the agent reads recent() under its own short timeout. A slow or unreachable database only costs memory.
"""
import asyncio
import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg

logger = logging.getLogger("truckbuddy.memory")

CONNECT_TIMEOUT_S = 8.0  # Neon resumes an idle compute in 1-3 s
COMMAND_TIMEOUT_S = 10.0

_pool: asyncpg.Pool | None = None
_pool_lock: asyncio.Lock | None = None
_tasks: set = set()
_pending: "defaultdict[str, set]" = defaultdict(set)  # driver -> saves still in flight

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation (
    id BIGSERIAL PRIMARY KEY,
    driver TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS conversation_driver_at ON conversation (driver, at);
CREATE TABLE IF NOT EXISTS driver_facts (
    driver TEXT PRIMARY KEY,
    facts TEXT NOT NULL,
    turns_seen INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL
);
"""


def _dsn() -> str:
    # Neon connection strings carry channel_binding, which asyncpg doesn't accept; sslmode stays.
    url = urlsplit(os.environ["DATABASE_URL"])
    query = urlencode([(k, v) for k, v in parse_qsl(url.query) if k != "channel_binding"])
    return urlunsplit(url._replace(query=query))


async def _db() -> asyncpg.Pool:
    global _pool, _pool_lock
    if _pool is not None:
        return _pool
    if _pool_lock is None:  # created lazily so importing the module needs no running loop
        _pool_lock = asyncio.Lock()
    async with _pool_lock:  # two first requests at once must not open two pools
        if _pool is None:
            pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=5,
                                             timeout=CONNECT_TIMEOUT_S, command_timeout=COMMAND_TIMEOUT_S)
            async with pool.acquire() as con:
                await con.execute(_SCHEMA)
            _pool = pool
    return _pool


async def warm() -> None:
    """Open the pool at startup so the first driver doesn't pay the connect + schema check."""
    try:
        await _db()
        logger.info("memory database ready")
    except Exception as e:  # noqa: BLE001 - the server runs without memory
        logger.warning("memory database unavailable at startup: %s", e)


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def add(driver: str, turns: list[tuple[str, str]]) -> None:
    """turns: [(role, text)], role is "driver" or "copilot"."""
    now = datetime.now(timezone.utc)  # +1 ms per turn keeps the order
    rows = [(driver, r, t, now + timedelta(milliseconds=i)) for i, (r, t) in enumerate(turns) if t]
    if rows:
        pool = await _db()
        await pool.executemany("INSERT INTO conversation (driver, role, text, at) VALUES ($1, $2, $3, $4)", rows)


def add_later(driver: str, turns: list[tuple[str, str]]) -> None:
    """add() as a background task: the voice reply never waits on the database."""
    async def _save():
        try:
            await add(driver, turns)
        except Exception as e:  # noqa: BLE001
            logger.warning("memory save failed: %s", e)

    t = asyncio.create_task(_save())
    _tasks.add(t)
    _pending[driver].add(t)

    def _done(task):
        _tasks.discard(task)
        _pending[driver].discard(task)
        if not _pending[driver]:
            _pending.pop(driver, None)

    t.add_done_callback(_done)


async def recent(driver: str, limit: int) -> list[dict]:
    """Last `limit` turns, oldest first. Waits for this driver's saves in flight, so a read right after a turn sees it."""
    if _pending.get(driver):
        await asyncio.gather(*list(_pending[driver]), return_exceptions=True)
    pool = await _db()
    rows = await pool.fetch("SELECT role, text, at FROM conversation WHERE driver = $1 ORDER BY at DESC LIMIT $2", driver, limit)
    return [{"role": r["role"], "text": r["text"], "at": r["at"].astimezone(timezone.utc).isoformat()} for r in reversed(rows)]


async def count_turns(driver: str) -> int:
    pool = await _db()
    return int(await pool.fetchval("SELECT count(*) FROM conversation WHERE driver = $1", driver) or 0)


async def get_facts(driver: str) -> dict | None:
    """{"facts": dict, "turns_seen": int, "updated_at": datetime} or None."""
    pool = await _db()
    row = await pool.fetchrow("SELECT facts, turns_seen, updated_at FROM driver_facts WHERE driver = $1", driver)
    if not row:
        return None
    try:
        facts = json.loads(row["facts"])
    except ValueError:
        return None
    return {"facts": facts, "turns_seen": int(row["turns_seen"]), "updated_at": row["updated_at"]}


async def set_facts(driver: str, facts: dict, turns_seen: int) -> None:
    pool = await _db()
    await pool.execute(
        "INSERT INTO driver_facts (driver, facts, turns_seen, updated_at) VALUES ($1, $2, $3, $4) "
        "ON CONFLICT (driver) DO UPDATE SET facts = EXCLUDED.facts, turns_seen = EXCLUDED.turns_seen, "
        "updated_at = EXCLUDED.updated_at",
        driver, json.dumps(facts), int(turns_seen), datetime.now(timezone.utc))


async def clear_facts(driver: str) -> int:
    pool = await _db()
    status = await pool.execute("DELETE FROM driver_facts WHERE driver = $1", driver)
    return int(status.split()[-1])


async def clear(driver: str) -> int:
    """Everything about the driver: the turns and the learned facts."""
    if _pending.get(driver):
        await asyncio.gather(*list(_pending[driver]), return_exceptions=True)
    pool = await _db()
    status = await pool.execute("DELETE FROM conversation WHERE driver = $1", driver)
    await pool.execute("DELETE FROM driver_facts WHERE driver = $1", driver)
    return int(status.split()[-1])
