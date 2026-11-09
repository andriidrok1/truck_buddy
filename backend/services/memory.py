"""Long-term conversation memory per driver, kept until the driver clears it.

Two stores behind the same functions: Postgres (DATABASE_URL, e.g. Neon) for production, and a local
SQLite file for tests and a keyless dev box (services/db.py owns the connections and the migrations;
MEMORY_STORE=pg|sqlite|off forces one).

The store is never on the hot path of a voice turn: writes go through add_later() (background task),
and the agent reads recent() under its own short timeout. A slow or unreachable store only costs memory.
"""
import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from services import db
from services.db import _db, _sq_run, close, sqlite_path, store  # noqa: F401 - the store's plumbing lives in db.py

logger = logging.getLogger("truckbuddy.memory")

_tasks: set = set()
_pending: "defaultdict[str, set]" = defaultdict(set)  # driver -> saves still in flight


def _off():
    raise RuntimeError("memory store is off")


async def warm() -> None:
    """Open the store at startup and bring its schema up to date, so the first driver doesn't pay for either."""
    kind = store()
    if kind == "off":
        logger.info("memory store off")
        return
    try:
        applied = await db.migrate()
        logger.info("memory store: %s%s", "postgres" if kind == "pg" else f"sqlite at {sqlite_path()}",
                    f", migrated {applied}" if applied else "")
    except Exception as e:  # noqa: BLE001 - the server runs without memory
        logger.warning("memory store unavailable at startup: %s", e)


async def add(driver: str, turns: list[tuple[str, str]]) -> None:
    """turns: [(role, text)], role is "driver" or "copilot"."""
    now = datetime.now(timezone.utc)  # +1 ms per turn keeps the order
    rows = [(driver, r, t, now + timedelta(milliseconds=i)) for i, (r, t) in enumerate(turns) if t]
    if not rows:
        return
    kind = store()
    if kind == "off":
        _off()
    if kind == "sqlite":
        sq_rows = [(d, r, t, at.isoformat()) for d, r, t, at in rows]
        await _sq_run(lambda con: con.executemany(
            "INSERT INTO conversation (driver, role, text, at) VALUES (?, ?, ?, ?)", sq_rows))
        return
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
    kind = store()
    if kind == "off":
        _off()
    if kind == "sqlite":
        rows = await _sq_run(lambda con: con.execute(
            "SELECT role, text, at FROM conversation WHERE driver = ? ORDER BY at DESC, id DESC LIMIT ?",
            (driver, limit)).fetchall())
        return [{"role": r[0], "text": r[1], "at": r[2]} for r in reversed(rows)]
    pool = await _db()
    rows = await pool.fetch("SELECT role, text, at FROM conversation WHERE driver = $1 ORDER BY at DESC LIMIT $2", driver, limit)
    return [{"role": r["role"], "text": r["text"], "at": r["at"].astimezone(timezone.utc).isoformat()} for r in reversed(rows)]


async def count_turns(driver: str) -> int:
    kind = store()
    if kind == "off":
        _off()
    if kind == "sqlite":
        row = await _sq_run(lambda con: con.execute("SELECT count(*) FROM conversation WHERE driver = ?",
                                                    (driver,)).fetchone())
        return int(row[0] or 0)
    pool = await _db()
    return int(await pool.fetchval("SELECT count(*) FROM conversation WHERE driver = $1", driver) or 0)


async def get_facts(driver: str) -> dict | None:
    """{"facts": dict, "turns_seen": int, "updated_at": datetime} or None."""
    kind = store()
    if kind == "off":
        _off()
    if kind == "sqlite":
        row = await _sq_run(lambda con: con.execute(
            "SELECT facts, turns_seen, updated_at FROM driver_facts WHERE driver = ?", (driver,)).fetchone())
        if not row:
            return None
        try:
            facts = json.loads(row[0])
        except ValueError:
            return None
        return {"facts": facts, "turns_seen": int(row[1]), "updated_at": datetime.fromisoformat(row[2])}
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
    kind = store()
    if kind == "off":
        _off()
    if kind == "sqlite":
        args = (driver, json.dumps(facts), int(turns_seen), datetime.now(timezone.utc).isoformat())
        await _sq_run(lambda con: con.execute(
            "INSERT INTO driver_facts (driver, facts, turns_seen, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (driver) DO UPDATE SET facts = excluded.facts, turns_seen = excluded.turns_seen, "
            "updated_at = excluded.updated_at", args))
        return
    pool = await _db()
    await pool.execute(
        "INSERT INTO driver_facts (driver, facts, turns_seen, updated_at) VALUES ($1, $2, $3, $4) "
        "ON CONFLICT (driver) DO UPDATE SET facts = EXCLUDED.facts, turns_seen = EXCLUDED.turns_seen, "
        "updated_at = EXCLUDED.updated_at",
        driver, json.dumps(facts), int(turns_seen), datetime.now(timezone.utc))


async def clear_facts(driver: str) -> int:
    """"Forget that": the learned facts and the visits (the count is facts rows)."""
    kind = store()
    if kind == "off":
        _off()
    if kind == "sqlite":
        def _forget(con):
            con.execute("DELETE FROM driver_visits WHERE driver = ?", (driver,))
            con.execute("DELETE FROM driver_visit_trips WHERE driver = ?", (driver,))
            return con.execute("DELETE FROM driver_facts WHERE driver = ?", (driver,)).rowcount
        return await _sq_run(_forget)
    pool = await _db()
    await pool.execute("DELETE FROM driver_visits WHERE driver = $1", driver)
    await pool.execute("DELETE FROM driver_visit_trips WHERE driver = $1", driver)
    status = await pool.execute("DELETE FROM driver_facts WHERE driver = $1", driver)
    return int(status.split()[-1])


# ---- visits: the places the driver arrived at (the app's "arrived" events), counted apart from the likes --------

MAX_VISITS = 30  # places kept per driver, the most recently visited


async def add_visits(driver: str, visits: list[tuple[str, str | None, str | None, datetime, str | None]]) -> None:
    """One more visit to each (place_id, name, category, at, trip_id); the latest name and time win. A visit
    counts once per place per trip (driver_visit_trips): the app sends a batch again when an upload failed after
    it had landed, and a batch can hold two trips at one place. An arrival with no trip is not counted, nothing
    tells it from one sent again. Beyond MAX_VISITS places the least recently visited go, with their trips."""
    visits = [v for v in visits if v[4]]
    if not visits:
        return
    kind = store()
    if kind == "off":
        _off()
    if kind == "sqlite":
        def _count(con):
            con.execute("BEGIN")
            try:
                for place_id, name, category, at, trip in visits:
                    new = con.execute("INSERT OR IGNORE INTO driver_visit_trips (driver, place_id, trip_id) "
                                      "VALUES (?, ?, ?)", (driver, place_id, trip)).rowcount == 1
                    con.execute(
                        "INSERT INTO driver_visits (driver, place_id, name, category, count, last_at) "
                        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (driver, place_id) DO UPDATE SET "
                        "count = count + excluded.count, "
                        "name = coalesce(excluded.name, name), category = coalesce(excluded.category, category), "
                        "last_at = max(last_at, excluded.last_at)",
                        (driver, place_id, name, category, int(new), at.astimezone(timezone.utc).isoformat()))
                con.execute("DELETE FROM driver_visits WHERE driver = ? AND place_id NOT IN (SELECT place_id FROM "
                            "driver_visits WHERE driver = ? ORDER BY last_at DESC LIMIT ?)", (driver, driver, MAX_VISITS))
                con.execute("DELETE FROM driver_visit_trips WHERE driver = ? AND place_id NOT IN (SELECT place_id "
                            "FROM driver_visits WHERE driver = ?)", (driver, driver))
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        await _sq_run(_count)
        return
    pool = await _db()
    async with pool.acquire() as con, con.transaction():
        for place_id, name, category, at, trip in visits:
            status = await con.execute("INSERT INTO driver_visit_trips (driver, place_id, trip_id) VALUES ($1, $2, $3) "
                                       "ON CONFLICT DO NOTHING", driver, place_id, trip)
            await con.execute(
                "INSERT INTO driver_visits (driver, place_id, name, category, count, last_at) "
                "VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT (driver, place_id) DO UPDATE SET "
                "count = driver_visits.count + EXCLUDED.count, "
                "name = coalesce(EXCLUDED.name, driver_visits.name), "
                "category = coalesce(EXCLUDED.category, driver_visits.category), "
                "last_at = greatest(driver_visits.last_at, EXCLUDED.last_at)",
                driver, place_id, name, category, int(status.split()[-1]), at)
        await con.execute("DELETE FROM driver_visits WHERE driver = $1 AND place_id NOT IN (SELECT place_id FROM "
                          "driver_visits WHERE driver = $1 ORDER BY last_at DESC LIMIT $2)", driver, MAX_VISITS)
        await con.execute("DELETE FROM driver_visit_trips WHERE driver = $1 AND place_id NOT IN (SELECT place_id "
                          "FROM driver_visits WHERE driver = $1)", driver)


async def visits(driver: str) -> list[dict]:
    """The driver's visits, the most visited first: {place_id, name, category, count, last_at (datetime)}."""
    kind = store()
    if kind == "off":
        _off()
    cols = "place_id, name, category, count, last_at"
    if kind == "sqlite":
        rows = await _sq_run(lambda con: con.execute(
            f"SELECT {cols} FROM driver_visits WHERE driver = ? ORDER BY count DESC, last_at DESC", (driver,)).fetchall())
        return [{"place_id": r[0], "name": r[1], "category": r[2], "count": int(r[3]),
                 "last_at": datetime.fromisoformat(r[4])} for r in rows]
    pool = await _db()
    rows = await pool.fetch(f"SELECT {cols} FROM driver_visits WHERE driver = $1 ORDER BY count DESC, last_at DESC", driver)
    return [{**dict(r), "count": int(r["count"])} for r in rows]


async def clear(driver: str) -> int:
    """Everything about the driver: the turns, the learned facts, the visits, the trip events and the ledger of
    loads (the count is turns)."""
    if _pending.get(driver):
        await asyncio.gather(*list(_pending[driver]), return_exceptions=True)
    kind = store()
    if kind == "off":
        _off()
    if kind == "sqlite":
        def _wipe(con):
            n = con.execute("DELETE FROM conversation WHERE driver = ?", (driver,)).rowcount
            con.execute("DELETE FROM driver_facts WHERE driver = ?", (driver,))
            con.execute("DELETE FROM driver_visits WHERE driver = ?", (driver,))
            con.execute("DELETE FROM driver_visit_trips WHERE driver = ?", (driver,))
            con.execute("DELETE FROM trip_events WHERE driver = ?", (driver,))
            con.execute("DELETE FROM driver_loads WHERE driver = ?", (driver,))
            return n
        return await _sq_run(_wipe)
    pool = await _db()
    status = await pool.execute("DELETE FROM conversation WHERE driver = $1", driver)
    await pool.execute("DELETE FROM driver_facts WHERE driver = $1", driver)
    await pool.execute("DELETE FROM driver_visits WHERE driver = $1", driver)
    await pool.execute("DELETE FROM driver_visit_trips WHERE driver = $1", driver)
    await pool.execute("DELETE FROM trip_events WHERE driver = $1", driver)
    await pool.execute("DELETE FROM driver_loads WHERE driver = $1", driver)
    return int(status.split()[-1])
