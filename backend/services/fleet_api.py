"""Fleet dashboard reads over HTTP (read-only, plus the two settings a fleet owns: its Motive key and which
Motive user each driver is).

Every route needs an owner or dispatcher of an organisation (auth.require_org_member); a driver's token, a
solo driver's included, gets 403. Every query is filtered by the caller's org_id, so a truck id from another
fleet is a 404 and another fleet's events never show up. Transcripts stay on the server: whatever a driver
said (`text`, `transcript` in an event's data) is removed here, on the way out (decision 2026-10-30: the
owner sees what happened, not what was said). The tester route /api/trips/recent is unchanged.

Days are UTC calendar days (?day=YYYY-MM-DD, default today).
"""
import asyncio
import logging
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field

from services import auth, secrets, tenancy, trip_events

logger = logging.getLogger("truckbuddy.fleet")

router = APIRouter(prefix="/api/fleet", dependencies=[Depends(auth.rate_limited)])
reader = auth.require_org_member("owner", "dispatcher")
owner = auth.require_org_member("owner")
WAIT_S = 8.0
UNAVAILABLE = "Fleet data is unavailable right now"
TRANSCRIPT_KEYS = frozenset({"text", "transcript"})
DAY_RE = r"^\d{4}-\d{2}-\d{2}$"

# What the summary counts, per truck. reroute_requested is the server's record of every /reroute call (the app's
# own "reroute" event describes the same reroute, so it is not counted twice).
FAILED_TURN_KINDS = ("not_understood", "turn_error", "turn_failed")
SUMMARY_KINDS = ("reroute_requested", "stop_guard", "route_started", "route_failed", *FAILED_TURN_KINDS)


def public_event(e: dict) -> dict:
    """An event as a fleet may see it: no transcript."""
    return {**e, "data": {k: v for k, v in (e.get("data") or {}).items() if k not in TRANSCRIPT_KEYS}}


def _day(day: Optional[str]) -> tuple[datetime, datetime, str]:
    try:
        d = date.fromisoformat(day) if day else datetime.now(timezone.utc).date()
    except ValueError:
        raise HTTPException(status_code=422, detail="day is YYYY-MM-DD")
    start = datetime.combine(d, dtime.min, tzinfo=timezone.utc)
    return start, start + timedelta(days=1), d.isoformat()


async def _guarded(coro):
    try:
        return await asyncio.wait_for(coro, WAIT_S)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 - store off, down or slow
        logger.warning("fleet read failed: %s: %s", type(e).__name__, e)
        raise HTTPException(status_code=503, detail=UNAVAILABLE)


def _status(route: Optional[dict]) -> str:
    if route is None:
        return "no_data"
    return "on_route" if route["kind"] == "route_started" else "idle"


@router.get("/vehicles")
async def vehicles(principal: auth.Principal = Depends(reader)):
    """The org's trucks with where each was last seen (the last event that carried a position) and whether a
    route is running (the last route_started vs route_ended)."""
    trucks = await _guarded(tenancy.org_vehicles(principal.org_id))
    latest = await _guarded(trip_events.org_latest(principal.org_id))
    out = []
    for v in trucks:
        got = latest.get(v["id"]) or {}
        pos, last = got.get("position"), got.get("last")
        out.append({"id": v["id"], "label": v["label"], "devices": len(v["devices"]), "status": _status(got.get("route")),
                    "last_seen": last["t"] if last else None, "last_kind": last["kind"] if last else None,
                    "position": {"lat": pos["lat"], "lng": pos["lng"], "t": pos["t"]} if pos else None})
    return {"vehicles": out}


@router.get("/vehicles/{vehicle_id}/events")
async def vehicle_events(vehicle_id: str = Path(max_length=64), day: Optional[str] = Query(None, pattern=DAY_RE),
                         limit: int = Query(2000, ge=1, le=5000), principal: auth.Principal = Depends(reader)):
    """One truck's day, oldest first, without transcripts; 404 for a truck that is not the caller's."""
    start, end, label = _day(day)
    trucks = await _guarded(tenancy.org_vehicles(principal.org_id))
    if vehicle_id not in {v["id"] for v in trucks}:
        raise HTTPException(status_code=404, detail="No such vehicle")
    events = await _guarded(trip_events.vehicle_day(principal.org_id, vehicle_id, start, end, limit))
    return {"day": label, "vehicle_id": vehicle_id, "events": [public_event(e) for e in events]}


SUMMARY_COUNTS = ("routes", "reroutes", "guard_questions", "failed_turns", "route_failures")


def _zero_row(vehicle_id: Optional[str], label: Optional[str]) -> dict:
    return {"vehicle_id": vehicle_id, "label": label, **dict.fromkeys(SUMMARY_COUNTS, 0)}


@router.get("/summary")
async def summary(day: Optional[str] = Query(None, pattern=DAY_RE), principal: auth.Principal = Depends(reader)):
    """Per truck for the day: routes started, reroutes, far-stop questions the guard asked, failed copilot turns,
    routing failures. Events from a phone not bound to a truck are counted under vehicle_id null."""
    start, end, label = _day(day)
    trucks = await _guarded(tenancy.org_vehicles(principal.org_id))
    events = await _guarded(trip_events.org_day(principal.org_id, start, end, SUMMARY_KINDS))
    labels = {v["id"]: v["label"] for v in trucks}
    rows: dict[Optional[str], dict] = {vid: _zero_row(vid, name) for vid, name in labels.items()}
    for e in events:
        r = rows.setdefault(e["vehicle_id"], _zero_row(e["vehicle_id"], labels.get(e["vehicle_id"])))
        k = e["kind"]
        if k == "route_started":
            r["routes"] += 1
        elif k == "reroute_requested":
            r["reroutes"] += 1
        elif k == "stop_guard" and e["data"].get("result") == "asked":
            r["guard_questions"] += 1
        elif k in FAILED_TURN_KINDS:
            r["failed_turns"] += 1
        elif k == "route_failed":
            r["route_failures"] += 1
    totals = {k: sum(r[k] for r in rows.values()) for k in SUMMARY_COUNTS}
    ordered = sorted(rows.values(), key=lambda r: (r["vehicle_id"] is None, r["label"] or "", r["vehicle_id"] or ""))
    return {"day": label, "vehicles": ordered, "totals": totals}


@router.get("/drivers")
async def drivers(principal: auth.Principal = Depends(reader)):
    """The org's drivers (ids to map to Motive users; no device tokens)."""
    return {"drivers": await _guarded(tenancy.org_drivers(principal.org_id))}


class MotiveUserIn(BaseModel):
    user_id: Optional[str] = Field(None, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")


@router.put("/drivers/{driver_id}/motive")
async def driver_motive(body: MotiveUserIn, driver_id: str = Path(max_length=64), principal: auth.Principal = Depends(reader)):
    """Which Motive user this driver is, so the fleet key fetches their clocks (null unlinks)."""
    if not await _guarded(tenancy.set_motive_user(principal.org_id, driver_id, body.user_id)):
        raise HTTPException(status_code=404, detail="No such driver")
    return {"driver_id": driver_id, "motive_linked": body.user_id is not None}


class MotiveKeyIn(BaseModel):
    key: str = Field(min_length=8, max_length=200)


@router.put("/credentials/motive")
async def motive_key(body: MotiveKeyIn, principal: auth.Principal = Depends(owner)):
    """The fleet's Motive API key, stored encrypted (services/secrets.py); owner only, never read back."""
    try:
        await asyncio.wait_for(tenancy.credential_put(principal.org_id, "motive", body.key.strip()), WAIT_S)
    except secrets.NoCredentialsKey:
        raise HTTPException(status_code=503, detail="Credential storage is not configured")
    except Exception as e:  # noqa: BLE001
        logger.warning("motive key store failed: %s", type(e).__name__)
        raise HTTPException(status_code=503, detail=UNAVAILABLE)
    return {"stored": True}
