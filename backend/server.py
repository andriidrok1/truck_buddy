import asyncio
import json
import re
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from typing import Literal, Optional

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.middleware.cors import CORSMiddleware

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

from models import AgentRequest, AgentResponse, Category, NudgeRequest, NudgeResponse, ParkingCheck, ParkingRequest, Place, PlacesResponse, RerouteRequest, RouteInfo, RouteRequest, RouteResponse, TruckProfile  # noqa: E402,E501
from services.agent import get_agent_provider  # noqa: E402
from services import breakdown, flags, nudges  # noqa: E402
from services.speech_text import split_reply  # noqa: E402
from services.google_places import PlacesUnavailable  # noqa: E402
from services.parking import check_parking, fetch_satellite  # noqa: E402
from services.places import get_places_provider  # noqa: E402
from services.errors import NoTruckRoute  # noqa: E402
from services.routing import PARSE_DEGRADED, RoutingUnavailable, get_routing_provider  # noqa: E402
from services import account_api, approach, approach_api, auth, chains, db, fleet_api, fuel_index, i18n, kv, leader, memory, onboarding, parking_store, parking_warm, quota, reqid, route_cost, session_store, spend, tenancy, timing, tpims, trip_events, trips_api, voice, weather  # noqa: E402,E501
from services.http import client as http_client  # noqa: E402
from services.logs import said, transcripts_on  # noqa: E402
from services.settings import settings  # noqa: E402

WEATHER_BUDGET_S = 2.5  # NWS alerts on /route may add at most this much; never the route itself
from pydantic import BaseModel, Field  # noqa: E402

reqid.install_log_field()  # %(req)s: the request id on every line (services/reqid.py)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(req)s - %(message)s")
logger = logging.getLogger("truckbuddy")
# httpx logs every request URL at INFO: HERE's apiKey, the driver's geocode text and position. Warnings only.
for _noisy in ("httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

app = FastAPI(
    title="TruckBuddy API",
    version="0.2.0",
    description="Voice copilot for truckers: Google Places, HERE truck routing, OpenAI STT/LLM/TTS, satellite parking checks.",
    openapi_url="/api/openapi.json",
    docs_url="/api/docs" if os.environ.get("DEV_DOCS") == "1" else None,  # Swagger only on dev instances
    redoc_url=None,
)
public = APIRouter(prefix="/api")  # no access code: health, code check, reply audio (opened by the player)
# Every other route: valid token + per-driver request budget (services/auth.py).
api = APIRouter(prefix="/api", dependencies=[Depends(auth.rate_limited)])

# Continental US (with margin): the app serves US truckers; anything else is a bad client or an abuse probe.
US_BBOX = (24.0, 50.0, -125.5, -66.0)


def _require_us(lat: float, lng: float) -> None:
    if not (US_BBOX[0] <= lat <= US_BBOX[1] and US_BBOX[2] <= lng <= US_BBOX[3]):
        raise HTTPException(status_code=422, detail="Outside the service area")


DEEP_CACHE_S = 30.0  # /health?deep=1: one HERE call per this many seconds per worker at most, whoever asks
DEEP_TIMEOUT_S = 4.0
HERE_HEALTH_URL = "https://geocode.search.hereapi.com/v1/geocode"
_deep: dict = {}  # {"at": monotonic, "checks": {...}}
_deep_lock: tuple = (None, None)  # (loop, asyncio.Lock): a lock is bound to the loop it first waited on


async def _check_db() -> dict:
    """SELECT 1 on the configured store; {"ok": None} when the store is off (nothing to check)."""
    kind = db.store()
    if kind == "off":
        return {"ok": None, "store": "off"}
    t0 = asyncio.get_running_loop().time()
    try:
        if kind == "sqlite":
            await asyncio.wait_for(db._sq_run(lambda con: con.execute("SELECT 1").fetchone()), DEEP_TIMEOUT_S)
        else:
            pool = await asyncio.wait_for(db._db(), DEEP_TIMEOUT_S)
            await asyncio.wait_for(pool.fetchval("SELECT 1"), DEEP_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "store": kind, "error": type(e).__name__}
    return {"ok": True, "store": kind, "ms": round((asyncio.get_running_loop().time() - t0) * 1000)}


async def _check_here() -> dict:
    """One HERE geocode (the cheapest call on the key routing uses); skipped without a key or with HEALTH_HERE=0."""
    key = os.environ.get("HERE_API_KEY", "").strip()
    if not key or not flags.on("HEALTH_HERE"):
        return {"ok": None, "skipped": True}
    t0 = asyncio.get_running_loop().time()
    try:
        r = await http_client().get(HERE_HEALTH_URL, params={"q": "Sacramento, CA", "limit": 1, "apiKey": key},
                                    timeout=DEEP_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": type(e).__name__}
    ms = round((asyncio.get_running_loop().time() - t0) * 1000)
    if r.is_error:
        return {"ok": False, "status": r.status_code, "ms": ms}
    return {"ok": True, "ms": ms}


async def _deep_checks() -> dict:
    """The cached db + HERE checks, refreshed at most once per DEEP_CACHE_S: concurrent callers wait for the one
    refresh in flight instead of each calling HERE."""
    global _deep_lock
    loop = asyncio.get_running_loop()
    if _deep_lock[0] is not loop:
        _deep_lock = (loop, asyncio.Lock())
    async with _deep_lock[1]:
        now = loop.time()
        if not _deep or now - _deep["at"] >= DEEP_CACHE_S:
            db_check, here_check = await asyncio.gather(_check_db(), _check_here())
            _deep.update(at=now, checks={"db": db_check, "here": here_check})
        return _deep["checks"]


@public.get("/health")
async def health(deep: bool = Query(False), credentials=Depends(auth._bearer)):
    """Liveness and the providers in use (public); ?deep=1 (tester token only) also checks the database and one
    HERE call (cached DEEP_CACHE_S) and answers 503 when either fails."""
    if deep:
        await auth.require_tester(credentials)
    out = {
        "ok": True,
        "providers": {
            "places": get_places_provider().source,
            "routing": get_routing_provider().source,
            "agent": get_agent_provider().source,
            "tts": voice.provider(),
        },
        "parking_cache": parking_store.stats(),
        "here_parse_degraded": dict(PARSE_DEGRADED),  # parts of HERE routes dropped since start
        **spend.health(),  # spend_today_usd, spend_cap_usd, spend_stage, calls_today (services/spend.py)
    }
    if not deep:
        return out
    out["checks"] = await _deep_checks()
    if any(c.get("ok") is False for c in out["checks"].values()):
        out["ok"] = False
        return JSONResponse(status_code=503, content=out)
    return out


@api.get("/places", response_model=PlacesResponse)
async def search_places(
    category: Category = Query(...),
    lat: float = Query(38.5816, ge=-90, le=90),
    lng: float = Query(-121.4944, ge=-180, le=180),
):
    return await get_places_provider().search(category, lat, lng)


@api.post("/route", response_model=RouteResponse)
async def build_route(req: RouteRequest, driver: str = Depends(auth.require_driver)):
    place = req.place
    if place is None and req.place_id:
        place = await get_places_provider().get(req.place_id, req.lat, req.lng)
    if place is None:
        raise HTTPException(status_code=404, detail="Place not found")
    _require_us(req.lat, req.lng)
    _require_us(place.lat, place.lng)
    for stop in req.via:
        if len(stop) != 2:
            raise HTTPException(status_code=422, detail="via stops are [lat, lng] pairs")
        _require_us(stop[0], stop[1])
    req.truck_profile = await tenancy.effective_profile(driver, req.truck_profile)
    try:
        route = await get_routing_provider().route(req.lat, req.lng, *approach.target(place), req.truck_profile,
                                                   via=req.via)
    except NoTruckRoute:
        trip_events.add_later(driver, req.trip_id, "route_failed", {"where": "route", "error": "no_truck_route", "place_id": place.id},
                              pos=(req.lat, req.lng))
        raise HTTPException(status_code=422, detail="No truck route to this place from here")
    except RoutingUnavailable:
        trip_events.add_later(driver, req.trip_id, "route_failed", {"where": "route", "error": "unavailable", "place_id": place.id},
                              pos=(req.lat, req.lng))
        raise HTTPException(status_code=503, detail="Truck routing is unavailable right now")
    # NWS alerts and Caltrans chain controls along the way; slow or down sources cost at most WEATHER_BUDGET_S
    # and never the route itself.
    await asyncio.gather(_advisories(route, WEATHER_BUDGET_S, WEATHER_BUDGET_S),
                         route_cost.annotate(route, req.lat, req.lng, req.truck_profile))
    if req.skip_parking:  # breakdown providers: the driver needs the shop, no parking check at all
        return RouteResponse(place=place, route=i18n.localize_route(route), parking=None)
    parking = approach.for_route(place, route)
    approach.background(check_parking(place, route.approach))  # don't wait for the satellite; app re-asks /parking
    return RouteResponse(place=place, route=i18n.localize_route(route), parking=await i18n.localize_parking(parking))


@api.post("/nudges", response_model=NudgeResponse)
async def nudges_endpoint(req: NudgeRequest, request: Request, driver: str = Depends(auth.require_driver)):
    """Anything the copilot wants to say on its own right now (break due, parking before the shift ends)."""
    _require_us(req.lat, req.lng)
    req.lang = i18n.use_lang(req.lang)
    req.motive_key, req.motive_user = await motive_auth(request, driver)
    req.truck_profile = await tenancy.effective_profile(driver, req.truck_profile)
    if req.session_id:
        req.session_id = f"{driver}:{req.session_id}"[:64]
    else:
        req.session_id = f"{driver}:solo"[:64]
    store = session_store.get_store()
    state = await store.load(req.session_id)
    token = session_store.enter(req.session_id, state)
    try:
        out = await nudges.compute(req, state, req.motive_key)
    except Exception as e:  # noqa: BLE001 - never an error for a background poll
        logger.warning("nudges failed: %s: %s", type(e).__name__, e)
        out = []
    finally:
        session_store.leave(token)
        try:
            await store.save(req.session_id, state)  # a nudge offer becomes the next turn's pending answer
        except Exception as e:  # noqa: BLE001
            logger.warning("dialog state save failed for %s: %s", req.session_id, e)
    if i18n.lang() != "en":
        for n in out:
            n.text = await i18n.localize(n.text)
    return NudgeResponse(nudges=out)


REROUTE_ADVISORY_BUDGET_S = 1.0


@api.post("/reroute", response_model=RouteResponse)
async def reroute(req: RerouteRequest, driver: str = Depends(auth.require_driver)):
    """The app detected the driver left the route: a new route from the current position to the same place.
    No new satellite check (the destination hasn't changed); the cached verdict comes along, its entry tip
    phrased for the way this route arrives."""
    _require_us(req.lat, req.lng)
    _require_us(req.place.lat, req.place.lng)
    for stop in req.via:
        if len(stop) != 2:
            raise HTTPException(status_code=422, detail="via stops are [lat, lng] pairs")
        _require_us(stop[0], stop[1])
    req.truck_profile = await tenancy.effective_profile(driver, req.truck_profile)
    try:
        route = await get_routing_provider().reroute(req.handle, req.lat, req.lng, req.heading,
                                                     *approach.target(req.place), req.truck_profile,
                                                     section_index=req.section_index, traveled_m=req.traveled_m,
                                                     via=req.via)
    except NoTruckRoute:
        _reroute_event(driver, req, ok=False, error="no_truck_route")
        raise HTTPException(status_code=422, detail="No truck route to this place from here")
    except RoutingUnavailable:
        _reroute_event(driver, req, ok=False, error="unavailable")
        raise HTTPException(status_code=503, detail="Truck routing is unavailable right now")
    _reroute_event(driver, req, ok=True)
    # The new route needs its own alerts and chain controls, or the advisories vanish after a reroute; the
    # driver is off the route while this runs, so the budget is shorter.
    await asyncio.gather(_advisories(route, REROUTE_ADVISORY_BUDGET_S, 0),
                         route_cost.annotate(route, req.lat, req.lng, req.truck_profile))  # cost + "fastest" like /route
    return RouteResponse(place=req.place, route=i18n.localize_route(route),
                         parking=await i18n.localize_parking(approach.for_route(req.place, route)))


def _reroute_event(driver: str, req: RerouteRequest, ok: bool, error: Optional[str] = None) -> None:
    """One trip event per /reroute: how far along the truck was and whether the engine answered."""
    data = {"section_index": req.section_index, "handle": bool(req.handle), "via": len(req.via), "ok": ok}
    if error:
        data["error"] = error
        trip_events.add_later(driver, req.trip_id, "route_failed", {"where": "reroute", "error": error, "place_id": req.place.id},
                              pos=(req.lat, req.lng))
    trip_events.add_later(driver, req.trip_id, "reroute_requested", data, pos=(req.lat, req.lng))


async def _advisories(route: RouteInfo, budget_s: float, alt_budget_s: float) -> None:
    """NWS alerts (route and alternatives) and Caltrans chain controls, each bounded, never an error."""
    async def chain_controls():
        try:
            return await asyncio.wait_for(chains.for_route(route.geometry), budget_s)
        except Exception as e:  # noqa: BLE001
            logger.info("route chains skipped: %s", type(e).__name__)
            return []

    try:
        _, route.chain_controls = await asyncio.gather(weather.for_route(route, budget_s, alt_budget_s), chain_controls())
    except Exception as e:  # noqa: BLE001
        logger.info("route advisories failed: %s", e)
        route.weather, route.chain_controls = [], []


@api.post("/parking", response_model=ParkingCheck)
async def parking_check(req: ParkingRequest):
    _require_us(req.place.lat, req.place.lng)  # each new coordinate is a satellite fetch + a vision call
    return await i18n.localize_parking(await check_parking(req.place, req.approach))


@api.get("/satellite")  # behind the token: the thumbnail is loaded with the auth header
async def satellite(lat: float = Query(..., ge=-90, le=90), lng: float = Query(..., ge=-180, le=180)):
    try:
        img = await fetch_satellite(lat, lng)
    except Exception:
        raise HTTPException(status_code=502, detail="Satellite image unavailable")
    return Response(content=img, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


async def _run_agent(req: AgentRequest, speak: bool, driver: Optional[str] = None) -> AgentResponse:
    if driver and req.session_id:
        req.session_id = f"{driver}:{req.session_id}"[:64]  # sessions can't be shared across drivers
    role = getattr(driver, "role", "driver")
    if not spend.turns_open(role):
        # The day's money is spent (services/spend.py, stage no_ai): no model call, a stored line.
        logger.info("turn refused for %s: spend stage %s", driver, spend.stage())
        return await _speak(AgentResponse(status="chat", transcript=req.text, reply=spend.AI_PAUSED, source="spend"), speak)
    new_device = False
    if driver and role != "tester":
        created = await tenancy.device_created(driver)
        new_device = created is not None and (db.utcnow() - created).total_seconds() < quota.NEW_DEVICE_S
    if not quota.take_turn(driver, role, new_device=new_device):
        # Free release: the day's copilot turns are spent (services/quota.py). Said, not an error screen.
        logger.info("turn budget spent for %s%s", driver, " (new device)" if new_device else "")
        return await _speak(AgentResponse(status="chat", transcript=req.text, reply=quota.OUT_OF_TURNS,
                                          source="quota"), speak)
    res = await get_agent_provider().handle(req, driver)
    logger.info("agent text=%s status=%s place=%s", said(req.text), res.status, res.place.name if res.place else None)
    return await _speak(res, speak)


async def _same(text):
    return text


async def _speak(res: AgentResponse, speak: bool) -> AgentResponse:
    # The driver's language (services/i18n.py): the reply and the UI message as they will be shown and said.
    if i18n.lang() != "en":
        # A Google Maps grounded answer (what drivers say, services/maps_grounding.py) is said exactly as Google
        # returned it: their terms forbid changing it, so it stays English (the tool answers only in English).
        # A place the driver wrote in Cyrillic keeps the driver's spelling ("до Рино", not "до Reno").
        # The breakdown comparison on the screen (services/breakdown.py) goes at the same time as the reply.
        keep = not res.reply or bool(res.maps_sources)
        res.reply, res.message, res.summary = await asyncio.gather(
            _same(res.reply) if keep else i18n.localize_reply(res.reply, res.transcript),
            i18n.localize_reply(res.message, res.transcript), i18n.localize(res.summary or ""))
        res.summary = res.summary or None
        res.route = i18n.localize_route(res.route)
        res.parking = await i18n.localize_parking(res.parking)
        res.call_card = i18n.localize_call_card(res.call_card)
    if speak and res.reply and res.source in FIXED_SOURCES:
        res.reply_parts = [res.reply]
        audio_id = await _fixed_clip(res.reply)
        if audio_id:
            res.audio_url = f"/api/voice/audio/{audio_id}"
        return res
    if speak and res.reply:
        # The first clip (first clause, or first sentence with TTS_CLAUSE_SPLIT=0) is spoken as soon as it's
        # ready; the rest is generated in parallel and the app plays it right after.
        # Streamed (TTS_STREAM=1, one worker): Gemini when it is on, else OpenAI (voice.synthesize_stream). Without a
        # stream Gemini makes the first clip whole (1-2.5 s), so then it always ends at the first clause.
        stream = flags.on("TTS_STREAM", "0") and settings().workers == 1 and (
            voice.gemini_active() or not voice.eleven_voice())
        clause = flags.on("TTS_CLAUSE_SPLIT") or (not stream and voice.gemini_active())
        first, rest = split_reply(res.reply, clause=clause)
        res.reply_parts = [first, rest] if rest else [first]
        if rest:
            # The rest is made whole, in parallel: it is ready before the first clip has finished playing.
            res.audio_url_2 = f"/api/voice/audio/{voice.synthesize_later(rest, previous_text=first)}"
        if stream:
            # (a stream lives in the worker that started it: with several workers the clip is made whole)
            # Streamed: the id is ready now; the first bytes reach the app in ~0.5-1 s instead of a whole clip.
            audio_id = voice.synthesize_stream(first)
            timing.add("tts", 0)
        else:
            with timing.stage("tts"):
                audio_id = await voice.synthesize(first, next_text=rest or None)
        if audio_id:
            res.audio_url = f"/api/voice/audio/{audio_id}"
    return res


FIXED_SOURCES = ("quota", "spend")  # budget lines: the same words every time, made once and kept


async def _fixed_clip(text: str) -> Optional[str]:
    """A fixed line's clip, made once per text and voice and kept on disk (no TTS call per refused turn)."""
    key = f"{voice.cache_tag()}|{text}"
    audio_id = voice.pinned(key)
    if audio_id:
        return audio_id
    with timing.stage("tts"):
        audio_id = await voice.synthesize(text)
    return voice.pin(audio_id, key) if audio_id else None


TURN_STAGES = ("upload", "stt", "llm_round_1", "tools", "tts")


def _finish(res: AgentResponse, extra: Optional[dict] = None, driver: Optional[str] = None,
            trip_id: Optional[str] = None, sid: Optional[str] = None,
            pos: Optional[tuple[float, float]] = None) -> AgentResponse:
    """Timings onto the response, one `turn {json}` log line per copilot turn (scripts/turn_stats.py reads it:
    request id, device, driver, the app's session id, org, status, intent, notes, timings; never the transcript), and
    the turn's trip events when the driver is known (services/trip_events.from_turn)."""
    res.timings = {**(extra or {}), **timing.current()}
    res.timings["total"] = sum(v for k, v in res.timings.items() if k != "total" and not timing.is_counter(k))
    # Server-side estimate of when the driver first hears the reply: everything before the first clip is ready.
    res.timings["first_audio"] = sum(res.timings.get(k, 0) for k in TURN_STAGES)
    notes = timing.notes()
    res.intent = notes.get("intent") or res.intent
    # device = the phone's sub, driver = drivers.id (the row the fleet tables use); both look like drv_…, and
    # "driver" used to be the sub, which no fleet table could be joined on (review 2026-11-02, bug 11).
    fields = {"req": reqid.current(), "device": str(driver) if driver else None,
              "driver": getattr(driver, "driver_id", None), "sid": sid, "org": getattr(driver, "org_id", None),
              "status": res.status, "intent": res.intent or "-",
              **{k: v for k, v in notes.items() if k != "intent"}, **res.timings}
    logger.info("turn %s", json.dumps(fields, separators=(",", ":"), default=str))
    trip_events.from_turn(driver, trip_id, res.status, res.intent, notes, res.transcript or "", pos=pos)
    return res


@api.post("/agent", response_model=AgentResponse)
async def agent(req: AgentRequest, request: Request, speak: bool = Query(False),
                driver: str = Depends(auth.require_driver)):
    timing.begin()
    req.lang = i18n.use_lang(req.lang)  # the body's field, else the X-Lang header; the model is told either way
    req.motive_key, req.motive_user = await motive_auth(request, driver)
    req.truck_profile = await tenancy.effective_profile(driver, req.truck_profile)
    sid = req.session_id
    return _finish(await _run_agent(req, speak, driver), {"stt": req.stt_ms} if req.stt_ms else None, driver, req.trip_id,
                   sid, (req.lat, req.lng))


def _motive_key(request: Request) -> Optional[str]:
    """The driver's Motive API key, sent per request by the app; used for one call, never kept."""
    key = (request.headers.get("X-Motive-Key") or "").strip()
    return key[:200] or None


async def motive_auth(request: Optional[Request], driver) -> tuple[Optional[str], Optional[str]]:
    """(key, Motive user id) for the HOS lookup. The X-Motive-Key header wins (a solo driver's own key, user id
    None). Without it, the org's stored key, but only for a driver the fleet has mapped to a Motive user: a fleet
    key answers for every driver of the company. (None, None) when there is nothing to use."""
    key = _motive_key(request) if request is not None else None
    if key:
        return key, None
    org_id = getattr(driver, "org_id", None)
    if not org_id:
        return None, None
    try:
        secret = await tenancy.credential_get(org_id, "motive")
        if not secret:
            return None, None
        user = await tenancy.motive_user(getattr(driver, "driver_id", None))
    except Exception as e:  # noqa: BLE001 - HOS is optional; the turn goes on without it
        logger.info("fleet motive lookup skipped: %s", type(e).__name__)
        return None, None
    if not user:
        logger.info("fleet motive key present but driver %s is not mapped to a Motive user", getattr(driver, "driver_id", None))
        return None, None
    return secret, user


@api.get("/breakdown/{rid}", response_model=AgentResponse)
async def breakdown_result(rid: str, speak: bool = Query(False)):
    """Result of a breakdown research started by /agent (status=researching); waits until it's ready."""
    timing.begin()
    res = await breakdown.result(rid)
    if res is None:
        raise HTTPException(status_code=404, detail="Unknown research id")
    return _finish(await _speak(res, speak))


def _bg(coro) -> None:
    t = asyncio.create_task(coro)
    _BG.add(t)
    t.add_done_callback(_BG.discard)


_BG: set = set()
WARM_TOP = 1  # satellite check only for the first place of each category
WARM_EVERY_S = 600  # per driver: a warm-up is 3 Places searches + up to 3 vision calls
_WARM_LAST: dict[str, float] = {}


async def _warm(lat: float, lng: float) -> None:
    async def one(cat: str):
        try:
            res = await get_places_provider().search(cat, lat, lng)
        except Exception as e:  # noqa: BLE001
            logger.warning("warm %s failed: %s", cat, e)
            return
        for p in res.places[:WARM_TOP]:
            _bg(check_parking(p))
    await asyncio.gather(*(one(c) for c in ("truck_stop", "hotel", "rest_area")))


@api.post("/warm")
async def warm(lat: float = Query(..., ge=-90, le=90), lng: float = Query(..., ge=-180, le=180),
               driver: str = Depends(auth.require_driver)):
    """Pre-fetch Places for all categories (+ satellite check for the top place) around the driver.
    At most once per driver per WARM_EVERY_S; the answer is always ok (the app ignores it)."""
    now = asyncio.get_running_loop().time()
    if len(_WARM_LAST) > 10_000:
        for d in [d for d, t in _WARM_LAST.items() if now - t > WARM_EVERY_S]:
            del _WARM_LAST[d]
    if now - _WARM_LAST.get(driver, -WARM_EVERY_S) < WARM_EVERY_S:
        return {"ok": True}
    _WARM_LAST[driver] = now
    _bg(_warm(lat, lng))
    return {"ok": True}


@api.post("/voice", response_model=AgentResponse)
async def voice_turn(
    audio: UploadFile = File(...),
    lat: float = Form(..., ge=-90, le=90),
    lng: float = Form(..., ge=-180, le=180),
    active_place: Optional[str] = Form(None),  # JSON of the current destination Place
    session_id: Optional[str] = Form(None, max_length=64),  # live dialog id (conversation memory)
    truck_profile: Optional[str] = Form(None),  # JSON TruckProfile from the in-app editor
    active_distance_mi: Optional[float] = Form(None, ge=0),
    active_duration_min: Optional[int] = Form(None, ge=0),
    trip_id: Optional[str] = Form(None, max_length=64),  # the app's id of the drive (trip events)
    lang: Optional[str] = Form(None, max_length=10),  # the driver's language (services/i18n.py)
    driver: str = Depends(auth.require_driver),
    request: Request = None,
):
    timing.begin()
    lang = i18n.use_lang(lang)
    with timing.stage("upload"):
        data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty audio")
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Audio too long")
    current = None
    if active_place:
        try:
            current = Place(**json.loads(active_place))
        except Exception:
            raise HTTPException(status_code=422, detail="Invalid active_place")
    try:
        truck = TruckProfile(**json.loads(truck_profile)) if truck_profile else TruckProfile()
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid truck_profile")
    truck = await tenancy.effective_profile(driver, truck)
    name = audio.filename or "speech.m4a"
    if "." not in name:
        name += ".webm" if "webm" in (audio.content_type or "") else ".m4a"
    confidence = None
    try:
        with timing.stage("stt"):
            tr = await voice.transcribe_full(data, name, current.name if current else None)
            text, confidence = tr.text, tr.confidence
    except Exception:
        logger.exception("transcription failed")
        text = ""
    logger.info("voice transcript=%s confidence=%s", said(text), confidence)
    key, user = await motive_auth(request, driver)
    req = AgentRequest(text=text, lat=lat, lng=lng, active_place=current, truck_profile=truck, session_id=session_id,
                       active_distance_mi=active_distance_mi, active_duration_min=active_duration_min,
                       stt_confidence=confidence, trip_id=trip_id, motive_key=key, motive_user=user, lang=lang)
    return _finish(await _run_agent(req, speak=True, driver=driver), None, driver, trip_id, session_id, (lat, lng))


@api.post("/voice/transcribe")
async def voice_transcribe(audio: UploadFile = File(...), hint: Optional[str] = Form(None, max_length=600),
                           maybe: Optional[str] = Form(None, max_length=8), lang: Optional[str] = Form(None, max_length=10)):
    """Step 1 of a voice turn: speech -> text (the app shows it right away, then calls /agent?speak=true).

    `maybe=1`: the app's mic heard sound but no clear speech (src/voice/endpointer.ts). A short, unsure or
    made-up-from-noise transcript then comes back empty, so the agent never answers a "Thank you." nobody said.
    `lang` (or the X-Lang header): the recogniser's language and prompt (services/voice.stt_language_and_prompt)."""
    timing.begin()
    i18n.use_lang(lang)  # the recogniser's language and prompt (services/voice.stt_language_and_prompt)
    with timing.stage("upload"):
        data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty audio")
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Audio too long")
    name = audio.filename or "speech.m4a"
    if "." not in name:
        name += ".webm" if "webm" in (audio.content_type or "") else ".m4a"
    confidence = None
    try:
        with timing.stage("stt"):
            tr = await voice.transcribe_full(data, name, hint)
            text, confidence = tr.text, tr.confidence
    except voice.SttTimeout as e:
        logger.warning("transcription timed out: %s", e)
        raise HTTPException(status_code=504, detail="Speech recognition timed out")
    except Exception:
        logger.exception("transcription failed")
        text = ""
    dropped = False
    if maybe in ("1", "true") and text and voice.noise_only(text, confidence):
        dropped = True
        text = ""
    t = timing.current()
    logger.info("voice transcribe bytes=%d transcript=%s confidence=%s maybe=%s dropped=%s %s", len(data), said(text),
                confidence, bool(maybe), dropped, " ".join(f"{k}={v}" for k, v in t.items()))
    return {"transcript": text, "stt_confidence": confidence, "timings": t}


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=600)
    # Optional, for comparing voices; the app always uses the default.
    voice: Optional[Literal["alloy", "ash", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer"]] = None
    model: Optional[Literal["tts-1", "gpt-4o-mini-tts"]] = None
    # Optional ElevenLabs voice to compare (Voice Library id); the app always uses the configured one.
    eleven_voice_id: Optional[str] = Field(None, pattern=r"^[A-Za-z0-9]{10,40}$")


_SPEAK_CACHE: dict[str, str] = {}
_SPEAK_PENDING: dict[str, "asyncio.Task[Optional[str]]"] = {}


@api.post("/speak")
async def speak(req: SpeakRequest):
    """TTS for fixed copilot lines (onboarding, reminders). Cached per text, one generation per text."""
    key = f"{voice.cache_tag(req.voice, req.model, req.eleven_voice_id)}|{req.text}"
    audio_id = _SPEAK_CACHE.get(key) or voice.pinned(key)
    if not audio_id or voice.get_audio(audio_id) is None:
        if spend.voice_cheap() and key not in _SPEAK_PENDING:
            # Past the day's spend cap (services/spend.py): no new clips; the app says this line with the phone's
            # own voice (src/hooks/useSpeechQueue.ts falls back to it on any error).
            raise HTTPException(status_code=503, detail="Voice paused for today")
        task = _SPEAK_PENDING.get(key)
        if task is None:
            async def _gen() -> Optional[str]:
                aid = await voice.synthesize(req.text, req.voice, req.model, eleven_voice_id=req.eleven_voice_id)
                # pin inside the single flight: pin() moves the audio to a stable id, so a second waiter
                # pinning the same temp id again would get back a dead id (404)
                return voice.pin(aid, key) if aid else None  # fixed lines survive cache eviction and restarts
            task = asyncio.create_task(_gen())
            _SPEAK_PENDING[key] = task
            task.add_done_callback(lambda _t: _SPEAK_PENDING.pop(key, None))
        audio_id = await asyncio.shield(task)
        if not audio_id:
            raise HTTPException(status_code=502, detail="Voice unavailable")
        if voice.is_fallback(audio_id):
            return {"audio_url": f"/api/voice/audio/{audio_id}"}  # don't cache fallback audio as ElevenLabs
        if len(_SPEAK_CACHE) >= 200:
            _SPEAK_CACHE.pop(next(iter(_SPEAK_CACHE)))
        _SPEAK_CACHE[key] = audio_id
    return {"audio_url": f"/api/voice/audio/{audio_id}"}


@api.post("/onboarding/parse")
async def onboarding_parse(audio: UploadFile = File(...), step: str = Form(...)):
    if step not in ("name", "work", "truck", "prefs"):
        raise HTTPException(status_code=422, detail="Invalid step")
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty audio")
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Audio too long")
    name = audio.filename or "speech.m4a"
    try:
        text = await voice.transcribe(data, name)
    except Exception:
        logger.exception("transcription failed")
        text = ""
    updates = await onboarding.extract(step, text)
    # the updates are the driver's own answers (name, truck): their values only with LOG_TRANSCRIPTS on
    logger.info("onboarding step=%s transcript=%s updates=%s", step, said(text), updates if transcripts_on() else sorted(updates or {}))
    return {"transcript": text, "updates": updates}


_RANGE = re.compile(r"bytes=(\d*)-(\d*)")


@public.get("/voice/audio/{audio_id}")
async def voice_audio(audio_id: str, request: Request):
    """mp3 with byte-range support (AVPlayer on iOS probes with Range: bytes=0-1). A clip still being streamed
    is served as it arrives: a probe gets 206 with the first byte, a full request gets a 200 stream."""
    if voice.is_streaming(audio_id):
        rng = (request.headers.get("range") or "").strip()
        if _RANGE.fullmatch(rng) and rng.startswith("bytes=0-"):
            head = await voice.first_chunk(audio_id)
            if head is None:
                raise HTTPException(status_code=404, detail="Audio not found")
            return Response(content=head, status_code=206, media_type="audio/mpeg",
                            headers={"Accept-Ranges": "bytes", "Content-Range": "bytes 0-0/*", "Cache-Control": "no-store"})
        if voice.is_streaming(audio_id):
            return StreamingResponse(voice.stream_chunks(audio_id), media_type="audio/mpeg",
                                     headers={"Accept-Ranges": "none", "Cache-Control": "no-store"})
    data = await voice.wait_audio(audio_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Audio not found")
    size = len(data)
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=3600"}
    m = _RANGE.fullmatch((request.headers.get("range") or "").strip())
    if not m or (not m.group(1) and not m.group(2)):
        return Response(content=data, media_type="audio/mpeg", headers={**headers, "Content-Length": str(size)})
    if m.group(1):
        start = int(m.group(1))
        end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
    else:  # suffix range: last N bytes
        start, end = max(0, size - int(m.group(2))), size - 1
    if start >= size or start > end:
        return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
    chunk = data[start:end + 1]
    return Response(content=chunk, status_code=206, media_type="audio/mpeg", headers={
        **headers, "Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(len(chunk))})


app.include_router(account_api.public)
app.include_router(public)
app.include_router(account_api.api)
app.include_router(api)
app.include_router(approach_api.router, dependencies=[Depends(auth.rate_limited)])
app.include_router(trips_api.router)
app.include_router(fleet_api.router)
app.include_router(parking_warm.router)


_FUEL: list = []  # the EIA refresh loop; not in _BG, so the shutdown drain doesn't wait for it
DRAIN_S = 5.0  # background writes (memory, trip events, jobs, parking) get this long at shutdown


@app.on_event("startup")
async def _memory_up():
    s = settings()
    logger.info("workers=%d tts_dir=%s per-worker budgets: %d/min %d/10min %d/day%s", s.workers, s.tts_dir,
                auth.per_worker(auth.RATE_PER_MIN), auth.per_worker(s.rate_per_10min), auth.per_worker(s.rate_per_day),
                " (TTS_STREAM off: more than one worker)" if s.workers > 1 and flags.on("TTS_STREAM", "0") else "")
    _bg(memory.warm())
    _bg(spend.refresh())  # today's spend so far: the cap holds across restarts
    _bg(trip_events.prune())  # the trip log keeps TRIP_EVENTS_DAYS; the rest goes at every start
    _bg(kv.job_prune())  # breakdown results older than a day
    _bg(parking_store.start())  # verdicts and approaches other workers saved, refreshed every minute
    _FUEL.append(asyncio.create_task(fuel_index.keep_warm()))  # EIA averages for every region: a route's cost never waits
    _bg(weather.warm())  # the NWS alert feed and its zone polygons, so the first route pays nothing
    await chains.start()  # Caltrans chain-control poller (CHAIN_CONTROLS=0 to skip)
    await tpims.start()  # live truck parking counts (TPIMS_ENABLED=0 to skip)


def _draining() -> list:
    return [t for t in (*_BG, *memory._tasks, *trip_events._tasks, *approach._tasks, *breakdown._tasks,
                        *parking_store.pending(), *voice._PENDING.values(), *spend.pending_tasks()) if not t.done()]


async def drain(timeout: float = DRAIN_S) -> int:
    """Lets the background writes finish (a deploy must not lose the memory turn or the trip events that were
    in flight); returns how many were still running at the deadline."""
    pending = _draining()
    if pending:
        await asyncio.wait(pending, timeout=timeout)
    return len(_draining())


@app.on_event("shutdown")
async def _memory_down():
    for t in _FUEL:
        t.cancel()
    _FUEL.clear()
    await chains.stop()
    await tpims.stop()
    await parking_store.stop()
    left = await drain()
    if left:
        logger.warning("shutdown: %d background tasks still running after %.0f s", left, DRAIN_S)
    await spend.close()  # the last counters, before the store closes
    await leader.close()
    await memory.close()


@app.exception_handler(PlacesUnavailable)
async def places_unavailable(_request, _exc):
    return JSONResponse(status_code=502, content={"detail": "Places unavailable right now"})

app.add_middleware(
    CORSMiddleware,
    allow_credentials=False,  # Bearer tokens only, no cookies
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id"],
)
app.add_middleware(i18n.LangMiddleware)  # X-Lang: the driver's language for every endpoint (services/i18n.py)
app.add_middleware(reqid.RequestIdMiddleware)  # outermost: the id is set before anything else runs
