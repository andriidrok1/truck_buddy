"""Manual probe: does the place name bias the GPT vision parking verdict? (not a pytest test)"""
import asyncio
import sys

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/app/backend/.env")
from models import Place  # noqa: E402
from services import parking  # noqa: E402


async def main():
    img = await parking.fetch_satellite(38.548, -121.412)
    base = dict(lat=38.548, lng=-121.412, id="x", tags=[])
    for name, cat in [("Sierra Fuel & Truck Stop", "truck_stop"), ("Place", "hotel"), ("Sunny Coffee Cafe", "hotel")]:
        try:
            p = Place.model_construct(name=name, category=cat, **base)
            print(name, "->", await parking._ask_vision(img, p))
        except Exception as e:  # noqa: BLE001
            print(name, "ERR", e)


asyncio.run(main())
