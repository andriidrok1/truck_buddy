"""Truck parking check.

1. If the place data already lists truck parking -> verdict "listed".
2. Always fetch an Esri World Imagery satellite tile around the place and ask
   GPT 5.5 (vision) whether a 53 ft tractor-trailer can park there.
3. The verdict comes from the grounded check (services.parking_ground: ringed place, grid, OSM driveways, two
   samples), and so does the entry when OSM driveways were on the frame; without them there is no entry point
   (the route goes to the place). The parking spot comes from the bare-image call. Eval 2026-11-02, 41 + 34
   hand-labelled places: grounded verdict 60/67 right, 3 confident mistakes (bare 58/67, 7); entry points more
   than 60 m off: grounded 7/43, bare 16/43; parking spot within 60 m: bare 37/44, grounded 33/44.
Results and images are cached in memory (keyed by rounded coordinates).
"""
import asyncio
import base64
import json
import logging
import math
import re
import time
from typing import Optional

from services.http import client

from models import NearbyLot, ParkingCheck, ParkingSpot, Place
from services import llm, osm_access, parking_ground, parking_store

logger = logging.getLogger("truckbuddy.parking")

ESRI_EXPORT = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export"
HALF_SPAN_M = 150.0  # 300 m x 300 m view around the place
IMG_PX = 640
LLM_TIMEOUT_S = 35
GROUNDED_TIMEOUT_S = 32  # inside LLM_TIMEOUT_S: a slow grounded check falls back to the bare verdict
OSM_WAIT_S = 10  # Overpass often takes ~40 s; then the frame goes without driveway markers

SYSTEM = (
    "You are an expert at reading satellite imagery for US truck drivers. "
    "You judge if a semi-truck (tractor + 53 ft trailer, about 72 ft / 22 m long, 13 ft 6 in tall) "
    "can park at the place in the CENTER of the image. The image covers about 300 m x 300 m, north is up. "
    "Look for: a large paved lot with long open stretches, parked semi-trucks or trailers, "
    "wide entrances and room to turn around. Small car-only lots, tight drive-throughs or "
    "parking garages mean it does not fit. Also tell the driver HOW to get in and WHERE to leave the truck: "
    "the widest driveway a semi can use, and the best spot for the truck. "
    "Mark them with normalized image coordinates (x from 0 left to 1 right, y from 0 top to 1 bottom). "
    "Answer ONLY with JSON: "
    '{"fits": "likely" | "unlikely" | "unclear", "reason": "<max 15 words>", '
    '"entry": "<how to enter, max 14 words, use compass sides e.g. \'Use the wide east driveway off the frontage road\'>", '
    '"park": "<where to leave the truck, max 14 words, e.g. \'Park along the back lot on the north side\'>", '
    '"spots": [{"kind": "entry" | "park", "x": 0.0-1.0, "y": 0.0-1.0}], '
    '"nearby": null | {"direction": "north" | "northeast" | "east" | "southeast" | "south" | "southwest" | "west" | '
    '"northwest", "kind": "public lot" | "private yard" | "unclear"}} '
    "spots are ONLY at the center place itself. "
    "nearby: only when fits is \"unlikely\" and ANOTHER lot elsewhere in the image clearly has parked trailers or "
    "long open paved stretches a semi could use; give its compass direction from the center and whether it looks "
    "like a public lot or a fenced private yard (fences, gates, rows of company trailers). Otherwise null. "
    "Use empty strings and an empty list if you can't tell."
)

CACHE_MAX = 300
FAIL_TTL_S = 300  # don't re-hit the LLM for a failed check for 5 minutes

_img_cache: dict[str, bytes] = {}
_verdict_cache: dict[str, ParkingCheck] = {}
_fail_cache: dict[str, tuple[float, ParkingCheck]] = {}
_verdict_cache.update(list(parking_store.load_all().items())[-CACHE_MAX:])  # verdicts survive restarts


def _put(cache: dict, k: str, v) -> None:
    if len(cache) >= CACHE_MAX:
        cache.pop(next(iter(cache)))  # drop oldest
    cache[k] = v


LISTED_WORDS = ("truck parking", "53 ft parking", "hgv parking", "truck lot")


def _key(lat: float, lng: float) -> str:
    return f"{lat:.5f},{lng:.5f}"


def image_path(lat: float, lng: float) -> str:
    return f"/satellite?lat={lat:.5f}&lng={lng:.5f}"


async def fetch_satellite(lat: float, lng: float) -> bytes:
    k = _key(lat, lng)
    if k in _img_cache:
        return _img_cache[k]
    dlat, dlng = _spans(lat)
    params = {
        "bbox": f"{lng - dlng},{lat - dlat},{lng + dlng},{lat + dlat}",
        "bboxSR": 4326,
        "imageSR": 3857,
        "size": f"{IMG_PX},{IMG_PX}",
        "format": "jpg",
        "f": "image",
    }
    r = await client().get(ESRI_EXPORT, params=params, timeout=15)
    r.raise_for_status()
    if not r.headers.get("content-type", "").startswith("image/"):
        raise RuntimeError("satellite service returned no image")
    _put(_img_cache, k, r.content)
    return r.content


def _spans(lat: float) -> tuple[float, float]:
    dlat = HALF_SPAN_M / 111_320
    return dlat, dlat / max(0.2, math.cos(math.radians(lat)))


def _tip(v) -> Optional[str]:
    """Directions only; drop 'no entrance visible'-style non-answers."""
    t = str(v or "").strip()[:120]
    return None if not t or NEGATIVE_TIP.search(t) else t


def _parse(text: str, place: Place) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    data = json.loads(m.group(0)) if m else {}
    fits = data.get("fits")
    if fits not in ("likely", "unlikely", "unclear"):
        fits = "unclear"
    dlat, dlng = _spans(place.lat)
    spots = []
    for s in data.get("spots") or []:
        try:
            kind, x, y = s.get("kind"), float(s.get("x")), float(s.get("y"))
        except (TypeError, ValueError, AttributeError):
            continue
        if kind not in ("entry", "park") or not (0 <= x <= 1 and 0 <= y <= 1):
            continue
        # Small area: linear mapping from the image box to lat/lng is accurate enough.
        spots.append(ParkingSpot(kind=kind, x=x, y=y,
                                 lat=round(place.lat + dlat - 2 * dlat * y, 6),
                                 lng=round(place.lng - dlng + 2 * dlng * x, 6)))
    return {
        "fits": fits,
        "reason": str(data.get("reason") or "").strip()[:140],
        "entry_tip": _tip(data.get("entry")),
        "park_tip": _tip(data.get("park")),
        "spots": spots[:4],
        "nearby": _nearby(data.get("nearby")) if fits == "unlikely" else None,
    }


def _nearby(v) -> Optional[NearbyLot]:
    if not isinstance(v, dict):
        return None
    try:
        return NearbyLot(direction=str(v.get("direction") or "").strip().lower().replace("-", ""),
                         kind=v.get("kind") if v.get("kind") in ("public lot", "private yard") else "unclear")
    except ValueError:
        return None


async def _ask_vision(img: bytes, place: Place) -> dict:
    """Both checks at once: verdict and entry from the grounded one, the parking spot from the bare one; either
    alone if the other fails."""
    bare, grounded = await asyncio.gather(_ask_bare(img, place),
                                          asyncio.wait_for(_ask_grounded(place), GROUNDED_TIMEOUT_S),
                                          return_exceptions=True)
    if isinstance(grounded, BaseException):
        if isinstance(bare, BaseException):
            raise bare
        logger.warning("grounded check failed for %s: %r", place.name, grounded)
        return _without_entry(bare)
    if not grounded["markers"]:
        grounded = _without_entry(grounded)
    if isinstance(bare, BaseException):
        logger.warning("bare vision check failed for %s: %r", place.name, bare)
        return {**grounded, "nearby": None}
    return {**bare, "fits": grounded["fits"], "reason": grounded["reason"] or bare["reason"],
            "entry_tip": grounded["entry_tip"],
            "spots": [s for s in grounded["spots"] if s.kind == "entry"] + [s for s in bare["spots"] if s.kind == "park"],
            "nearby": bare["nearby"] if grounded["fits"] == "unlikely" else None}


def _without_entry(v: dict) -> dict:
    """An entry not taken from a map driveway was more than 60 m off in 16 of 43 places (eval 2026-11-02): no
    point, the route goes to the place itself; the one re-check (no entry) usually has the OSM markers by then."""
    return {**v, "entry_tip": None, "spots": [s for s in v["spots"] if s.kind != "entry"]}


async def _ask_grounded(place: Place) -> dict:
    access, img = await asyncio.gather(osm_access.around(place.lat, place.lng, OSM_WAIT_S),
                                       parking_ground.fetch_image(place.lat, place.lng))
    v = {**await parking_ground.check(place, access, img), "markers": len(access)}
    logger.info("grounded %s: %s, %d markers, samples %s", place.name, v["fits"], len(access),
                [(s["fits"], s["entries"]) for s in v["samples"]])
    return v


async def _ask_bare(img: bytes, place: Place) -> dict:
    # Image only: the old prompt, kept for its entry/park directions.
    out = await llm.complete(
        SYSTEM,
        "Judge ONLY from what is visible in this image. Can a 53 ft semi park at the center point? "
        "Where should it enter and where should it park?",
        "gpt-5.5",
        image_b64=base64.b64encode(img).decode(),
    )
    return _parse(out, place)


def is_town(place: Place) -> bool:
    """A whole town (HERE locality/area): never gets a parking verdict."""
    return place.id.startswith("here-town-")


def town_check() -> ParkingCheck:
    return ParkingCheck(verdict="unclear", label="No parking check for a town",
                        reason="Ask for a truck stop or give an address to check the parking.", source="none")


def cached_parking(place: Place) -> Optional[ParkingCheck]:
    """Satellite verdict if already computed (memory, then disk), without waiting."""
    if is_town(place):
        return town_check()
    k = _key(place.lat, place.lng) + (":listed" if listed_in_data(place) else "")
    return _from_cache(k)


def _from_cache(k: str) -> Optional[ParkingCheck]:
    hit = _verdict_cache.get(k)
    if hit is None:
        hit = parking_store.get(k)
        if hit is not None:
            _put(_verdict_cache, k, hit)
    return hit


async def _retry(place: Place, k: str) -> ParkingCheck:
    """The single allowed re-check of a verdict without an entry point; the result is final."""
    old = _from_cache(k)
    new = await _check_parking(place, force=True)
    keep = new if new.source != "none" and (parking_store.has_entry(new) or not parking_store.has_entry(old)) else old
    if keep is not None and keep.source != "none":
        _put(_verdict_cache, k, keep)
        parking_store.save(k, keep, retried=True)
    logger.info("parking re-check %s: entry %s -> %s", place.name, parking_store.has_entry(old), parking_store.has_entry(keep))
    return keep or new


async def retry_once(place: Place) -> Optional[ParkingCheck]:
    """Await the one re-check if this verdict still needs it (used by the startup warm-up)."""
    k = _key(place.lat, place.lng) + (":listed" if listed_in_data(place) else "")
    if not parking_store.needs_retry(k):
        return _from_cache(k)
    task = _pending.get(k + "#retry")
    if task is None:
        task = asyncio.create_task(_retry(place, k))
        _pending[k + "#retry"] = task
        task.add_done_callback(lambda _t: _pending.pop(k + "#retry", None))
    return await asyncio.shield(task)


def listed_in_data(place: Place) -> bool:
    return any(w in t.lower() for t in place.tags for w in LISTED_WORDS)


LISTED = ("listed", "Truck parking listed", "The place info lists truck parking.")


_pending: dict[str, "asyncio.Task[ParkingCheck]"] = {}
NEGATIVE_TIP = re.compile(
    r"^\s*(no|none|n/a|unclear|unknown|nothing)\b(?!\.\s*\d)|no suitable|not (clear|visible)|isn't (clear|visible)|"
    r"\bunclear\b|hard to tell|no visible|nothing suitable|can't (tell|see)|cannot (tell|see)|unable to",
    re.I,
)


async def check_parking(place: Place, approach=None) -> ParkingCheck:
    """Single-flight wrapper: concurrent checks of the same place share one vision call.
    With an approach (route set), the entry tip is rewritten relative to the driver."""
    if is_town(place):  # a satellite frame of a town center says nothing about truck parking
        parking_store.drop(_key(place.lat, place.lng))  # an old verdict saved for the town center
        _verdict_cache.pop(_key(place.lat, place.lng), None)
        return town_check()
    k = _key(place.lat, place.lng) + (":listed" if listed_in_data(place) else "")
    if approach is not None:
        from services import approach as approach_mod  # lazy: approach imports this module
        approach_mod.remember(place, approach)  # answers asked meanwhile use this approach too
    res = _from_cache(k)
    if res is not None:
        if parking_store.needs_retry(k) and k + "#retry" not in _pending:
            asyncio.ensure_future(retry_once(place))  # answer now from cache; one background re-check
    else:
        task = _pending.get(k)
        if task is None:
            task = asyncio.create_task(_check_parking(place))
            _pending[k] = task
            task.add_done_callback(lambda _t: _pending.pop(k, None))
        res = await asyncio.shield(task)
    if approach is None:
        return res
    from services import approach as approach_mod  # lazy: approach imports this module
    return await approach_mod.relative(place, res, approach)


async def _check_parking(place: Place, force: bool = False) -> ParkingCheck:
    """Always looks at the satellite image so the driver also gets entry/parking directions.
    force: the one re-check (ignores the cache; the caller decides what to keep)."""
    listed = listed_in_data(place)
    k = _key(place.lat, place.lng) + (":listed" if listed else "")
    if not force and (hit := _from_cache(k)) is not None:
        return hit
    failed = _fail_cache.get(k)
    if failed and time.monotonic() - failed[0] < FAIL_TTL_S:
        return failed[1]
    path = image_path(place.lat, place.lng)
    try:
        img = await fetch_satellite(place.lat, place.lng)
    except Exception as e:  # noqa: BLE001
        logger.warning("satellite fetch failed for %s: %s", place.name, e)
        if listed:
            return ParkingCheck(verdict=LISTED[0], label=LISTED[1], reason=LISTED[2], source="place_data")
        return ParkingCheck(verdict="unclear", label="Truck parking unclear",
                            reason="Satellite image unavailable.", source="none")
    try:
        v = await asyncio.wait_for(_ask_vision(img, place), LLM_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001
        logger.warning("vision check failed for %s: %s", place.name, e)
        if listed:
            res = ParkingCheck(verdict=LISTED[0], label=LISTED[1], reason=LISTED[2], source="place_data",
                               image_path=path)
        else:
            res = ParkingCheck(verdict="unclear", label="Truck parking unclear",
                               reason="Couldn't read the satellite image.", source="satellite", image_path=path)
        _put(_fail_cache, k, (time.monotonic(), res))
        return res
    if listed and v["fits"] != "unlikely":
        reason = v["reason"] if v["fits"] == "likely" and v["reason"] else LISTED[2]
        verdict, label, source = LISTED[0], LISTED[1], "place_data"
    elif listed:
        # Data says truck parking, imagery disagrees: don't promise it.
        verdict, label, reason, source = ("unclear", "Truck parking listed, not visible on satellite",
                                          v["reason"], "satellite")
    else:
        verdict, source, reason = v["fits"], "satellite", v["reason"]
        label = {
            "likely": "Likely fits a truck (satellite check)",
            "unlikely": "Unlikely to fit a truck (satellite check)",
            "unclear": "Truck parking unclear",
        }[verdict]
    nearby = v["nearby"] if verdict == "unlikely" else None
    if nearby:
        reason = f"{reason.rstrip('.')}; {nearby.phrase}." if reason else f"{nearby.phrase[0].upper()}{nearby.phrase[1:]}."
    res = ParkingCheck(verdict=verdict, label=label, reason=reason, source=source, image_path=path,
                       entry_tip=v["entry_tip"], park_tip=v["park_tip"], spots=v["spots"], nearby=nearby)
    if force:
        return res  # _retry() keeps the better of old/new and saves it as final
    _put(_verdict_cache, k, res)
    parking_store.save(k, res, retried=False)
    logger.info("parking %s -> %s (%s) entry=%r park=%r", place.name, verdict, reason, v["entry_tip"], v["park_tip"])
    return res
