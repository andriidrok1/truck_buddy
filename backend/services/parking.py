"""Truck parking check.

1. If the place data already lists truck parking -> verdict "listed".
2. Always fetch an Esri World Imagery satellite tile around the place and ask
   GPT 5.5 (vision) whether a 53 ft tractor-trailer can park there.
Results and images are cached in memory (keyed by rounded coordinates).
"""
import asyncio
import base64
import json
import logging
import math
import os
import re
import time
import uuid
from typing import Optional

import httpx
from emergentintegrations.llm.chat import ImageContent, LlmChat, StreamDone, TextDelta, UserMessage

from models import ParkingCheck, ParkingSpot, Place

logger = logging.getLogger("truckbuddy.parking")

ESRI_EXPORT = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export"
HALF_SPAN_M = 150.0  # 300 m x 300 m view around the place
IMG_PX = 640
LLM_TIMEOUT_S = 35

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
    '"spots": [{"kind": "entry" | "park", "x": 0.0-1.0, "y": 0.0-1.0}]} '
    "Use empty strings and an empty list if you can't tell."
)

CACHE_MAX = 300
FAIL_TTL_S = 300  # don't re-hit the LLM for a failed check for 5 minutes

_img_cache: dict[str, bytes] = {}
_verdict_cache: dict[str, ParkingCheck] = {}
_fail_cache: dict[str, tuple[float, ParkingCheck]] = {}


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
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(ESRI_EXPORT, params=params)
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
    }


async def _ask_vision(img: bytes, place: Place) -> dict:
    chat = LlmChat(
        api_key=os.environ["EMERGENT_LLM_KEY"],
        session_id=f"parking-{uuid.uuid4()}",
        system_message=SYSTEM,
    ).with_model("openai", "gpt-5.5")
    # Image only: the place name/type must not bias the verdict.
    msg = UserMessage(
        text="Judge ONLY from what is visible in this image. Can a 53 ft semi park at the center point? "
             "Where should it enter and where should it park?",
        file_contents=[ImageContent(image_base64=base64.b64encode(img).decode())],
    )
    out = ""
    async for ev in chat.stream_message(msg):
        if isinstance(ev, TextDelta):
            out += ev.content
        elif isinstance(ev, StreamDone):
            if not out and ev.content:
                out = ev.content
            break
    return _parse(out, place)


def cached_parking(place: Place) -> Optional[ParkingCheck]:
    """Satellite verdict if already computed, without waiting."""
    k = _key(place.lat, place.lng) + (":listed" if listed_in_data(place) else "")
    return _verdict_cache.get(k)


def listed_in_data(place: Place) -> bool:
    return any(w in t.lower() for t in place.tags for w in LISTED_WORDS)


LISTED = ("listed", "Truck parking listed", "The place info lists truck parking.")


_pending: dict[str, "asyncio.Task[ParkingCheck]"] = {}
NEGATIVE_TIP = re.compile(
    r"^\s*(no|none|n/a|unclear|unknown|nothing)\b(?!\.\s*\d)|no suitable|not (clear|visible)|isn't (clear|visible)|"
    r"\bunclear\b|hard to tell|no visible|nothing suitable|can't (tell|see)|cannot (tell|see)|unable to",
    re.I,
)


async def check_parking(place: Place) -> ParkingCheck:
    """Single-flight wrapper: concurrent checks of the same place share one vision call."""
    k = _key(place.lat, place.lng) + (":listed" if listed_in_data(place) else "")
    if k in _verdict_cache:
        return _verdict_cache[k]
    task = _pending.get(k)
    if task is None:
        task = asyncio.create_task(_check_parking(place))
        _pending[k] = task
        task.add_done_callback(lambda _t: _pending.pop(k, None))
    return await asyncio.shield(task)


async def _check_parking(place: Place) -> ParkingCheck:
    """Always looks at the satellite image so the driver also gets entry/parking directions."""
    listed = listed_in_data(place)
    k = _key(place.lat, place.lng) + (":listed" if listed else "")
    if k in _verdict_cache:
        return _verdict_cache[k]
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
    res = ParkingCheck(verdict=verdict, label=label, reason=reason, source=source, image_path=path,
                       entry_tip=v["entry_tip"], park_tip=v["park_tip"], spots=v["spots"])
    _put(_verdict_cache, k, res)
    logger.info("parking %s -> %s (%s) entry=%r park=%r", place.name, verdict, reason, v["entry_tip"], v["park_tip"])
    return res
