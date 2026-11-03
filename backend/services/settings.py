"""Process-wide settings read once (the few that other modules need at import time are read lazily on first use
and cached): how many uvicorn workers share this box, where TTS clips live, the per-driver budgets.

WORKERS (or WEB_CONCURRENCY, uvicorn's own name) makes the per-driver rate limits per worker: each worker
keeps its own buckets, so a budget of 40/min across N workers is 40/N per worker. TTS_STREAM is off for more
than one worker (the stream lives in the process that started it). `reload()` is for tests."""
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_BACKEND = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    workers: int
    tts_dir: Path
    rate_per_min: int
    rate_per_10min: int
    rate_per_day: int

    def per_worker(self, total: int) -> int:
        """A per-driver budget shared by all workers, as each worker's share (never below 1; 0 stays 0 = off)."""
        if total <= 0:
            return 0
        return max(1, math.ceil(total / max(1, self.workers)))


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def load() -> Settings:
    workers = _int("WORKERS", 0) or _int("WEB_CONCURRENCY", 0) or 1
    return Settings(
        workers=max(1, workers),
        tts_dir=Path(os.environ.get("TTS_DIR") or _BACKEND / "data" / "tts"),
        rate_per_min=_int("RATE_LIMIT_PER_MIN", 40),
        rate_per_10min=_int("RATE_PER_10MIN", 300),
        rate_per_day=_int("RATE_PER_DAY", 3000),
    )


_settings: Optional[Settings] = None


def settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load()
    return _settings


def reload() -> Settings:
    global _settings
    _settings = load()
    return _settings
