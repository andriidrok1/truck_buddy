"""Satellite verdicts and the approach to each place: in memory for the synchronous callers (the agent's context,
`approach.best/target`), mirrored from the `parking_checks` table so every worker and a restarted one see the
same verdicts (refreshed every REFRESH_S), and written through to it. Without a database (store off) the
per-key JSON files in backend/.parking_cache remain the fallback, as before.

Rows: key -> (ParkingCheck, retried) for verdicts; "approach:<lat,lng>" -> Approach for the way a route arrives.
A verdict without an entry point (E) may be re-checked once; after that it's final (retried = true).
"""
import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

from models import Approach, ParkingCheck
from services import db, kv

logger = logging.getLogger("truckbuddy.parking_store")

DIR = Path(os.environ.get("PARKING_CACHE_DIR") or Path(__file__).resolve().parent.parent / ".parking_cache")
REFRESH_S = 60  # how stale another worker's verdict may be here
APPROACH_PREFIX = "approach:"
_mem: dict[str, tuple[ParkingCheck, bool]] = {}  # mirror of the table (or the files), loaded once, refreshed
_approach: dict[str, Approach] = {}
_tasks: set = set()
_refresh_task: Optional[asyncio.Task] = None


def _file(key: str) -> Path:
    return DIR / (re.sub(r"[^0-9a-zA-Z.\-]", "_", key) + ".json")


def has_entry(c: Optional[ParkingCheck]) -> bool:
    return bool(c and any(s.kind == "entry" for s in c.spots))


_CYRILLIC = re.compile(r"[А-Яа-яЁёІіЇїЄєҐґ]")


def localized(c: ParkingCheck) -> bool:
    """A verdict saved after a Russian request translated the cached copy in place (fixed 2026-11-05): its English
    text is gone, so it is not loaded and the place is checked again. Verdicts are stored in English only."""
    return any(_CYRILLIC.search(v or "") for v in (c.label, c.reason, c.entry_tip, c.park_tip))


def _files_on() -> bool:
    return db.store() == "off"


def _schedule(coro) -> None:
    """A write to the table from synchronous code: a task when a loop runs, nothing otherwise (import time)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        return
    t = loop.create_task(_guarded(coro))
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


async def _guarded(coro) -> None:
    try:
        await coro
    except Exception as e:  # noqa: BLE001 - the memory copy is already right; the table catches up next time
        logger.warning("parking store write failed: %s", e)


def load_all() -> dict[str, ParkingCheck]:
    """Read every saved verdict from the files (called at import; the table is loaded by sync_from_db at startup)."""
    if not DIR.exists():
        return {}
    for f in DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text())
            c = ParkingCheck(**d["check"])
            if localized(c):
                logger.warning("parking cache file %s holds translated text; re-checked", f.name)
                continue
            _mem[d["key"]] = (c, bool(d.get("retried")))
        except Exception as e:  # noqa: BLE001 - a broken file is just re-checked
            logger.warning("bad parking cache file %s: %s", f.name, e)
    logger.info("parking cache: %d verdicts loaded", len(_mem))
    return {k: c for k, (c, _r) in _mem.items()}


async def sync_from_db() -> int:
    """The table into memory: verdicts and approaches other workers saved. Returns the row count."""
    if db.store() == "off":
        return 0
    rows = await kv.parking_all()
    for key, check, retried, ap in rows:
        if key.startswith(APPROACH_PREFIX):
            if ap:
                try:
                    _approach[key[len(APPROACH_PREFIX):]] = Approach(**ap)
                except Exception:  # noqa: BLE001
                    continue
            continue
        if check:
            try:
                c = ParkingCheck(**check)
            except Exception as e:  # noqa: BLE001
                logger.warning("bad parking row %s: %s", key, e)
            else:
                if localized(c):
                    logger.warning("parking row %s holds translated text; re-checked", key)
                else:
                    _mem[key] = (c, retried)
        if ap:
            try:
                _approach[key] = Approach(**ap)
            except Exception:  # noqa: BLE001
                pass
    return len(rows)


async def _refresh_loop() -> None:
    while True:
        await asyncio.sleep(REFRESH_S)
        try:
            await sync_from_db()
        except Exception as e:  # noqa: BLE001
            logger.info("parking store refresh skipped: %s", type(e).__name__)


async def start() -> None:
    """At startup: load the table once, then keep the mirror fresh (every worker)."""
    global _refresh_task
    if db.store() == "off":
        return
    try:
        n = await sync_from_db()
        logger.info("parking store: %d rows from the database", n)
    except Exception as e:  # noqa: BLE001
        logger.warning("parking store load failed: %s", e)
    if _refresh_task is None:
        _refresh_task = asyncio.create_task(_refresh_loop())


async def stop() -> None:
    global _refresh_task
    if _refresh_task:
        _refresh_task.cancel()
        _refresh_task = None


def get(key: str) -> Optional[ParkingCheck]:
    hit = _mem.get(key)
    return hit[0] if hit else None


def needs_retry(key: str) -> bool:
    """Saved without an entry point and not re-checked yet."""
    hit = _mem.get(key)
    return bool(hit and not hit[1] and not has_entry(hit[0]))


def drop(key: str) -> None:
    """Forgets a saved verdict (e.g. one saved for a town center before towns were excluded)."""
    if _mem.pop(key, None) is not None:
        logger.info("parking cache: dropped %s", key)
    if _files_on():
        try:
            _file(key).unlink(missing_ok=True)
        except OSError as e:
            logger.warning("parking cache delete failed for %s: %s", key, e)
        return
    _schedule(kv.parking_delete(key))


def save(key: str, check: ParkingCheck, retried: bool) -> None:
    _mem[key] = (check, retried)
    if _files_on():
        try:
            DIR.mkdir(parents=True, exist_ok=True)
            tmp = _file(key).with_suffix(".tmp")
            tmp.write_text(json.dumps({"key": key, "retried": retried, "check": check.model_dump()}))
            tmp.replace(_file(key))
        except OSError as e:
            logger.warning("parking cache write failed for %s: %s", key, e)
        return
    ap = _approach.get(key)
    _schedule(kv.parking_put(key, check.model_dump(mode="json"), retried, ap.model_dump(mode="json") if ap else None))


def approach_get(pkey: str) -> Optional[Approach]:
    return _approach.get(pkey)


def approach_set(pkey: str, ap: Approach) -> None:
    """The way the latest route arrives at a place; shared with other workers through the table."""
    _approach[pkey] = ap
    if _files_on():
        return
    _schedule(kv.parking_set_approach(APPROACH_PREFIX + pkey, ap.model_dump(mode="json")))


def stats() -> dict:
    return {"total": len(_mem), "with_entry": sum(1 for c, _r in _mem.values() if has_entry(c))}


def pending() -> set:
    return _tasks


def _reset_for_tests() -> None:
    _mem.clear()
    _approach.clear()
