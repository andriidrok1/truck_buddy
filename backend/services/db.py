"""The database behind memory, trip events and (soon) dialog sessions: one asyncpg pool for Postgres
(DATABASE_URL, e.g. Neon) or one SQLite connection for tests and a keyless dev box, and the migrations that
bring either up to the current schema.

Migrations are numbered SQL files in backend/migrations/ (Postgres) with a twin in migrations/sqlite/ where
the dialect differs (AUTOINCREMENT, text timestamps). schema_version records what has been applied; each
file runs once, inside a transaction together with its schema_version row, in order. SQLite has no
ADD COLUMN IF NOT EXISTS, so there an ALTER TABLE ... ADD COLUMN for a column that already exists is skipped.
migrate() is idempotent and runs at startup (memory.warm) and lazily on the first connection, so a fresh
database and an existing one end up identical.

MEMORY_STORE=pg|sqlite|off picks the store (the name predates this module); without it, Postgres when
DATABASE_URL is set, else SQLite at MEMORY_SQLITE_PATH (default backend/data/memory.sqlite).
"""
import asyncio
import logging
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg

logger = logging.getLogger("truckbuddy.db")

CONNECT_TIMEOUT_S = 8.0  # Neon resumes an idle compute in 1-3 s
COMMAND_TIMEOUT_S = 10.0
MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
_VERSION_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
_ADD_COLUMN_RE = re.compile(r"^(?:\s*--[^\n]*\n)*\s*ALTER\s+TABLE\s+(\w+)\s+ADD\s+(?:COLUMN\s+)?(\w+)", re.IGNORECASE)

_pool: asyncpg.Pool | None = None
_pool_lock: asyncio.Lock | None = None
_sqlite: sqlite3.Connection | None = None
_sqlite_lock = threading.Lock()


def store() -> str:
    """"pg" | "sqlite" | "off". MEMORY_STORE wins; otherwise Postgres when DATABASE_URL is set, else SQLite."""
    forced = os.environ.get("MEMORY_STORE", "").strip().lower()
    if forced in ("pg", "postgres", "sqlite", "off"):
        return "pg" if forced == "postgres" else forced
    return "pg" if os.environ.get("DATABASE_URL") else "sqlite"


def sqlite_path() -> Path:
    return Path(os.environ.get("MEMORY_SQLITE_PATH") or Path(__file__).resolve().parents[1] / "data" / "memory.sqlite")


# ---- timestamps (SQLite keeps them as ISO text, Postgres as timestamptz) -----------------------------------------

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(at: datetime) -> str:
    return at.astimezone(timezone.utc).isoformat()


def parse_at(v) -> datetime:
    """A stored timestamp back as an aware datetime: a naive one is UTC."""
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(v))


# ---- migrations ---------------------------------------------------------------------------------------------

def migration_files(dialect: str) -> list[tuple[int, Path]]:
    """(version, path) in order. For sqlite, a file in migrations/sqlite/ replaces the Postgres one of the same
    version; versions with no sqlite twin use the Postgres file as is."""
    files: dict[int, Path] = {}
    for p in sorted(MIGRATIONS_DIR.glob("*.sql")):
        m = _VERSION_RE.match(p.name)
        if m:
            files[int(m.group(1))] = p
    if dialect == "sqlite":
        for p in sorted((MIGRATIONS_DIR / "sqlite").glob("*.sql")):
            m = _VERSION_RE.match(p.name)
            if m:
                files[int(m.group(1))] = p
    return sorted(files.items())


def _sqlite_statements(script: str) -> list[str]:
    """The script's statements, split where sqlite itself sees one end (semicolons in comments and strings
    don't count)."""
    out, buf = [], ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


def _has_column(con: sqlite3.Connection, table: str, column: str) -> bool:
    return any(r[1].lower() == column.lower() for r in con.execute(f"PRAGMA table_info({table})").fetchall())


def _migrate_sqlite(con: sqlite3.Connection) -> list[int]:
    """Applies the pending sqlite migrations; returns the versions applied. Each file and its schema_version row
    commit together (the connection is in autocommit mode, so BEGIN/COMMIT are explicit; executescript would
    commit after every statement), and a column that is already there is not added again, so a run cut short
    before the version row, or a schema someone patched by hand, still migrates."""
    con.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    done = {r[0] for r in con.execute("SELECT version FROM schema_version").fetchall()}
    applied = []
    for version, path in migration_files("sqlite"):
        if version in done:
            continue
        con.execute("BEGIN")
        try:
            for stmt in _sqlite_statements(path.read_text()):
                m = _ADD_COLUMN_RE.match(stmt)
                if m and _has_column(con, m.group(1), m.group(2)):
                    continue
                con.execute(stmt)
            con.execute("INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                        (version, datetime.now(timezone.utc).isoformat()))
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        applied.append(version)
    return applied


async def _migrate_pg(con: asyncpg.Connection) -> list[int]:
    await con.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL)")
    done = {r["version"] for r in await con.fetch("SELECT version FROM schema_version")}
    applied = []
    for version, path in migration_files("pg"):
        if version in done:
            continue
        async with con.transaction():
            await con.execute(path.read_text())
            await con.execute("INSERT INTO schema_version (version, applied_at) VALUES ($1, now())", version)
        applied.append(version)
    return applied


async def migrate() -> list[int]:
    """Brings the configured store up to the current schema; returns the versions applied (empty when current)."""
    kind = store()
    if kind == "off":
        return []
    if kind == "sqlite":
        return await _sq_run(_migrate_sqlite)
    pool = await _db()
    async with pool.acquire() as con:
        applied = await _migrate_pg(con)
    if applied:
        logger.info("db migrated: %s", applied)
    return applied


# ---- connections --------------------------------------------------------------------------------------------

def _sq_run(fn):
    """Run a sqlite callback in a worker thread, serialised on one connection (migrated on first open)."""
    def _call():
        with _sqlite_lock:
            return fn(_sq_conn_unlocked())
    return asyncio.to_thread(_call)


def _sq_conn_unlocked() -> sqlite3.Connection:
    global _sqlite
    if _sqlite is None:
        path = sqlite_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        con.execute("PRAGMA journal_mode=WAL")
        _migrate_sqlite(con)
        _sqlite = con
    return _sqlite


def _dsn() -> str:
    # Neon connection strings carry channel_binding, which asyncpg doesn't accept; sslmode stays.
    url = urlsplit(os.environ["DATABASE_URL"])
    query = urlencode([(k, v) for k, v in parse_qsl(url.query) if k != "channel_binding"])
    return urlunsplit(url._replace(query=query))


async def _db() -> asyncpg.Pool:
    """The pool, opened (and the schema migrated) on first use; two first callers share one opening."""
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
                applied = await _migrate_pg(con)
            if applied:
                logger.info("db migrated: %s", applied)
            _pool = pool
    return _pool


async def close() -> None:
    global _pool, _sqlite
    if _pool is not None:
        await _pool.close()
        _pool = None
    with _sqlite_lock:
        if _sqlite is not None:
            _sqlite.close()
            _sqlite = None
