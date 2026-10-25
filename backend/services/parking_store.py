"""Satellite verdicts on disk (backend/.parking_cache/{key}.json), like TTS in .tts_cache.

Each file: {"check": ParkingCheck, "retried": bool}. A verdict without an entry point (E) may be
re-checked once; after that it's final (retried = true).
"""
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

from models import ParkingCheck

logger = logging.getLogger("truckbuddy.parking_store")

DIR = Path(os.environ.get("PARKING_CACHE_DIR") or Path(__file__).resolve().parent.parent / ".parking_cache")
_mem: dict[str, tuple[ParkingCheck, bool]] = {}  # mirror of the files, loaded once


def _file(key: str) -> Path:
    return DIR / (re.sub(r"[^0-9a-zA-Z.\-]", "_", key) + ".json")


def has_entry(c: Optional[ParkingCheck]) -> bool:
    return bool(c and any(s.kind == "entry" for s in c.spots))


def load_all() -> dict[str, ParkingCheck]:
    """Read every saved verdict (called at import). -> {key: check}."""
    if not DIR.exists():
        return {}
    for f in DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text())
            _mem[d["key"]] = (ParkingCheck(**d["check"]), bool(d.get("retried")))
        except Exception as e:  # noqa: BLE001 - a broken file is just re-checked
            logger.warning("bad parking cache file %s: %s", f.name, e)
    logger.info("parking cache: %d verdicts loaded", len(_mem))
    return {k: c for k, (c, _r) in _mem.items()}


def get(key: str) -> Optional[ParkingCheck]:
    hit = _mem.get(key)
    return hit[0] if hit else None


def needs_retry(key: str) -> bool:
    """Saved without an entry point and not re-checked yet."""
    hit = _mem.get(key)
    return bool(hit and not hit[1] and not has_entry(hit[0]))


def save(key: str, check: ParkingCheck, retried: bool) -> None:
    _mem[key] = (check, retried)
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        tmp = _file(key).with_suffix(".tmp")
        tmp.write_text(json.dumps({"key": key, "retried": retried, "check": check.model_dump()}))
        tmp.replace(_file(key))
    except OSError as e:
        logger.warning("parking cache write failed for %s: %s", key, e)


def stats() -> dict:
    return {"total": len(_mem), "with_entry": sum(1 for c, _r in _mem.values() if has_entry(c))}
