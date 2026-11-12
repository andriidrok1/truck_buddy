"""Device tokens, sign-in with Apple and Google, the driver's stored truck profile, "delete my data" and deleting the
account: /api/auth/*, /api/me/profile, /api/memory and /api/account.

Mounted by server.py ahead of its own routers, so the routes keep their order. `public` needs no token; `api` needs a
valid one and counts against the driver's request budget (services/auth.py), like server.api.
"""
import asyncio
import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from models import TruckProfile
from services import accounts, apple, auth, breakdown, db, identity, memory, session_store, tenancy, trip_events

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
    authorization_code: Optional[str] = Field(None, max_length=2048)  # traded for Apple's refresh token (services/apple.py)
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
    new_account: bool  # the account's first time through: nothing was ever set up for it
    has_profile: bool  # the driver finished setup before (own stored profile, or a fleet truck): the app skips onboarding
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


async def _sign_in(provider: identity.Provider, request: Request, verify, name: Optional[str], platform: Optional[str],
                   apple_code: Optional[str] = None) -> dict:
    """Checks the provider's token (`verify` returns the identity), finds or creates the account and opens a session
    for this phone. The account's sub is the key its data lives under, so the token handed back sees everything the
    driver stored from any other phone. `apple_code` is Apple's authorization code, traded for a refresh token in
    the background once the sign-in has succeeded."""
    if not provider.audiences():
        raise _not_configured(provider)
    if db.store() == "off":
        raise _unavailable()  # an account is a row: there is nowhere to keep one
    auth.signin_allowed(request)
    try:
        who = await verify()
    except identity.Invalid as e:
        auth.signin_failed(request)
        logger.info("%s sign-in refused: %s", provider.name, e)  # the reason stays here; the client learns nothing
        raise HTTPException(status_code=401, detail="Sign-in failed")
    except identity.Unavailable:
        raise _unavailable()
    except identity.NotConfigured:  # the variable was emptied between the check above and the verification
        raise _not_configured(provider)
    try:
        # Twice at most: an account deleted between being found and being signed in to (another phone's "Delete
        # account" in that moment) is gone for good, and this sign-in starts a new one under a new sub.
        for _ in range(2):
            account = await accounts.find_or_create(who.provider, who.sub, auth.new_sub(), who.email, who.name or name)
            opened = await auth.open_session(account.sub, platform)
            if opened is not None:
                break
        else:
            raise _unavailable()
        out, new = opened
        has_profile = await tenancy.has_profile(account.sub)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("%s sign-in failed in the store: %s", provider.name, e)
        raise _unavailable()
    apple.remember_later(account.sub, apple_code, who.audience)
    return {**out, "new_account": new, "has_profile": has_profile, "name": account.name, "email": account.email}


@public.post("/auth/apple", response_model=SignInResponse)
async def auth_apple(req: AppleSignInRequest, request: Request):
    """Sign in with Apple: the identity token from the phone, plus the raw nonce the app made for it."""
    name = (req.name or "").strip() or None
    return await _sign_in(identity.APPLE, request, lambda: identity.verify_apple(req.identity_token, req.nonce), name, req.platform,
                          req.authorization_code)


@public.post("/auth/google", response_model=SignInResponse)
async def auth_google(req: GoogleSignInRequest, request: Request):
    """Sign in with Google: the id token from the phone."""
    return await _sign_in(identity.GOOGLE, request, lambda: identity.verify_google(req.id_token), None, req.platform)


@public.post("/auth/logout")
async def auth_logout(claims: dict = Depends(auth.logout_claims)):
    """Signs this phone out: its session and refresh token go, the account's other phones stay signed in. Any token
    this server signed will do, expired or not. Answers the same when the session is already gone, or for a device
    token (which has no session), so the app can always finish signing out."""
    sid = claims.get("sid")
    if sid and db.store() != "off":
        try:
            await accounts.session_close(str(sid))
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


@api.post("/auth/logout-all")
async def auth_logout_all(principal: auth.Principal = Depends(auth.require_driver)):
    """Signs every phone of this account out, this one included (a phone was lost or stolen). The other phones are
    refused within a minute. A device token has no account: nothing to close."""
    if not principal.sid or db.store() == "off":
        return {"ok": True, "closed": 0}
    try:
        return {"ok": True, "closed": await accounts.close_all(principal.sub)}
    except Exception as e:  # noqa: BLE001
        logger.warning("logout-all failed: %s", e)
        raise _unavailable()


@api.post("/auth/fleet-code")
async def auth_fleet_code(req: FleetCodeRequest, principal: auth.Principal = Depends(auth.require_driver)):
    """Binds this device to the fleet (and truck) the code was made for; answers with a token carrying the org."""
    try:
        dev = await tenancy.bind_fleet_code(principal.sub, req.code, create=not principal.sid)
    except tenancy.Unregistered:  # the account was deleted while this request was on its way
        raise auth._unregistered()
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
        await tenancy.profile_put(principal.sub, profile, create=not principal.sid)
    except tenancy.Unregistered:  # the account was deleted while this request was on its way
        raise auth._unregistered()
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


async def _wipe(driver: str) -> dict:
    """What "Delete my data" removes: the trip events, the remembered turns, facts and visits (after the saves still
    in flight land) and the driver's own stored truck profile and name."""
    await tenancy.revoke_grace(driver)  # old tokens die at their exp; a stolen one cannot ride the grace
    events = await trip_events.clear(driver)
    turns = await memory.clear(driver)
    return {"deleted": turns, "trip_events": events, "profile": await tenancy.profile_clear(driver)}


@api.delete("/memory")
async def memory_clear(driver: str = Depends(auth.require_driver)):
    """"Delete my data": the dialogs, the remembered turns and facts, the trip events (after the saves still in
    flight land) and the driver's own stored truck profile (a fleet truck's profile belongs to the fleet).
    `deleted` counts turns, as before."""
    await agent_sessions_drop(driver)
    try:
        return await asyncio.wait_for(_wipe(driver), MEMORY_WAIT_S)
    except Exception as e:  # noqa: BLE001
        logger.warning("memory clear failed: %s", e)
        raise HTTPException(status_code=503, detail="Memory is unavailable right now")


@api.delete("/memory/history")
async def memory_history_clear(driver: str = Depends(auth.require_driver)):
    """"Clear memory" on the history screen: what was said, what the copilot learned from it, and the dialogs, so the
    copilot does not carry the thread on. The truck profile, the trip events and the visit counts stay.
    `deleted` counts turns, `facts` is whether there were learned facts."""
    await agent_sessions_drop(driver)
    try:
        turns, facts = await asyncio.wait_for(memory.clear_history(driver), MEMORY_WAIT_S)
    except Exception as e:  # noqa: BLE001
        logger.warning("history clear failed: %s", e)
        raise HTTPException(status_code=503, detail="Memory is unavailable right now")
    return {"deleted": turns, "facts": facts > 0}


LAST_OWNER = ("This account is the only owner of a fleet that still has drivers or a stored key. "
              "Write to support@truckbuddy.io to close the fleet or hand it over.")


@api.delete("/account")
async def account_delete(principal: auth.Principal = Depends(auth.require_driver)):
    """Deletes the signed-in driver's account and everything stored for it; nothing of the driver is left, and every
    phone of the account is signed out. An Apple account's refresh token is revoked at Apple first (`apple_revoked`:
    true when Apple accepted the call, false when Apple could not be reached or refused, null when there was no
    token or no key to revoke with); the deletion goes on either way.

    The data goes first and the account row last, in one transaction with the sessions, so a deletion that failed
    halfway (503) is repeated with the same token; the stored Apple token is still there for the retry.

    409 for the only owner of a fleet that still has drivers, other phones or a stored key (nobody could manage it
    afterwards). A fleet with nobody else in it goes with its owner."""
    if not principal.sid:
        # A device or access-code token has no account behind it; its data goes with DELETE /api/memory.
        raise HTTPException(status_code=409, detail="This sign-in has no account. Use DELETE /api/memory to delete its data.")
    if db.store() == "off":
        raise _unavailable()
    driver = principal.sub
    try:
        await accounts.check_may_delete(driver)  # before anything is wiped
        await apple.settled(driver)  # an exchange still running for this account: its token is revoked too
        account = await accounts.by_sub(driver)
        revoked = await apple.revoke(account[1], driver) if account and account[0] == "apple" else None
        breakdown.forget(driver)
        await agent_sessions_drop(driver, strict=True)
        wiped = await asyncio.wait_for(_wipe(driver), MEMORY_WAIT_S)
        sessions = await asyncio.wait_for(accounts.erase(driver), MEMORY_WAIT_S)
    except accounts.LastOwner:
        raise HTTPException(status_code=409, detail=LAST_OWNER)
    except Exception as e:  # noqa: BLE001
        logger.warning("account deletion failed for %s: %s", driver, e)
        raise HTTPException(status_code=503, detail="The account could not be deleted right now. Try again.")
    return {"ok": True, **wiped, "sessions": sessions, "apple_revoked": revoked}


async def agent_sessions_drop(driver: str, strict: bool = False) -> None:
    """Forget the dialogs of this driver too (its id prefixes the session ids), wherever they are stored, and the
    memory reads still running for them."""
    from services import agent  # lazy: the agent imports half the services
    agent.forget_past(f"{driver}:")
    await session_store.drop_prefix(f"{driver}:", strict)
