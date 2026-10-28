"""Warm the satellite cache for all demo places at startup (PARKING_WARM=1; off by default: up to ~23 vision calls per cold start)."""
import asyncio
import logging
import os

from fastapi import APIRouter

from models import Place
from services import parking, parking_store

logger = logging.getLogger("truckbuddy.parking_warm")

CONCURRENCY = 2  # stay well under the vision/imagery rate limits
_task = None


async def warm_demo_places() -> dict:
    """check_parking for every demo place not on disk yet; places without E get their one re-check."""
    from services.places import MOCK_PLACES
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(raw: dict):
        p = Place(**raw)
        if parking.is_town(p):  # towns never get a parking check
            return
        async with sem:
            try:
                if parking.cached_parking(p) is None:
                    await parking.check_parking(p)
                await parking.retry_once(p)
            except Exception as e:  # noqa: BLE001 - warm-up must never crash the server
                logger.warning("warm-up failed for %s: %s", p.name, e)

    await asyncio.gather(*(one(r) for r in MOCK_PLACES))
    s = parking_store.stats()
    logger.info("parking warm-up done: %s", s)
    return s


async def _start():
    global _task
    if os.environ.get("PARKING_WARM", "0") == "1":
        _task = asyncio.create_task(warm_demo_places())


router = APIRouter(on_startup=[_start])
