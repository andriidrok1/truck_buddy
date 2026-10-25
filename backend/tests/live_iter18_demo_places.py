"""Demo truck stops: satellite verdict + entry point E for each (in-process)."""
import asyncio
import sys

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")

from models import Place  # noqa: E402
from services.parking import check_parking  # noqa: E402
from services.places import MOCK_PLACES  # noqa: E402


async def one(raw):
    p = Place(**raw)
    c = await check_parking(p)
    e = next((s for s in c.spots if s.kind == "entry"), None)
    k = next((s for s in c.spots if s.kind == "park"), None)
    return p, c, e, k


async def main():
    stops = list(MOCK_PLACES)
    for p, c, e, k in await asyncio.gather(*(one(r) for r in stops)):
        print(f"| {p.category} | {p.name} | {p.address} | {c.verdict} | E={'%.5f,%.5f' % (e.lat, e.lng) if e else '-'} | "
              f"P={'%.5f,%.5f' % (k.lat, k.lng) if k else '-'} | entry={c.entry_tip!r}")


asyncio.run(main())
