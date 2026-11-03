"""Grounded satellite check: the vision model picks, the map supplies the coordinates.

The old check asked the model for x/y of the entrance on a bare image; the points landed on lawns, roofs and
the neighbour's lot (eval 2026-11-02). Here:
- the frame is 400 m (big truck stops do not fit in 300 m) with the place ringed and a 25 m grid;
- OSM driveway junctions (services.osm_access) are drawn as numbered markers and the model names numbers;
- the parking spot is a grid cell; compass words and the street name are computed from the chosen point;
- two independent samples; where they disagree the answer is "unclear" / no point.
"""
import asyncio
import base64
import io
import json
import logging
import math
import re
from typing import Optional

from PIL import Image, ImageDraw

from models import ParkingSpot, Place
from services import llm
from services.http import client
from services.osm_access import Access

logger = logging.getLogger("truckbuddy.parking_ground")

ESRI_EXPORT = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export"
HALF_M = 200.0
PX = 800
CELL_PX = 50  # 16 x 16 cells of 25 m
COLS = "ABCDEFGHIJKLMNOP"
MODEL = "gpt-5.5"
EFFORT = "low"  # picking among numbered markers needs little reasoning; default effort cost 4x and 40 s
SAMPLES = 2
AGREE_CELLS = 2  # park cells further apart than this (50 m) = the samples disagree
COMPASS = ["north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"]

SYSTEM = (
    "You read satellite images for US truck drivers. The image is 400 m x 400 m, north up, with a yellow grid of "
    "25 m cells: columns A-P from west to east, rows 1-16 from north to south. A magenta ring marks the place the "
    "driver asked about. Numbered markers sit where a driveway or service road meets a public road (from map "
    "data); some lead to other businesses.\n"
    "Decide if a tractor with a 53 ft trailer (22 m long) can get in and park at THE RINGED PLACE: a large paved "
    "or gravel lot with long open stretches, parked semi-trucks or trailers, wide driveways and room to turn. "
    "Car-only lots, drive-throughs, parking garages, or a ring on a highway or empty land mean it does not fit. "
    "Judge only what you see; the name only tells you which business to look at.\n"
    "Answer ONLY with JSON: {\"fits\": \"likely\" | \"unlikely\" | \"unclear\", \"reason\": \"<max 15 words>\", "
    "\"entries\": [numbers of markers a semi would use to drive into the ringed place's lot, best first; [] if "
    "no marker sits at a driveway into that lot (markers at other businesses do not count)], "
    "\"entry_cell\": null | \"<cell of the truck driveway, only when no numbered marker is one>\", "
    "\"park_cell\": null | \"<cell where the truck should stand>\", \"entry_landmark\": \"<max 10 words, what "
    "the driver sees at the driveway, e.g. 'past the fuel canopy'; never north/south/east/west/left/right; '' if "
    "nothing>\", \"park_note\": \"<max 12 words, e.g. 'the trailer rows behind the store'; no compass words>\"}"
)


def spans(lat: float) -> tuple[float, float]:
    dlat = HALF_M / 111320
    return dlat, dlat / max(0.2, math.cos(math.radians(lat)))


def to_px(lat0: float, lng0: float, lat: float, lng: float) -> tuple[float, float]:
    dlat, dlng = spans(lat0)
    return (lng - lng0 + dlng) / (2 * dlng) * PX, (lat0 + dlat - lat) / (2 * dlat) * PX


def to_ll(lat0: float, lng0: float, x: float, y: float) -> tuple[float, float]:
    dlat, dlng = spans(lat0)
    return round(lat0 + dlat - 2 * dlat * y / PX, 6), round(lng0 - dlng + 2 * dlng * x / PX, 6)


async def fetch_image(lat: float, lng: float) -> bytes:
    dlat, dlng = spans(lat)
    r = await client().get(ESRI_EXPORT, params={
        "bbox": f"{lng - dlng},{lat - dlat},{lng + dlng},{lat + dlat}", "bboxSR": 4326, "imageSR": 3857,
        "size": f"{PX},{PX}", "format": "jpg", "f": "image"}, timeout=20)
    r.raise_for_status()
    if not r.headers.get("content-type", "").startswith("image/"):
        raise RuntimeError("satellite service returned no image")
    return r.content


def render(img: bytes, place: Place, access: list[Access]) -> bytes:
    im = Image.open(io.BytesIO(img)).convert("RGB")
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for k in range(0, PX + 1, CELL_PX):
        d.line((k, 0, k, PX), fill=(255, 230, 0, 90), width=1)
        d.line((0, k, PX, k), fill=(255, 230, 0, 90), width=1)
    for k in range(PX // CELL_PX):
        d.text((k * CELL_PX + 20, 2), COLS[k], fill=(255, 230, 0, 255))
        d.text((3, k * CELL_PX + 18), str(k + 1), fill=(255, 230, 0, 255))
    c = PX / 2
    d.ellipse((c - 14, c - 14, c + 14, c + 14), outline=(255, 0, 255, 255), width=4)
    for i, a in enumerate(access, 1):
        x, y = to_px(place.lat, place.lng, a.lat, a.lng)
        d.ellipse((x - 11, y - 11, x + 11, y + 11), fill=(0, 0, 0, 230), outline=(255, 255, 255, 255), width=2)
        d.text((x - (3 if i < 10 else 7), y - 6), str(i), fill=(255, 230, 0, 255))
    out = io.BytesIO()
    Image.alpha_composite(im.convert("RGBA"), layer).convert("RGB").save(out, "JPEG", quality=88)
    return out.getvalue()


_CELL = re.compile(r"^\s*([A-Pa-p])\s*(1[0-6]|[1-9])\s*$")


def cell_center(cell) -> Optional[tuple[float, float]]:
    m = _CELL.match(str(cell or ""))
    if not m:
        return None
    return (COLS.index(m.group(1).upper()) + 0.5) * CELL_PX, (int(m.group(2)) - 0.5) * CELL_PX


def _parse(text: str, n_access: int) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except ValueError:
        data = {}
    fits = data.get("fits") if data.get("fits") in ("likely", "unlikely", "unclear") else "unclear"
    entries = []
    for v in data.get("entries") or []:
        try:
            i = int(v)
        except (TypeError, ValueError):
            continue
        if 1 <= i <= n_access and i not in entries:
            entries.append(i)
    return {"fits": fits, "reason": str(data.get("reason") or "").strip()[:140], "entries": entries,
            "entry_cell": cell_center(data.get("entry_cell")), "park_cell": cell_center(data.get("park_cell")),
            "landmark": str(data.get("entry_landmark") or "").strip()[:80],
            "park_note": str(data.get("park_note") or "").strip()[:100]}


def _near(a: Optional[tuple], b: Optional[tuple]) -> bool:
    return a is not None and b is not None and math.dist(a, b) <= AGREE_CELLS * CELL_PX


def merge(samples: list[dict]) -> dict:
    """Agreement of independent samples; disagreement means "unclear" / no point, never a coin flip.
    Entry: the marker every sample lists, ranked best by the sum of its positions (the samples often agree on
    the set and differ only on the order: 49er, eval 2026-11-02)."""
    first = samples[0]
    if len(samples) == 1:
        return dict(first)
    fits = first["fits"] if all(s["fits"] == first["fits"] for s in samples) else "unclear"
    entry, entry_px = None, None
    common = set(first["entries"]).intersection(*(s["entries"] for s in samples[1:]))
    if common:
        entry = min(common, key=lambda i: (sum(s["entries"].index(i) for s in samples), i))
    elif all(not s["entries"] for s in samples) and all(_near(first["entry_cell"], s["entry_cell"]) for s in samples):
        entry_px = first["entry_cell"]
    park = first["park_cell"] if all(_near(first["park_cell"], s["park_cell"]) for s in samples) else None
    return {**first, "fits": fits, "entries": [entry] if entry else [], "entry_cell": entry_px, "park_cell": park}


def compass(from_ll: tuple[float, float], to_ll_: tuple[float, float]) -> str:
    dy = to_ll_[0] - from_ll[0]
    dx = (to_ll_[1] - from_ll[1]) * math.cos(math.radians(from_ll[0]))
    return COMPASS[round(math.degrees(math.atan2(dx, dy)) % 360 / 45) % 8]


async def _sample(img_b64: str, place: Place) -> tuple[str, dict]:
    res = await llm.complete_full(SYSTEM, f"Ringed place: {place.name} ({place.category.replace('_', ' ')}).",
                                  MODEL, image_b64=img_b64, reasoning_effort=EFFORT)
    return res.text, res.usage


async def check(place: Place, access: list[Access], img: bytes, samples: int = SAMPLES) -> dict:
    """-> fits, reason, entry_tip, park_tip, spots (lat/lng from the map or the grid), usage, raw samples."""
    b64 = base64.b64encode(render(img, place, access)).decode()
    outs = await asyncio.gather(*(_sample(b64, place) for _ in range(samples)))
    parsed = [_parse(t, len(access)) for t, _ in outs]
    usage = {k: sum(u.get(k, 0) for _, u in outs) for k in ("prompt_tokens", "completion_tokens")}
    return {**build(place, access, parsed), "usage": usage, "samples": parsed}


def build(place: Place, access: list[Access], parsed: list[dict]) -> dict:
    """Merged samples -> verdict, tips and spots with coordinates from the map (or the grid)."""
    v = merge(parsed)
    spots: list[ParkingSpot] = []
    entry_tip = park_tip = None
    park_ll = to_ll(place.lat, place.lng, *v["park_cell"]) if v["park_cell"] else None
    if v["entries"]:
        a = access[v["entries"][0] - 1]
        x, y = to_px(place.lat, place.lng, a.lat, a.lng)
        spots.append(ParkingSpot(kind="entry", x=x / PX, y=y / PX, lat=round(a.lat, 6), lng=round(a.lng, 6)))
        side = compass(park_ll or (place.lat, place.lng), (a.lat, a.lng))
        what = "ramp" if a.ramp else "driveway"
        entry_tip = f"Use the {side} {what}" + (f" off {a.road}" if a.road else "")
    elif v["entry_cell"]:
        e_lat, e_lng = to_ll(place.lat, place.lng, *v["entry_cell"])
        spots.append(ParkingSpot(kind="entry", x=v["entry_cell"][0] / PX, y=v["entry_cell"][1] / PX,
                                 lat=e_lat, lng=e_lng))
        entry_tip = f"Use the {compass(park_ll or (place.lat, place.lng), (e_lat, e_lng))} driveway"
    if entry_tip and v["landmark"]:
        entry_tip += f", {v['landmark']}"
    if park_ll:
        spots.append(ParkingSpot(kind="park", x=v["park_cell"][0] / PX, y=v["park_cell"][1] / PX,
                                 lat=park_ll[0], lng=park_ll[1]))
        park_tip = f"Park in {v['park_note']}" if v["park_note"] else None
    return {"fits": v["fits"], "reason": v["reason"], "entry_tip": entry_tip, "park_tip": park_tip,
            "spots": spots}
