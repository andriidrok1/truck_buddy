"""For demo places without a satellite entry point: move to the center of the largest OSM parking lot nearby."""
import asyncio
import math
import sys



sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")

from models import Place  # noqa: E402
from services import parking  # noqa: E402
from services.places import MOCK_PLACES  # noqa: E402

IDS = sys.argv[1].split(",")
OVERPASS = "https://overpass-api.de/api/interpreter"


def area(geom):
    if len(geom) < 3:
        return 0
    lat0 = geom[0]["lat"]
    pts = [((g["lon"]) * 111320 * math.cos(math.radians(lat0)), g["lat"] * 111320) for g in geom]
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))) / 2


def lots(lat, lng, r=80):
    """Overpass is unreachable here: candidate lot centers = grid offsets ~r m around the point."""
    dlat = r / 111320
    dlng = r / (111320 * math.cos(math.radians(lat)))
    offs = [(0, 0), (dlat, 0), (-dlat, 0), (0, dlng), (0, -dlng), (dlat, dlng), (-dlat, -dlng), (dlat, -dlng), (-dlat, dlng)]
    return [(0, (lat + a, lng + b), {}) for a, b in offs]


async def one(raw):
    cands = lots(raw["lat"], raw["lng"])
    for a, (lat, lng), _t in cands[1:]:
        p = Place(**{**raw, "lat": round(lat, 5), "lng": round(lng, 5)})
        c = await parking.check_parking(p)
        e = next((s for s in c.spots if s.kind == "entry"), None)
        if e:
            return f"{raw['id']}: {p.lat},{p.lng} -> {c.verdict} E={e.lat:.5f},{e.lng:.5f} {c.entry_tip!r}"
    return f"{raw['id']}: no E found"


async def main2():
    for line in await asyncio.gather(*(one(r) for r in MOCK_PLACES if r["id"] in IDS)):
        print(line)


async def main():
    for raw in MOCK_PLACES:
        if raw["id"] not in IDS:
            continue
        cands = lots(raw["lat"], raw["lng"])
        print(f"## {raw['name']}: {len(cands)} lots", [(round(a), (round(c[0], 5), round(c[1], 5)), t.get("hgv")) for a, c, t in cands[:3]])
        for a, (lat, lng), _t in cands[1:]:
            p = Place(**{**raw, "lat": round(lat, 5), "lng": round(lng, 5)})
            c = await parking.check_parking(p)
            e = next((s for s in c.spots if s.kind == "entry"), None)
            print(f"   try {p.lat},{p.lng} area={round(a)} -> {c.verdict} E={'%.5f,%.5f' % (e.lat, e.lng) if e else '-'} {c.entry_tip!r}")
            if e:
                break


asyncio.run(main2())
