"""Long-term conversation memory per driver (Postgres, e.g. Neon), kept until the driver clears it."""
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg

_pool: asyncpg.Pool | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation (
    id BIGSERIAL PRIMARY KEY,
    driver TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS conversation_driver_at ON conversation (driver, at);
"""


def _dsn() -> str:
    # Neon connection strings carry channel_binding, which asyncpg doesn't accept; sslmode stays.
    url = urlsplit(os.environ["DATABASE_URL"])
    query = urlencode([(k, v) for k, v in parse_qsl(url.query) if k != "channel_binding"])
    return urlunsplit(url._replace(query=query))


async def _db() -> asyncpg.Pool:
    global _pool
    if _pool is None:  # lazy: importing the agent (tests, tools) must not need a database
        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=5)
        async with pool.acquire() as con:
            await con.execute(_SCHEMA)
        _pool = pool
    return _pool


async def add(driver: str, turns: list[tuple[str, str]]) -> None:
    """turns: [(role, text)], role is "driver" or "copilot"."""
    now = datetime.now(timezone.utc)  # +1 ms per turn keeps the order
    rows = [(driver, r, t, now + timedelta(milliseconds=i)) for i, (r, t) in enumerate(turns) if t]
    if rows:
        pool = await _db()
        await pool.executemany("INSERT INTO conversation (driver, role, text, at) VALUES ($1, $2, $3, $4)", rows)


async def recent(driver: str, limit: int) -> list[dict]:
    """Last `limit` turns, oldest first."""
    pool = await _db()
    rows = await pool.fetch("SELECT role, text, at FROM conversation WHERE driver = $1 ORDER BY at DESC LIMIT $2", driver, limit)
    return [{"role": r["role"], "text": r["text"], "at": r["at"].astimezone(timezone.utc).isoformat()} for r in reversed(rows)]


async def clear(driver: str) -> int:
    pool = await _db()
    status = await pool.execute("DELETE FROM conversation WHERE driver = $1", driver)
    return int(status.split()[-1])
