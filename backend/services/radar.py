"""Precipitation radar tiles for the map's weather view, proxied from the Iowa Environmental Mesonet (IEM).

IEM serves the NEXRAD composite (live, every 5 min) and HRRR simulated reflectivity (a model forecast, hours
ahead) as XYZ tiles, public domain, no key. It is a university server with no SLA, so phones never call it:
they call /api/radar/... here, and one fetch per tile serves every driver. Swapping the source (self-rendered
MRMS, a paid API) is a change in this file only.

The tile URL carries a signed key instead of the bearer token (react-native-maps' UrlTile can't send headers).
RADAR=0 turns the weather view's data off.
"""
import base64
import hashlib
import hmac
import logging
import os
import re
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Optional

from services.flags import on
from services.http import client

logger = logging.getLogger("truckbuddy.radar")

IEM = "https://mesonet.agron.iastate.edu"
FRAME_URL = IEM + "/json/tms.json"
TIMEOUT_S = 8
FRAME_TTL_S = 120
LIVE_TTL_S = 300  # a frame's tiles never change, but "latest" moves on every 5 min
FORECAST_TTL_S = 1800  # HRRR runs hourly
MAX_Z = 10  # radar is ~1 km; past z10 the map stretches these tiles (UrlTile maximumNativeZ)
FORECAST_STEP_MIN = 15  # IEM serves HRRR reflectivity every 15 min of the run ...
FORECAST_MAX_MIN = 1080  # ... out to 18 h
HRRR_META = IEM + "/data/gis/images/4326/hrrr/refd_0000.json"  # {"model_init_utc": ...} of the run the "-0" layers show
CACHE_MAX_BYTES = 48 * 1024 * 1024

# Our short layer ids -> IEM layer names. Only these are proxied.
_LIVE = re.compile(r"^n0q-(\d{12})$")  # a timestamped NEXRAD composite frame, UTC YYYYMMDDHHMM
_FCST = re.compile(r"^hrrr-f(\d{3,4})$")  # HRRR simulated reflectivity, minutes ahead of the latest run


LIVE_BACK_MIN = 180  # live frames older than this aren't served (the app only asks for the latest)


def _live_ok(stamp: str, now: Optional[datetime]) -> bool:
    """A real NEXRAD frame time: on the 5-minute grid, within the last few hours, not in the future."""
    try:
        at = datetime.strptime(stamp, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    now = now or datetime.now(timezone.utc)
    return at.minute % 5 == 0 and now - timedelta(minutes=LIVE_BACK_MIN) <= at <= now + timedelta(minutes=10)


def iem_layer(layer: str, now: Optional[datetime] = None) -> Optional[str]:
    """The IEM layer for one of our ids, None for anything else (the proxy never fetches arbitrary layers, and a
    made-up frame time would be a fresh upstream fetch per tile)."""
    m = _LIVE.match(layer)
    if m:
        return f"ridge::USCOMP-N0Q-{m.group(1)}" if _live_ok(m.group(1), now) else None
    m = _FCST.match(layer)
    if m and int(m.group(1)) % FORECAST_STEP_MIN == 0 and int(m.group(1)) <= FORECAST_MAX_MIN:
        return f"hrrr::REFD-F{int(m.group(1)):04d}-0"
    return None


def tile_ok(z: int, x: int, y: int) -> bool:
    return 0 <= z <= MAX_Z and 0 <= x < 2 ** z and 0 <= y < 2 ** z


# ---- signed tile key -------------------------------------------------------------------------------------------

def _secret() -> bytes:
    return os.environ["JWT_SECRET"].encode()  # the same secret as the device tokens (services/auth.py)


def _sig(day: str, sub: str) -> str:
    mac = hmac.new(_secret(), f"radar:{day}:{sub}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac[:12]).decode().rstrip("=")


def _day(now: datetime) -> str:
    return now.strftime("%Y%m%d")


def key_for(sub: str, now: Optional[datetime] = None) -> str:
    """A tile key for this driver, good today and tomorrow (UTC), so a key handed out at 23:59 still works."""
    day = _day(now or datetime.now(timezone.utc))
    return f"{day}.{_b64(sub)}.{_sig(day, sub)}"


def driver_of(key: str, now: Optional[datetime] = None) -> Optional[str]:
    """The driver a tile key was issued to, None when it is forged or older than a day."""
    try:
        day, sub_b64, sig = key.split(".")
        sub = base64.urlsafe_b64decode(sub_b64 + "=" * (-len(sub_b64) % 4)).decode()
    except Exception:
        return None
    now = now or datetime.now(timezone.utc)
    if day not in (_day(now), _day(now - timedelta(days=1))) or not sig.isascii():
        return None  # compare_digest raises on non-ASCII text: a junk key is a 401, not a 500
    return sub if hmac.compare_digest(sig, _sig(day, sub)) else None


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


# ---- frames ----------------------------------------------------------------------------------------------------

_frame: dict = {"at": 0.0, "ts": None}


def _parse_frame(data: dict) -> Optional[str]:
    """The latest NEXRAD composite time (YYYYMMDDHHMM UTC) from IEM's tms.json."""
    for s in data.get("services", []):
        if s.get("id") == "ridge_uscomp_n0q":
            m = re.search(r"(\d{12})$", s.get("layername", ""))
            if m:
                return m.group(1)
    return None


async def latest_frame(now: Optional[float] = None) -> Optional[str]:
    """Latest live frame stamp, cached FRAME_TTL_S; on an IEM failure the last known one (or None)."""
    now = time.monotonic() if now is None else now
    if _frame["ts"] and now - _frame["at"] < FRAME_TTL_S:
        return _frame["ts"]
    try:
        r = await client().get(FRAME_URL, timeout=TIMEOUT_S)
        r.raise_for_status()
        ts = _parse_frame(r.json())
        if ts:
            _frame.update(at=now, ts=ts)
    except Exception as e:
        logger.warning("radar frame lookup failed: %s", e)
    return _frame["ts"]


_hrrr: dict = {"at": 0.0, "init": None}


async def hrrr_init(now: Optional[float] = None) -> Optional[str]:
    """When the HRRR run behind the forecast layers started (ISO, UTC); its steps count from here, not from now
    (a run reaches IEM about 2 h late). Cached FRAME_TTL_S; the last known one on failure."""
    now = time.monotonic() if now is None else now
    if _hrrr["init"] and now - _hrrr["at"] < FRAME_TTL_S:
        return _hrrr["init"]
    try:
        r = await client().get(HRRR_META, timeout=TIMEOUT_S)
        r.raise_for_status()
        init = (r.json() or {}).get("model_init_utc")
        if isinstance(init, str) and re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", init):
            _hrrr.update(at=now, init=init)
    except Exception as e:
        logger.warning("hrrr run lookup failed: %s", e)
    return _hrrr["init"]


def frame_time(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)


# ---- tiles -----------------------------------------------------------------------------------------------------

class _Lru:
    """Tile bytes by key, bounded by total size, each with its own expiry."""

    def __init__(self, max_bytes: int):
        self.max_bytes, self.size = max_bytes, 0
        self.items: "OrderedDict[str, tuple[float, bytes]]" = OrderedDict()

    def get(self, key: str, now: float) -> Optional[bytes]:
        hit = self.items.get(key)
        if not hit:
            return None
        if hit[0] < now:
            self._drop(key)
            return None
        self.items.move_to_end(key)
        return hit[1]

    def put(self, key: str, data: bytes, expires: float) -> None:
        if key in self.items:
            self._drop(key)
        self.items[key] = (expires, data)
        self.size += len(data)
        while self.size > self.max_bytes and self.items:
            self._drop(next(iter(self.items)))

    def _drop(self, key: str) -> None:
        _, data = self.items.pop(key)
        self.size -= len(data)


_tiles = _Lru(CACHE_MAX_BYTES)


class TileUnavailable(Exception):
    pass


UPSTREAM_PER_MIN = 900  # fetches to IEM per process per minute, all drivers together (one key can't flood them)
FAIL_TTL_S = 60  # a tile IEM failed on is not asked again for this long
_upstream = {"tokens": float(UPSTREAM_PER_MIN), "at": 0.0}
_failed: dict[str, float] = {}


def _take_upstream(now: float) -> bool:
    t = min(float(UPSTREAM_PER_MIN), _upstream["tokens"] + (now - _upstream["at"]) * UPSTREAM_PER_MIN / 60.0)
    _upstream["at"] = now
    if t < 1.0:
        _upstream["tokens"] = t
        return False
    _upstream["tokens"] = t - 1.0
    return True


async def fetch_tile(layer: str, z: int, x: int, y: int, now: Optional[float] = None) -> bytes:
    """PNG bytes for one of our layers. Raises ValueError for a layer or tile we don't serve, TileUnavailable
    when IEM fails (the app keeps the tiles it has)."""
    name = iem_layer(layer)
    if not name or not tile_ok(z, x, y):
        raise ValueError("no such radar tile")
    now = time.monotonic() if now is None else now
    forecast = name.startswith("hrrr::")
    # A forecast layer name is the same for every run: the run goes into the cache key so a new run is fetched.
    run = (_hrrr["init"] or str(int(time.time() // 3600))) if forecast else ""
    key = f"{layer}:{run}:{z}/{x}/{y}"
    hit = _tiles.get(key, now)
    if hit is not None:
        return hit
    if _failed.get(key, 0.0) > now or not _take_upstream(now):
        raise TileUnavailable()
    # /c/ is IEM's long-cache endpoint, for timestamped frames; HRRR "-0" moves, so it goes through /cache/.
    path = "cache" if forecast else "c"
    url = f"{IEM}/{path}/tile.py/1.0.0/{name}/{z}/{x}/{y}.png"
    try:
        r = await client().get(url, timeout=TIMEOUT_S)
        r.raise_for_status()
    except Exception as e:
        logger.warning("radar tile %s failed: %s", url, e)
        _fail(key, now)
        raise TileUnavailable() from e
    if not r.headers.get("content-type", "").startswith("image/"):
        _fail(key, now)
        raise TileUnavailable()
    _tiles.put(key, r.content, now + (FORECAST_TTL_S if forecast else LIVE_TTL_S))
    return r.content


def _fail(key: str, now: float) -> None:
    if len(_failed) > 5000:
        _failed.clear()
    _failed[key] = now + FAIL_TTL_S


def enabled() -> bool:
    return on("RADAR")


def reset_for_tests() -> None:
    global _tiles
    _tiles = _Lru(CACHE_MAX_BYTES)
    _frame.update(at=0.0, ts=None)
    _failed.clear()
    _upstream.update(tokens=float(UPSTREAM_PER_MIN), at=0.0)
    _hrrr.update(at=0.0, init=None)
