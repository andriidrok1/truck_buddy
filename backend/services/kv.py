"""Small JSON key-value tables in the store (services/db.py): one row per key, upserted, read whole.

Used for breakdown jobs (`jobs`), parking verdicts (`parking_checks`) and the shared feed cache
(`feed_cache`). Each table has its own columns, so this module holds the dialect-split helpers the three
stores share rather than a generic table. Every call returns None / does nothing when the store is off."""
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from services import db
from services.db import iso as _iso, parse_at as _parse_at, utcnow as _now

logger = logging.getLogger("truckbuddy.kv")


def dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)


# ---- jobs ----------------------------------------------------------------------------------------------------

async def job_put(rid: str, driver: Optional[str], kind: str, state: dict) -> None:
    k = db.store()
    if k == "off":
        return
    text, now = dumps(state), _now()
    if k == "sqlite":
        await db._sq_run(lambda con: con.execute(
            "INSERT INTO jobs (rid, driver, kind, state, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(rid) DO UPDATE SET state = excluded.state, updated_at = excluded.updated_at",
            (rid, driver, kind, text, _iso(now))))
        return
    pool = await db._db()
    await pool.execute(
        "INSERT INTO jobs (rid, driver, kind, state, updated_at) VALUES ($1, $2, $3, $4::jsonb, $5) "
        "ON CONFLICT (rid) DO UPDATE SET state = EXCLUDED.state, updated_at = EXCLUDED.updated_at",
        rid, driver, kind, text, now)


async def job_get(rid: str) -> Optional[dict]:
    k = db.store()
    if k == "off":
        return None
    if k == "sqlite":
        row = await db._sq_run(lambda con: con.execute("SELECT state FROM jobs WHERE rid = ?", (rid,)).fetchone())
        text = row[0] if row else None
    else:
        pool = await db._db()
        text = await pool.fetchval("SELECT state::text FROM jobs WHERE rid = $1", rid)
    return json.loads(text) if text else None


async def job_driver(rid: str) -> Optional[str]:
    """The sub of the driver who started the job; None when it has none or the job is unknown."""
    k = db.store()
    if k == "off":
        return None
    if k == "sqlite":
        row = await db._sq_run(lambda con: con.execute("SELECT driver FROM jobs WHERE rid = ?", (rid,)).fetchone())
        return row[0] if row else None
    pool = await db._db()
    return await pool.fetchval("SELECT driver FROM jobs WHERE rid = $1", rid)


async def job_prune(hours: int = 24) -> int:
    k = db.store()
    if k == "off":
        return 0
    cutoff = _now() - timedelta(hours=hours)
    if k == "sqlite":
        return await db._sq_run(lambda con: con.execute("DELETE FROM jobs WHERE updated_at < ?", (_iso(cutoff),)).rowcount)
    pool = await db._db()
    status = await pool.execute("DELETE FROM jobs WHERE updated_at < $1", cutoff)
    return int(status.split()[-1])


# ---- parking checks ------------------------------------------------------------------------------------------

async def parking_put(key: str, check: dict, retried: bool, approach: Optional[dict]) -> None:
    k = db.store()
    if k == "off":
        return
    now = _now()
    ap = dumps(approach) if approach is not None else None
    if k == "sqlite":
        await db._sq_run(lambda con: con.execute(
            "INSERT INTO parking_checks (key, check_json, retried, approach, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET check_json = excluded.check_json, retried = excluded.retried, "
            "approach = COALESCE(excluded.approach, parking_checks.approach), updated_at = excluded.updated_at",
            (key, dumps(check), int(retried), ap, _iso(now))))
        return
    pool = await db._db()
    await pool.execute(
        "INSERT INTO parking_checks (key, check_json, retried, approach, updated_at) VALUES ($1, $2::jsonb, $3, $4::jsonb, $5) "
        "ON CONFLICT (key) DO UPDATE SET check_json = EXCLUDED.check_json, retried = EXCLUDED.retried, "
        "approach = COALESCE(EXCLUDED.approach, parking_checks.approach), updated_at = EXCLUDED.updated_at",
        key, dumps(check), int(retried), ap, now)


async def parking_set_approach(key: str, approach: dict) -> None:
    """The approach alone (a route was built to a place whose verdict may not exist yet: stored as a row with
    an empty check only if none exists)."""
    k = db.store()
    if k == "off":
        return
    now, ap = _now(), dumps(approach)
    if k == "sqlite":
        await db._sq_run(lambda con: con.execute(
            "INSERT INTO parking_checks (key, check_json, retried, approach, updated_at) VALUES (?, 'null', 0, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET approach = excluded.approach, updated_at = excluded.updated_at",
            (key, ap, _iso(now))))
        return
    pool = await db._db()
    await pool.execute(
        "INSERT INTO parking_checks (key, check_json, retried, approach, updated_at) VALUES ($1, 'null'::jsonb, 0, $2::jsonb, $3) "
        "ON CONFLICT (key) DO UPDATE SET approach = EXCLUDED.approach, updated_at = EXCLUDED.updated_at", key, ap, now)


async def parking_all() -> list[tuple[str, Optional[dict], bool, Optional[dict]]]:
    """Every row: (key, check or None, retried, approach or None)."""
    k = db.store()
    if k == "off":
        return []
    if k == "sqlite":
        rows = await db._sq_run(lambda con: con.execute("SELECT key, check_json, retried, approach FROM parking_checks").fetchall())
    else:
        pool = await db._db()
        got = await pool.fetch("SELECT key, check_json::text AS check_json, retried, approach::text AS approach FROM parking_checks")
        rows = [(r["key"], r["check_json"], r["retried"], r["approach"]) for r in got]
    out = []
    for key, check, retried, ap in rows:
        c = json.loads(check) if check else None
        out.append((key, c if isinstance(c, dict) else None, bool(retried), json.loads(ap) if ap else None))
    return out


async def parking_delete(key: str) -> None:
    k = db.store()
    if k == "off":
        return
    if k == "sqlite":
        await db._sq_run(lambda con: con.execute("DELETE FROM parking_checks WHERE key = ?", (key,)))
        return
    pool = await db._db()
    await pool.execute("DELETE FROM parking_checks WHERE key = $1", key)


# ---- feed cache ----------------------------------------------------------------------------------------------

async def feed_put(name: str, payload: Any) -> None:
    k = db.store()
    if k == "off":
        return
    text, now = dumps(payload), _now()
    if k == "sqlite":
        await db._sq_run(lambda con: con.execute(
            "INSERT INTO feed_cache (name, payload, fetched_at) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET payload = excluded.payload, fetched_at = excluded.fetched_at",
            (name, text, _iso(now))))
        return
    pool = await db._db()
    await pool.execute(
        "INSERT INTO feed_cache (name, payload, fetched_at) VALUES ($1, $2::jsonb, $3) "
        "ON CONFLICT (name) DO UPDATE SET payload = EXCLUDED.payload, fetched_at = EXCLUDED.fetched_at", name, text, now)


async def feed_get(name: str) -> Optional[tuple[Any, datetime]]:
    """(payload, fetched_at) or None."""
    k = db.store()
    if k == "off":
        return None
    if k == "sqlite":
        row = await db._sq_run(lambda con: con.execute("SELECT payload, fetched_at FROM feed_cache WHERE name = ?", (name,)).fetchone())
        if not row:
            return None
        return json.loads(row[0]), _parse_at(row[1])
    pool = await db._db()
    row = await pool.fetchrow("SELECT payload::text AS payload, fetched_at FROM feed_cache WHERE name = $1", name)
    if not row:
        return None
    return json.loads(row["payload"]), _parse_at(row["fetched_at"])
