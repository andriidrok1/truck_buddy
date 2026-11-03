"""Cache the OSM road data around every eval place (Overpass is slow; the run must be repeatable)."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services import osm_access  # noqa: E402

HERE = Path(__file__).resolve().parent


async def main() -> None:
    out = HERE / "osm"
    out.mkdir(exist_ok=True)
    for p in json.loads((HERE / "places.json").read_text()):
        f = out / f"{p['id']}.json"
        if f.exists():
            continue
        data = await osm_access.fetch(p["lat"], p["lng"], timeout=90)
        if data is None:
            print("FAILED", p["id"])
            continue
        f.write_text(json.dumps(data))
        print(p["id"], len(osm_access.points(data, p["lat"], p["lng"])), "points")


asyncio.run(main())
