"""Device tokens, the driver's stored truck profile and "delete my data": /api/auth/*, /api/me/profile and /api/memory.

Mounted by server.py ahead of its own routers, so the routes keep their order. `public` needs no token; `api` needs a
valid one and counts against the driver's request budget (services/auth.py), like server.api.
"""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from models import TruckProfile
from services import auth, memory, session_store, tenancy, trip_events

logger = logging.getLogger("truckbuddy")

public = APIRouter(prefix="/api")
api = APIRouter(prefix="/api", dependencies=[Depends(auth.rate_limited)])


class AccessCodeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=64)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=16, max_length=128)


class FleetCodeRequest(BaseModel):
    code: str = Field(min_length=4, max_length=32)


async def _issued(token: str, role: str) -> dict:
    """The freshly minted device token re-issued with its tenancy claims and a refresh token (same shape plus
    refresh_token/expires_in when a store is on)."""
    sub = auth.sub_of(token)
    try:
        return await auth.issue(sub, role)
    except Exception as e:  # noqa: BLE001 - the store is down: the plain token still works
        logger.warning("token issue without tenancy: %s", e)
        return {"access_token": token, "token_type": "bearer", "role": role}


@public.post("/auth/access-code")
async def access_code(req: AccessCodeRequest, request: Request):
    token, role = auth.redeem(req.code, request)
    return await _issued(token, role)


@public.post("/auth/device")
async def auth_device(request: Request):
    return await _issued(auth.register_device(request), "driver")


@public.post("/auth/refresh")
async def auth_refresh(req: RefreshRequest):
    """A new access token for a device's refresh token (the refresh token rotates)."""
    return await auth.refresh(req.refresh_token)


@api.post("/auth/fleet-code")
async def auth_fleet_code(req: FleetCodeRequest, principal: auth.Principal = Depends(auth.require_driver)):
    """Binds this device to the fleet (and truck) the code was made for; answers with a token carrying the org."""
    try:
        dev = await tenancy.bind_fleet_code(principal.sub, req.code)
    except tenancy.BadFleetCode:
        raise HTTPException(status_code=401, detail="That fleet code isn't right or has expired")
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Fleet codes are unavailable right now")
    out = await auth.issue(principal.sub, dev.role)
    out.update({"org_id": dev.org_id, "vehicle_id": dev.vehicle_id})
    return out


@api.get("/me/profile", response_model=TruckProfile)
async def me_profile(response: Response, principal: auth.Principal = Depends(auth.require_driver)):
    """The truck profile the server routes for when the app sends none: the vehicle's when the device is bound to
    one, else the driver's. 404 when nothing is stored yet (the app keeps its local copy). X-Profile-Source says
    which: "vehicle" is the fleet's truck, which a driver's own change does not overwrite (it holds for the trip
    on the phone), "driver" is the driver's own copy."""
    fleet_truck = bool(principal.vehicle_id) and principal.role == "driver"
    response.headers["X-Profile-Source"] = "vehicle" if fleet_truck else "driver"
    try:
        profile = await tenancy.profile_get(principal.sub)
    except Exception as e:  # noqa: BLE001
        logger.warning("profile read failed: %s", e)
        raise HTTPException(status_code=503, detail="Profile is unavailable right now")
    if profile is None:
        raise HTTPException(status_code=404, detail="No stored profile")
    return profile


@api.put("/me/profile", response_model=TruckProfile)
async def me_profile_put(profile: TruckProfile, principal: auth.Principal = Depends(auth.require_driver)):
    try:
        await tenancy.profile_put(principal.sub, profile)
    except Exception as e:  # noqa: BLE001
        logger.warning("profile write failed: %s", e)
        raise HTTPException(status_code=503, detail="Profile is unavailable right now")
    return profile


@api.get("/auth/check")
async def auth_check():
    return {"ok": True}


MEMORY_WAIT_S = 10.0  # the history screen: a down database is a clear error, not a hung spinner


@api.get("/memory")
async def memory_list(driver: str = Depends(auth.require_driver), limit: int = Query(200, ge=1, le=1000)):
    try:
        return {"turns": await asyncio.wait_for(memory.recent(driver, limit), MEMORY_WAIT_S)}
    except Exception as e:  # noqa: BLE001
        logger.warning("memory list failed: %s", e)
        raise HTTPException(status_code=503, detail="Memory is unavailable right now")


@api.delete("/memory")
async def memory_clear(driver: str = Depends(auth.require_driver)):
    """"Delete my data": the dialogs, the remembered turns and facts, the ledger of loads, the trip events (after the
    saves still in flight land) and the driver's own stored truck profile (a fleet truck's profile belongs to the fleet).
    `deleted` counts turns, as before."""
    await agent_sessions_drop(driver)

    async def wipe() -> dict:
        events = await trip_events.clear(driver)
        turns = await memory.clear(driver)
        return {"deleted": turns, "trip_events": events, "profile": await tenancy.profile_clear(driver)}
    try:
        return await asyncio.wait_for(wipe(), MEMORY_WAIT_S)
    except Exception as e:  # noqa: BLE001
        logger.warning("memory clear failed: %s", e)
        raise HTTPException(status_code=503, detail="Memory is unavailable right now")


async def agent_sessions_drop(driver: str) -> None:
    """Forget the dialogs of this driver too (its id prefixes the session ids), wherever they are stored."""
    await session_store.drop_prefix(driver)
