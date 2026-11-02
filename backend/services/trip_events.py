"""Trip events: what happened on a drive, from the app (reroutes, maneuver calls, dropped speech, failed turns)
and from the server (the far-stop question, destination checks, "didn't catch that", routing failures).

Same store as the conversation memory (services/memory.py: Postgres when DATABASE_URL is set, SQLite otherwise,
MEMORY_STORE=off turns both off). Rows: driver, trip_id (NULL for a server event the app didn't tag), kind, at,
source ('app' | 'server'), data (JSON text). Kept RETENTION_DAYS, pruned at startup and lazily.

The transcript of a failed turn is stored as is (decision 2026-11-01): it is what tells us what drivers say and
what the copilot missed. It is read only by the driver's own routes and the tester route; a fleet dashboard
reads aggregates, never this table.
"""
import asyncio
import json
import logging
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from services import memory

logger = logging.getLogger("truckbuddy.trips")

RETENTION_DAYS = int(os.environ.get("TRIP_EVENTS_DAYS", "90"))
MAX_BATCH = 200
MAX_DATA_BYTES = 16 * 1024
MAX_TRIP_ID = 64
KIND_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
PRUNE_EVERY_S = 6 * 3600
PAST_LIMIT = timedelta(days=30)  # an event older than this is a clock problem, not a drive
FUTURE_LIMIT = timedelta(minutes=5)

# Kinds the app and the server send today; an unknown kind is stored and logged, never rejected (the app adds
# kinds on its own schedule).
KNOWN_KINDS = frozenset({
    "route_started", "reroute", "alternative_picked", "stop_added", "stops_cleared", "route_ended",
    "maneuver_said", "speech_dropped", "turn_failed",
    "stop_guard", "dest_check", "stt_confirm", "not_understood", "turn_error", "route_failed", "reroute_requested",
})

_tasks: set = set()
_pending: "defaultdict[str, set]" = defaultdict(set)  # driver -> saves still in flight
_last_prune = 0.0


def parse_time(t, now: Optional[datetime] = None) -> datetime:
    """ISO 8601 or epoch (ms when > 1e11, else s); missing or unreadable = now; clamped to [now - 30 d, now + 5 min]."""
    now = now or datetime.now(timezone.utc)
    at: Optional[datetime] = None
    if isinstance(t, datetime):
        at = t
    elif isinstance(t, (int, float)) and not isinstance(t, bool):
        secs = float(t) / (1000.0 if t > 1e11 else 1.0)
        try:
            at = datetime.fromtimestamp(secs, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            at = None
    elif isinstance(t, str) and t.strip():
        try:
            at = datetime.fromisoformat(t.strip().replace("Z", "+00:00"))
        except ValueError:
            at = None
    if at is None:
        return now
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    at = at.astimezone(timezone.utc)
    return max(now - PAST_LIMIT, min(at, now + FUTURE_LIMIT))


def _iso(at: datetime) -> str:
    return at.astimezone(timezone.utc).isoformat()


def normalize(driver: str, events: list[dict], source: str = "app") -> list[tuple]:
    """Rows (driver, trip_id, kind, at, source, data_json) for a batch; a bad shape raises ValueError with the
    reason, so the API can answer 422 for the whole batch."""
    if len(events) > MAX_BATCH:
        raise ValueError(f"at most {MAX_BATCH} events per batch")
    now = datetime.now(timezone.utc)
    rows = []
    for i, e in enumerate(events):
        if not isinstance(e, dict):
            raise ValueError(f"event {i} is not an object")
        kind = str(e.get("kind") or "")
        if not KIND_RE.match(kind):
            raise ValueError(f"event {i}: bad kind {kind!r}")
        trip_id = e.get("trip_id")
        if trip_id is not None:
            trip_id = str(trip_id)
            if not trip_id or len(trip_id) > MAX_TRIP_ID:
                raise ValueError(f"event {i}: bad trip_id")
        data = e.get("data")
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ValueError(f"event {i}: data is not an object")
        text = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        if len(text.encode("utf-8")) > MAX_DATA_BYTES:
            raise ValueError(f"event {i}: data over {MAX_DATA_BYTES} bytes")
        if kind not in KNOWN_KINDS:
            logger.info("trip event of unknown kind %r from %s", kind, source)
        rows.append((driver, trip_id, kind, parse_time(e.get("t"), now) + timedelta(microseconds=i), source, text))
    return rows


async def add(driver: str, events: list[dict], source: str = "app") -> int:
    """Stores a batch; returns how many rows went in. Raises ValueError for a bad batch, RuntimeError when off."""
    rows = normalize(driver, events, source)
    if not rows:
        return 0
    kind = memory.store()
    if kind == "off":
        raise RuntimeError("memory store is off")
    if kind == "sqlite":
        sq_rows = [(d, t, k, _iso(at), s, j) for d, t, k, at, s, j in rows]
        await memory._sq_run(lambda con: con.executemany(
            "INSERT INTO trip_events (driver, trip_id, kind, at, source, data) VALUES (?, ?, ?, ?, ?, ?)", sq_rows))
    else:
        pool = await memory._db()
        await pool.executemany(
            "INSERT INTO trip_events (driver, trip_id, kind, at, source, data) VALUES ($1, $2, $3, $4, $5, $6)", rows)
    _maybe_prune()
    return len(rows)


def add_later(driver: Optional[str], trip_id: Optional[str], kind: str, data: Optional[dict] = None,
              source: str = "server") -> None:
    """A server-side event as a background task: the reply never waits on the database. No-op without a driver
    or with the store off."""
    if not driver or memory.store() == "off":
        return

    async def _save():
        try:
            await add(driver, [{"trip_id": trip_id, "kind": kind, "data": data or {}}], source)
        except Exception as e:  # noqa: BLE001
            logger.warning("trip event save failed: %s", e)

    t = asyncio.create_task(_save())
    _tasks.add(t)
    _pending[driver].add(t)

    def _done(task):
        _tasks.discard(task)
        _pending[driver].discard(task)
        if not _pending[driver]:
            _pending.pop(driver, None)

    t.add_done_callback(_done)


async def _settled(driver: str) -> None:
    if _pending.get(driver):
        await asyncio.gather(*list(_pending[driver]), return_exceptions=True)


def _row(r, with_driver: bool = False) -> dict:
    """One stored row (sqlite tuple in _COLS order, or an asyncpg record as dict) -> the API shape."""
    if not isinstance(r, dict):
        r = dict(zip(("id", "driver", "trip_id", "at", "kind", "source", "data"), r))
    at = r["at"]
    out = {"id": r["id"], "t": _iso(at) if isinstance(at, datetime) else str(at), "trip_id": r["trip_id"],
           "kind": r["kind"], "source": r["source"], "data": json.loads(r["data"])}
    if with_driver:
        out["driver"] = r["driver"]
    return out


_COLS = "id, driver, trip_id, at, kind, source, data"


async def for_trip(driver: str, trip_id: str, limit: int = 1000) -> list[dict]:
    """The driver's events of one trip, oldest first; waits for this driver's saves in flight."""
    await _settled(driver)
    kind = memory.store()
    if kind == "off":
        raise RuntimeError("memory store is off")
    if kind == "sqlite":
        rows = await memory._sq_run(lambda con: con.execute(
            f"SELECT {_COLS} FROM trip_events WHERE driver = ? AND trip_id = ? ORDER BY at, id LIMIT ?",
            (driver, trip_id, limit)).fetchall())
        return [_row(r) for r in rows]
    pool = await memory._db()
    rows = await pool.fetch(f"SELECT {_COLS} FROM trip_events WHERE driver = $1 AND trip_id = $2 ORDER BY at, id LIMIT $3",
                            driver, trip_id, limit)
    return [_row(dict(r)) for r in rows]


async def trips(driver: str, limit: int = 20) -> list[dict]:
    """The driver's trips, newest first: {trip_id, first_at, last_at, n, kinds: {kind: n}}."""
    await _settled(driver)
    kind = memory.store()
    if kind == "off":
        raise RuntimeError("memory store is off")
    q_sqlite = ("SELECT trip_id, kind, count(*), min(at), max(at) FROM trip_events WHERE driver = ? AND trip_id IS NOT NULL "
                "GROUP BY trip_id, kind")
    if kind == "sqlite":
        rows = await memory._sq_run(lambda con: con.execute(q_sqlite, (driver,)).fetchall())
        rows = [(r[0], r[1], r[2], str(r[3]), str(r[4])) for r in rows]
    else:
        pool = await memory._db()
        got = await pool.fetch("SELECT trip_id, kind, count(*) AS n, min(at) AS first_at, max(at) AS last_at FROM trip_events "
                               "WHERE driver = $1 AND trip_id IS NOT NULL GROUP BY trip_id, kind", driver)
        rows = [(r["trip_id"], r["kind"], r["n"], _iso(r["first_at"]), _iso(r["last_at"])) for r in got]
    by_trip: dict[str, dict] = {}
    for trip_id, k, n, first, last in rows:
        t = by_trip.setdefault(trip_id, {"trip_id": trip_id, "first_at": first, "last_at": last, "n": 0, "kinds": {}})
        t["n"] += n
        t["kinds"][k] = n
        t["first_at"] = min(t["first_at"], first)
        t["last_at"] = max(t["last_at"], last)
    return sorted(by_trip.values(), key=lambda t: t["last_at"], reverse=True)[:limit]


async def recent(limit: int = 200) -> list[dict]:
    """Newest events across all drivers (tester screen); pseudonymous driver ids come along."""
    kind = memory.store()
    if kind == "off":
        raise RuntimeError("memory store is off")
    if kind == "sqlite":
        rows = await memory._sq_run(lambda con: con.execute(
            f"SELECT {_COLS} FROM trip_events ORDER BY at DESC, id DESC LIMIT ?", (limit,)).fetchall())
        return [_row(r, True) for r in rows]
    pool = await memory._db()
    rows = await pool.fetch(f"SELECT {_COLS} FROM trip_events ORDER BY at DESC, id DESC LIMIT $1", limit)
    return [_row(dict(r), True) for r in rows]


async def clear(driver: str) -> int:
    await _settled(driver)
    kind = memory.store()
    if kind == "off":
        raise RuntimeError("memory store is off")
    if kind == "sqlite":
        return await memory._sq_run(lambda con: con.execute("DELETE FROM trip_events WHERE driver = ?", (driver,)).rowcount)
    pool = await memory._db()
    status = await pool.execute("DELETE FROM trip_events WHERE driver = $1", driver)
    return int(status.split()[-1])


async def prune(days: int = RETENTION_DAYS) -> int:
    """Drops events older than `days`; returns how many went."""
    global _last_prune
    _last_prune = time.monotonic()
    kind = memory.store()
    if kind == "off":
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    if kind == "sqlite":
        return await memory._sq_run(lambda con: con.execute("DELETE FROM trip_events WHERE at < ?", (_iso(cutoff),)).rowcount)
    pool = await memory._db()
    status = await pool.execute("DELETE FROM trip_events WHERE at < $1", cutoff)
    return int(status.split()[-1])


def _maybe_prune() -> None:
    if time.monotonic() - _last_prune < PRUNE_EVERY_S:
        return

    async def _run():
        try:
            n = await prune()
            if n:
                logger.info("trip events pruned: %d", n)
        except Exception as e:  # noqa: BLE001
            logger.info("trip events prune skipped: %s", e)

    t = asyncio.create_task(_run())
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


# ---- server-side events from a copilot turn --------------------------------------------------------------------

# timing.note() labels of a turn -> event kind; the note's value becomes data["result"].
_NOTE_KINDS = {"stop_guard": "stop_guard", "dest_check": "dest_check", "confirm": "stt_confirm"}


def from_turn(driver: Optional[str], trip_id: Optional[str], status: str, intent: Optional[str], notes: dict,
              text: str = "") -> None:
    """The server's own events for one copilot turn, from the turn's status and timing notes (called by
    server._finish). Nothing for an ordinary successful turn."""
    if not driver:
        return
    for note, kind in _NOTE_KINDS.items():
        if note in notes:
            add_later(driver, trip_id, kind, {"result": str(notes[note]), "intent": intent})
    if status == "not_understood":
        add_later(driver, trip_id, "not_understood", {"intent": intent, "text": text})
    elif status == "error":
        err = str(notes.get("error") or "unknown")
        if err == "no_truck_route":
            add_later(driver, trip_id, "route_failed", {"where": "agent", "error": err, "intent": intent})
        else:
            add_later(driver, trip_id, "turn_error", {"error": err, "intent": intent, "text": text})
