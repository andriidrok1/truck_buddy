"""5 real truck stops: compass Entry (warm-up) vs relative Entry (after a real HERE route). Run in-process."""
import asyncio
import sys

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")

from models import Place  # noqa: E402
from services import approach  # noqa: E402
from services.geocode import geocode  # noqa: E402
from services.parking import check_parking  # noqa: E402
from services.routing import get_routing_provider  # noqa: E402

FROM = (38.5816, -121.4944)  # downtown Sacramento
QUERIES = [
    "Pilot Travel Center, 4100 Pedrick Rd, Dixon, CA",
    "Flying J Travel Center, 15100 Thornton Rd, Lodi, CA",
    "Love's Travel Stop, 1601 Yosemite Blvd, Modesto, CA",
    "TA Travel Center, 3150 Red Bluff... Corning",
    "Petro Stopping Center, 3861 Tumbleweed Ave, Corning, CA",
    "Pilot Travel Center, 3740 Ramos Rd, Williams, CA",
]


async def one(q):
    m = await geocode(q, *FROM)
    if not m:
        return None
    g = m[0]
    p = Place(id=g.id, name=q.split(",")[0] + " " + q.split(",")[-2].strip(), category="truck_stop", lat=g.lat, lng=g.lng)
    base = await check_parking(p)  # warm-up: compass directions
    route = await get_routing_provider().route(*FROM, *approach.target(p), None)
    rel = await check_parking(p, route.approach)
    return p, base, route.approach, rel


async def main():
    rows = [r for r in await asyncio.gather(*(one(q) for q in QUERIES)) if r][:5]
    print("| # | Место | Вердикт | Подъезд | Entry до (стороны света) | Entry после (относительно водителя) |")
    print("|---|---|---|---|---|---|")
    for i, (p, base, ap, rel) in enumerate(rows, 1):
        a = f"с {ap.compass}, {ap.bearing}°, {ap.street or '—'}" if ap else "—"
        print(f"| {i} | {p.name} | {base.verdict} | {a} | {base.entry_tip or '—'} | {rel.entry_tip if rel.relative else '— (не переписано)'} |")


asyncio.run(main())
