"""Trip events over HTTP: the app posts batches of what happened on a drive; the driver reads their own trips;
a tester reads the latest events across drivers (services/trip_events.py)."""
import asyncio
import logging
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field

from services import auth, trip_events

logger = logging.getLogger("truckbuddy.trips")

router = APIRouter(prefix="/api/trips", dependencies=[Depends(auth.rate_limited)])
WAIT_S = 8.0
UNAVAILABLE = "Trip log is unavailable right now"


class TripEventIn(BaseModel):
    t: Optional[Any] = None  # ISO 8601 or epoch; missing = now
    trip_id: Optional[str] = Field(None, max_length=trip_events.MAX_TRIP_ID)
    kind: str = Field(pattern=trip_events.KIND_RE.pattern)
    data: dict = Field(default_factory=dict)


async def _guarded(coro):
    try:
        return await asyncio.wait_for(coro, WAIT_S)
    except Exception as e:  # noqa: BLE001 - store off, down or slow: the app keeps its batch and retries later
        logger.warning("trip log failed: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=503, detail=UNAVAILABLE)


@router.post("/events")
async def post_events(events: Annotated[list[TripEventIn], Field(max_length=trip_events.MAX_BATCH)],
                      driver: str = Depends(auth.require_driver)):
    """A batch from the app; all or nothing (a bad event is a 422 for the batch, so the app drops it)."""
    try:
        rows = [e.model_dump() for e in events]
        trip_events.normalize(driver, rows, "app")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    stored = await _guarded(trip_events.add(driver, rows, "app"))
    return {"stored": stored}


@router.get("")
async def list_trips(driver: str = Depends(auth.require_driver), limit: int = Query(20, ge=1, le=100)):
    return {"trips": await _guarded(trip_events.trips(driver, limit))}


@router.get("/recent")
async def recent_events(tester: str = Depends(auth.require_tester), limit: int = Query(200, ge=1, le=1000)):
    """Latest events across drivers (pseudonymous ids), for the tester screen and the stats script."""
    return {"events": await _guarded(trip_events.recent(limit))}


@router.get("/{trip_id}/events")
async def trip_events_list(trip_id: str = Path(max_length=trip_events.MAX_TRIP_ID),
                           driver: str = Depends(auth.require_driver), limit: int = Query(1000, ge=1, le=5000)):
    return {"events": await _guarded(trip_events.for_trip(driver, trip_id, limit))}
