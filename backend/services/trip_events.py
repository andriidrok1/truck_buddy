"""Trip events: what happened on a drive, from the app (reroutes, maneuver calls, dropped speech, failed turns)
and from the server (the far-stop question, destination checks, "didn't catch that", routing failures).

Same store as the conversation memory (services/memory.py: Postgres when DATABASE_URL is set, SQLite otherwise,
MEMORY_STORE=off turns both off). Rows: driver, trip_id (NULL for a server event the app didn't tag), kind, at,
source ('app' | 'server'), data (JSON text), and since migration 0005 org_id, vehicle_id (from the driver's
Principal at write time) and lat, lng (the request's position for a server event, data.lat/data.lng for an app
event). Kept RETENTION_DAYS, pruned at startup and lazily.

The kinds a fleet dashboard reads (DASHBOARD_MODELS) have their data checked against a model; a wrong type is a
422 for the batch like any other bad event. Extra fields pass, and every other kind is stored as is.

The transcript of a failed turn is stored as is (decision 2026-11-01): it is what tells us what drivers say and
what the copilot missed. It is read only by the driver's own routes and the tester route; the fleet routes
(services/fleet_api.py) strip it before anything leaves the server.
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

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from services import memory, reqid

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
    "route_started", "reroute", "alternative_picked", "stop_added", "stops_cleared", "stop_removed", "route_ended",
    "route_restored", "route_replaced", "turn_unknown_action",
    "maneuver_said", "speech_dropped", "turn_failed",
    "stop_guard", "dest_check", "stt_confirm", "not_understood", "turn_error", "route_failed", "reroute_requested",
})


class _Data(BaseModel):
    """Common to every dashboard kind: where the truck was (stored as a position only when both are there)."""
    model_config = ConfigDict(extra="allow")
    lat: Optional[float] = Field(None, ge=-90, le=90)
    lng: Optional[float] = Field(None, ge=-180, le=180)


class RouteStarted(_Data):
    place_id: Optional[str] = None
    category: Optional[str] = None
    distance_m: Optional[float] = None
    duration_s: Optional[float] = None
    alternatives: Optional[int] = None


class Reroute(_Data):
    cause: Optional[str] = None
    ok: Optional[bool] = None
    fallback: Optional[bool] = None
    section_index: Optional[int] = None


class RerouteRequested(_Data):
    section_index: Optional[int] = None
    handle: Optional[bool] = None
    via: Optional[int] = None
    ok: Optional[bool] = None
    error: Optional[str] = None


class GuardResult(_Data):
    result: Optional[str] = None
    intent: Optional[str] = None


class RouteEnded(_Data):
    place_id: Optional[str] = None
    driving_s: Optional[float] = None
    along_m: Optional[float] = None
    remaining_m: Optional[float] = None


class ManeuverSaid(_Data):
    action: Optional[str] = None
    index: Optional[int] = None
    to_next_m: Optional[float] = None


DASHBOARD_MODELS: dict[str, type[_Data]] = {
    "route_started": RouteStarted, "reroute": Reroute, "reroute_requested": RerouteRequested,
    "stop_guard": GuardResult, "dest_check": GuardResult, "route_ended": RouteEnded, "maneuver_said": ManeuverSaid,
}


def _position(data: dict) -> tuple[Optional[float], Optional[float]]:
    """(lat, lng) from an event's data when both are numbers in range, else (None, None)."""
    lat, lng = data.get("lat"), data.get("lng")
    ok = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (lat, lng))
    if not ok or not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None, None
    return float(lat), float(lng)


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
    """Rows (driver, trip_id, kind, at, source, data_json, org_id, vehicle_id, lat, lng) for a batch; a bad shape
    raises ValueError with the reason, so the API can answer 422 for the whole batch. org_id/vehicle_id come from
    the driver when it is an auth.Principal (None for a bare id)."""
    org_id, vehicle_id = getattr(driver, "org_id", None), getattr(driver, "vehicle_id", None)
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
        model = DASHBOARD_MODELS.get(kind)
        if model is not None:
            try:
                model.model_validate(data)
            except ValidationError as err:
                first = err.errors()[0]
                where = ".".join(str(x) for x in first.get("loc", ()))
                raise ValueError(f"event {i}: {kind} data.{where}: {first.get('msg')}")
        if kind not in KNOWN_KINDS:
            logger.info("trip event of unknown kind %r from %s", kind, source)
        lat, lng = _position(data)
        rows.append((str(driver), trip_id, kind, parse_time(e.get("t"), now) + timedelta(microseconds=i), source, text,
                     org_id, vehicle_id, lat, lng))
    return rows


_INSERT_COLS = "driver, trip_id, kind, at, source, data, org_id, vehicle_id, lat, lng"


async def add(driver: str, events: list[dict], source: str = "app") -> int:
    """Stores a batch; returns how many rows went in. Raises ValueError for a bad batch, RuntimeError when off."""
    rows = normalize(driver, events, source)
    if not rows:
        return 0
    kind = memory.store()
    if kind == "off":
        raise RuntimeError("memory store is off")
    if kind == "sqlite":
        sq_rows = [(r[0], r[1], r[2], _iso(r[3]), *r[4:]) for r in rows]
        await memory._sq_run(lambda con: con.executemany(
            f"INSERT INTO trip_events ({_INSERT_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", sq_rows))
    else:
        pool = await memory._db()
        await pool.executemany(
            f"INSERT INTO trip_events ({_INSERT_COLS}) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)", rows)
    _maybe_prune()
    return len(rows)


def add_later(driver: Optional[str], trip_id: Optional[str], kind: str, data: Optional[dict] = None,
              source: str = "server", pos: Optional[tuple[float, float]] = None) -> None:
    """A server-side event as a background task: the reply never waits on the database. No-op without a driver
    or with the store off. `pos` is the request's (lat, lng); the request id rides along as data.req so the
    event joins the turn log line."""
    if not driver or memory.store() == "off":
        return
    data = dict(data or {})
    if pos is not None:
        data.setdefault("lat", pos[0])
        data.setdefault("lng", pos[1])
    rid = reqid.current()
    if rid:
        data.setdefault("req", rid)

    async def _save():
        try:
            await add(driver, [{"trip_id": trip_id, "kind": kind, "data": data}], source)
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


# ---- fleet reads (services/fleet_api.py strips transcripts from what these return) -------------------------

_FLEET_FIELDS = ("id", "trip_id", "at", "kind", "source", "data", "vehicle_id", "lat", "lng")
_FLEET_COLS = ", ".join(_FLEET_FIELDS)


def _fleet_row(r) -> dict:
    if not isinstance(r, dict):
        r = dict(zip(_FLEET_FIELDS, r))
    at = r["at"]
    return {"id": r["id"], "t": _iso(at) if isinstance(at, datetime) else str(at), "trip_id": r["trip_id"],
            "kind": r["kind"], "source": r["source"], "data": json.loads(r["data"]), "vehicle_id": r["vehicle_id"],
            "lat": r["lat"], "lng": r["lng"]}


async def _fleet_fetch(sql_sqlite: str, sql_pg: str, args: tuple) -> list:
    """Rows of a fleet query (one SQL per dialect, same argument order); RuntimeError when the store is off."""
    kind = memory.store()
    if kind == "off":
        raise RuntimeError("memory store is off")
    if kind == "sqlite":
        sq_args = tuple(_iso(a) if isinstance(a, datetime) else a for a in args)
        return await memory._sq_run(lambda con: con.execute(sql_sqlite, sq_args).fetchall())
    pool = await memory._db()
    return [dict(r) for r in await pool.fetch(sql_pg, *args)]


async def vehicle_day(org_id: str, vehicle_id: str, start: datetime, end: datetime, limit: int = 2000) -> list[dict]:
    """One truck's events in [start, end), oldest first; the org is part of the filter, so a vehicle id from
    another fleet finds nothing."""
    rows = await _fleet_fetch(
        f"SELECT {_FLEET_COLS} FROM trip_events WHERE org_id = ? AND vehicle_id = ? AND at >= ? AND at < ? "
        "ORDER BY at, id LIMIT ?",
        f"SELECT {_FLEET_COLS} FROM trip_events WHERE org_id = $1 AND vehicle_id = $2 AND at >= $3 AND at < $4 "
        "ORDER BY at, id LIMIT $5",
        (org_id, vehicle_id, start, end, limit))
    return [_fleet_row(r) for r in rows]


async def org_day(org_id: str, start: datetime, end: datetime, kinds: tuple[str, ...]) -> list[dict]:
    """The org's events of these kinds in [start, end) (the summary counts them)."""
    marks = ", ".join("?" for _ in kinds)
    pg_marks = ", ".join(f"${i + 4}" for i in range(len(kinds)))
    rows = await _fleet_fetch(
        f"SELECT {_FLEET_COLS} FROM trip_events WHERE org_id = ? AND at >= ? AND at < ? AND kind IN ({marks})",
        f"SELECT {_FLEET_COLS} FROM trip_events WHERE org_id = $1 AND at >= $2 AND at < $3 AND kind IN ({pg_marks})",
        (org_id, start, end, *kinds))
    return [_fleet_row(r) for r in rows]


_ROUTE_KINDS = ("route_started", "route_ended")


async def org_latest(org_id: str) -> dict[str, dict]:
    """Per vehicle of the org: the last event, the last event with a position, the last route start or end.
    {vehicle_id: {"last": row, "position": row | None, "route": row | None}}; three grouped queries, not one
    per truck."""
    cols = ", ".join(f"e.{c}" for c in _FLEET_FIELDS)
    sub = "SELECT vehicle_id, max(at) AS at FROM trip_events WHERE org_id = {p} AND vehicle_id IS NOT NULL {x} GROUP BY vehicle_id"
    outer = ("SELECT {cols} FROM trip_events e JOIN ({sub}) m ON e.vehicle_id = m.vehicle_id AND e.at = m.at "
             "WHERE e.org_id = {p} {x} ORDER BY e.id")
    route_only = "AND kind IN (" + ", ".join(f"'{k}'" for k in _ROUTE_KINDS) + ")"
    out: dict[str, dict] = {}
    for key, extra in (("last", ""), ("position", "AND lat IS NOT NULL"), ("route", route_only)):
        sq = outer.format(cols=cols, sub=sub.format(p="?", x=extra), p="?", x=extra)
        pg = outer.format(cols=cols, sub=sub.format(p="$1", x=extra), p="$1", x=extra)
        args = (org_id, org_id) if memory.store() == "sqlite" else (org_id,)
        for r in await _fleet_fetch(sq, pg, args):
            row = _fleet_row(r)  # a tie on `at` keeps the higher id (ORDER BY e.id)
            out.setdefault(row["vehicle_id"], {"last": None, "position": None, "route": None})[key] = row
    return out


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
              text: str = "", pos: Optional[tuple[float, float]] = None) -> None:
    """The server's own events for one copilot turn, from the turn's status and timing notes (called by
    server._finish). Nothing for an ordinary successful turn."""
    if not driver:
        return
    for note, kind in _NOTE_KINDS.items():
        if note in notes:
            add_later(driver, trip_id, kind, {"result": str(notes[note]), "intent": intent}, pos=pos)
    if status == "not_understood":
        add_later(driver, trip_id, "not_understood", {"intent": intent, "text": text}, pos=pos)
    elif status == "error":
        err = str(notes.get("error") or "unknown")
        if err == "no_truck_route":
            add_later(driver, trip_id, "route_failed", {"where": "agent", "error": err, "intent": intent}, pos=pos)
        else:
            add_later(driver, trip_id, "turn_error", {"error": err, "intent": intent, "text": text}, pos=pos)
