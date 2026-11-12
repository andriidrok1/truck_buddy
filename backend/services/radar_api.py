"""Radar for the map's weather view (services/radar.py).

GET /api/radar/frame (bearer token): the latest live frame, the forecast steps and the tile URL template with a
signed key. GET /api/radar/tiles/{layer}/{z}/{x}/{y}.png?k=...: the tile itself; no bearer header (the map's
tile layer can't send one), the signed key stands in for it and has its own per-driver budget.
"""
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from services import auth, radar

router = APIRouter(prefix="/api")  # included with auth.rate_limited
tiles = APIRouter(prefix="/api")  # public: the key in the query is the credential

TILES_PER_MIN = 600  # one map view is ~20-40 tiles; panning and the forecast steps add up


@router.get("/radar/frame")
async def radar_frame(driver: auth.Principal = Depends(auth.require_driver)):
    if not radar.enabled():
        return {"enabled": False}
    ts = await radar.latest_frame()
    if not ts:
        return {"enabled": True, "live": None, "forecast": [], "tile_url": None}
    return {
        "enabled": True,
        "time": radar.frame_time(ts).isoformat(),
        "live": f"n0q-{ts}",
        "forecast": [{"minutes": m, "layer": f"hrrr-f{m:03d}"} for m in radar.FORECAST_MIN],
        "max_zoom": radar.MAX_Z,
        # The app fills {layer}; {z}/{x}/{y} are left for the map's tile layer.
        "tile_url": "/api/radar/tiles/{layer}/{z}/{x}/{y}.png?k=" + radar.key_for(str(driver)),
    }


@tiles.get("/radar/tiles/{layer}/{z}/{x}/{y}.png")
async def radar_tile(layer: str, z: int, x: int, y: int, k: str = Query(..., max_length=300)):
    sub = radar.driver_of(k)
    if not sub:
        raise HTTPException(status_code=401, detail="Bad radar key")
    if not radar.enabled():
        raise HTTPException(status_code=404, detail="Radar is off")
    if not auth.take_token("radar:" + sub, time.monotonic(), auth.per_worker(TILES_PER_MIN)):
        raise HTTPException(status_code=429, detail="Slow down a little.")
    try:
        png = await radar.fetch_tile(layer, z, x, y)
    except ValueError:
        raise HTTPException(status_code=404, detail="No such radar tile")
    except radar.TileUnavailable:
        raise HTTPException(status_code=502, detail="Radar unavailable")
    # A timestamped live frame never changes; a forecast step changes with each hourly HRRR run.
    max_age = 900 if layer.startswith("hrrr") else 3600
    return Response(content=png, media_type="image/png", headers={"Cache-Control": f"public, max-age={max_age}"})
