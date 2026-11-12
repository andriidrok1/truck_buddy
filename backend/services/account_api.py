"""Device tokens, sign-in with Apple and Google, the driver's stored truck profile and "delete my data": /api/auth/*,
/api/me/profile and /api/memory.

Mounted by server.py ahead of its own routers, so the routes keep their order. `public` needs no token; `api` needs a
valid one and counts against the driver's request budget (services/auth.py), like server.api.
"""
import asyncio
import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from models import TruckProfile
from services import accounts, auth, db, identity, memory, session_store, tenancy, trip_events

logger = logging.getLogger("truckbuddy")

public = APIRouter(prefix="/api")
api = APIRouter(prefix="/api", dependencies=[Depends(auth.rate_limited)])


class AccessCodeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=64)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=16, max_length=128)


class FleetCodeRequest(BaseModel):
    code: str = Field(min_length=4, max_length=32)


# Provider tokens are JWTs of 1-2 KB; the limits only keep a request body from being megabytes.
class AppleSignInRequest(BaseModel):
    identity_token: str = Field(min_length=20, max_length=4096)
    nonce: str = Field(min_length=8, max_length=128)  # the raw nonce; Apple's token carries its SHA-256
    authorization_code: Optional[str] = Field(None, max_length=2048)  # for revoking on account deletion; not used yet
    name: Optional[str] = Field(None, max_length=120)  # Apple gives the name to the app only, on the first authorization
    platform: Optional[Literal["ios", "android"]] = None


class GoogleSignInRequest(BaseModel):
    id_token: str = Field(min_length=20, max_length=4096)
    platform: Optional[Literal["ios", "android"]] = None


class SignInResponse(BaseModel):
    access_token: str
    token_type: str
    role: str
    refresh_token: str
    expires_in: int
    new_account: bool  # this sign-in created the account
    has_profile: bool  # a truck profile is stored for it: the app skips onboarding
    name: Optional[str] = None
    email: Optional[str] = None


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


def _unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="Sign-in is unavailable right now")


def _not_configured(provider: identity.Provider) -> HTTPException:
    logger.warning("%s sign-in refused: %s is empty", provider.name, provider.audiences_env)
    return HTTPException(status_code=503, detail="Sign-in is not configured")


async def _sign_in(provider: identity.Provider, request: Request, verify, name: Optional[str], platform: Optional[str]) -> dict:
    """Checks the provider's token (`verify` returns the identity), finds or creates the account and opens a session
    for this phone. The account's sub is the key its data lives under, so the token handed back sees everything the
    driver stored from any other phone."""
    if not provider.audiences():
        raise _not_configured(provider)
    if db.store() == "off":
        raise _unavailable()  # an account is a row: there is nowhere to keep one
    auth.signin_attempt(request)
    try:
        who = await verify()
    except identity.Invalid as e:
        logger.info("%s sign-in refused: %s", provider.name, e)  # the reason stays here; the client learns nothing
        raise HTTPException(status_code=401, detail="Sign-in failed")
    except identity.Unavailable:
        raise _unavailable()
    except identity.NotConfigured:  # the variable was emptied between the check above and the verification
        raise _not_configured(provider)
    try:
        account, new = await accounts.find_or_create(who.provider, who.sub, auth.new_sub(), who.email, who.name or name)
        sid = await accounts.session_open(account.sub, platform)
        out = await auth.issue(account.sub, "driver", sid)
        has_profile = await tenancy.has_profile(account.sub)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("%s sign-in failed in the store: %s", provider.name, e)
        raise _unavailable()
    return {**out, "new_account": new, "has_profile": has_profile, "name": account.name, "email": account.email}


@public.post("/auth/apple", response_model=SignInResponse)
async def auth_apple(req: AppleSignInRequest, request: Request):
    """Sign in with Apple: the identity token from the phone, plus the raw nonce the app made for it."""
    name = (req.name or "").strip() or None
    return await _sign_in(identity.APPLE, request, lambda: identity.verify_apple(req.identity_token, req.nonce), name, req.platform)


@public.post("/auth/google", response_model=SignInResponse)
async def auth_google(req: GoogleSignInRequest, request: Request):
    """Sign in with Google: the id token from the phone."""
    return await _sign_in(identity.GOOGLE, request, lambda: identity.verify_google(req.id_token), None, req.platform)


@public.post("/auth/logout")
async def auth_logout(claims: dict = Depends(auth.token_claims)):
    """Signs this phone out: its session and refresh token go, the account's other phones stay signed in. Answers
    the same when the session is already gone, or for a device token (which has no session), so the app can always
    finish signing out."""
    sid = claims.get("sid")
    if sid and db.store() != "off":
        try:
            await accounts.session_close(sid)
        except Exception as e:  # noqa: BLE001
            logger.warning("logout failed: %s", e)
            raise _unavailable()
    return {"ok": True}


@public.post("/auth/refresh")
async def auth_refresh(req: RefreshRequest):
    """A new access token for a refresh token (which rotates): an account session's, or a device's."""
    return await auth.refresh(req.refresh_token)


@api.post("/auth/renew")
async def auth_renew(principal: auth.Principal = Depends(auth.require_driver)):
    """A fresh access token and refresh token for the device behind the Bearer token (valid, or expired inside the
    grace window). For app builds that never stored a refresh token: they trade the token they have for both."""
    try:
        return await auth.issue(principal.sub, principal.role, principal.sid)
    except HTTPException:  # the session was closed while this request was on its way
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("renew failed: %s", e)
        raise _unavailable()


@api.post("/auth/fleet-code")
async def auth_fleet_code(req: FleetCodeRequest, principal: auth.Principal = Depends(auth.require_driver)):
    """Binds this device to the fleet (and truck) the code was made for; answers with a token carrying the org."""
    try:
        dev = await tenancy.bind_fleet_code(principal.sub, req.code)
    except tenancy.BadFleetCode:
        raise HTTPException(status_code=401, detail="That fleet code isn't right or has expired")
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Fleet codes are unavailable right now")
    out = await auth.issue(principal.sub, dev.role, principal.sid)
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
    """"Delete my data": the dialogs, the remembered turns and facts, the trip events (after the saves still in
    flight land) and the driver's own stored truck profile (a fleet truck's profile belongs to the fleet).
    `deleted` counts turns, as before."""
    await agent_sessions_drop(driver)

    async def wipe() -> dict:
        await tenancy.revoke_grace(driver)  # old tokens die at their exp; a stolen one cannot ride the grace
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
