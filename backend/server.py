import asyncio
import json
import re
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from typing import Literal, Optional

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

from models import AgentRequest, AgentResponse, Category, ParkingCheck, ParkingRequest, Place, PlacesResponse, RouteRequest, RouteResponse, TruckProfile  # noqa: E402
from services.agent import get_agent_provider  # noqa: E402
from services import breakdown  # noqa: E402
from services.google_places import PlacesUnavailable  # noqa: E402
from services.parking import cached_parking, check_parking, fetch_satellite  # noqa: E402
from services.places import get_places_provider  # noqa: E402
from services.routing import RoutingUnavailable, get_routing_provider  # noqa: E402
from services import approach, approach_api, auth, memory, onboarding, parking_store, parking_warm, timing, voice  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("truckbuddy")

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


class AccessCodeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=64)


@public.post("/auth/access-code")
async def access_code(req: AccessCodeRequest, request: Request):
    token, role = auth.redeem(req.code, request)
    return {"access_token": token, "token_type": "bearer", "role": role}


@public.post("/auth/device")
async def auth_device(request: Request):
    return {"access_token": auth.register_device(request), "token_type": "bearer", "role": "driver"}


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
    agent_sessions_drop(driver)
    try:
        return {"deleted": await asyncio.wait_for(memory.clear(driver), MEMORY_WAIT_S)}
    except Exception as e:  # noqa: BLE001
        logger.warning("memory clear failed: %s", e)
        raise HTTPException(status_code=503, detail="Memory is unavailable right now")


def agent_sessions_drop(driver: str) -> None:
    """Forget the in-process dialog of this driver too (its id prefixes the session ids)."""
    from services.agent import _SESSIONS
    for sid in [s for s in _SESSIONS if s.startswith(driver)]:
        _SESSIONS.pop(sid, None)


@public.get("/health")
async def health():
    return {
        "ok": True,
        "providers": {
            "places": get_places_provider().source,
            "routing": get_routing_provider().source,
            "agent": get_agent_provider().source,
            "tts": voice.provider(),
        },
        "parking_cache": parking_store.stats(),
    }


@api.get("/places", response_model=PlacesResponse)
async def search_places(
    category: Category = Query(...),
    lat: float = Query(38.5816, ge=-90, le=90),
    lng: float = Query(-121.4944, ge=-180, le=180),
):
    return await get_places_provider().search(category, lat, lng)


@api.post("/route", response_model=RouteResponse)
async def build_route(req: RouteRequest):
    place = req.place
    if place is None and req.place_id:
        place = await get_places_provider().get(req.place_id, req.lat, req.lng)
    if place is None:
        raise HTTPException(status_code=404, detail="Place not found")
    _require_us(req.lat, req.lng)
    _require_us(place.lat, place.lng)
    try:
        route = await get_routing_provider().route(req.lat, req.lng, *approach.target(place), req.truck_profile)
    except RoutingUnavailable:
        raise HTTPException(status_code=503, detail="Truck routing is unavailable right now")
    if req.skip_parking:  # breakdown providers: the driver needs the shop, no parking check at all
        return RouteResponse(place=place, route=route, parking=None)
    approach.background(check_parking(place, route.approach))  # don't wait for the satellite; app re-asks /parking
    return RouteResponse(place=place, route=route, parking=cached_parking(place))


@api.post("/parking", response_model=ParkingCheck)
async def parking_check(req: ParkingRequest):
    _require_us(req.place.lat, req.place.lng)  # each new coordinate is a satellite fetch + a vision call
    return await check_parking(req.place, req.approach)


@api.get("/satellite")  # behind the token: the thumbnail is loaded with the auth header
async def satellite(lat: float = Query(..., ge=-90, le=90), lng: float = Query(..., ge=-180, le=180)):
    try:
        img = await fetch_satellite(lat, lng)
    except Exception:
        raise HTTPException(status_code=502, detail="Satellite image unavailable")
    return Response(content=img, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


SENTENCE_END = re.compile(r"(?<!\bSt\.)(?<!\bDr\.)(?<!U\.S\.)(?<=[.!?])\s+(?=[A-Z])")


async def _run_agent(req: AgentRequest, speak: bool, driver: Optional[str] = None) -> AgentResponse:
    if driver and req.session_id:
        req.session_id = f"{driver}:{req.session_id}"[:64]  # sessions can't be shared across drivers
    res = await get_agent_provider().handle(req, driver)
    logger.info("agent text=%r status=%s place=%s", req.text, res.status, res.place.name if res.place else None)
    return await _speak(res, speak)


async def _speak(res: AgentResponse, speak: bool) -> AgentResponse:
    if speak and res.reply:
        # First sentence is spoken as soon as it's ready; the rest is generated in parallel.
        first, *rest = SENTENCE_END.split(res.reply.strip(), maxsplit=1)
        if rest:
            res.audio_url_2 = f"/api/voice/audio/{voice.synthesize_later(rest[0], previous_text=first)}"
        with timing.stage("tts"):
            audio_id = await voice.synthesize(first, next_text=rest[0] if rest else None)
        if audio_id:
            res.audio_url = f"/api/voice/audio/{audio_id}"
    return res


def _finish(res: AgentResponse, extra: Optional[dict] = None) -> AgentResponse:
    res.timings = {**(extra or {}), **timing.current()}
    res.timings["total"] = sum(v for k, v in res.timings.items() if k != "total")
    logger.info("voice timings %s", " ".join(f"{k}={v}" for k, v in res.timings.items()))
    return res


@api.post("/agent", response_model=AgentResponse)
async def agent(req: AgentRequest, speak: bool = Query(False), driver: str = Depends(auth.require_driver)):
    timing.begin()
    return _finish(await _run_agent(req, speak, driver))


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
    driver: str = Depends(auth.require_driver),
):
    timing.begin()
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
    name = audio.filename or "speech.m4a"
    if "." not in name:
        name += ".webm" if "webm" in (audio.content_type or "") else ".m4a"
    try:
        with timing.stage("stt"):
            text = await voice.transcribe(data, name)
    except Exception:
        logger.exception("transcription failed")
        text = ""
    logger.info("voice transcript=%r", text)
    req = AgentRequest(text=text, lat=lat, lng=lng, active_place=current, truck_profile=truck, session_id=session_id,
                       active_distance_mi=active_distance_mi, active_duration_min=active_duration_min)
    return _finish(await _run_agent(req, speak=True, driver=driver))


@api.post("/voice/transcribe")
async def voice_transcribe(audio: UploadFile = File(...)):
    """Step 1 of a voice turn: speech -> text (the app shows it right away, then calls /agent?speak=true)."""
    timing.begin()
    with timing.stage("upload"):
        data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty audio")
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Audio too long")
    name = audio.filename or "speech.m4a"
    if "." not in name:
        name += ".webm" if "webm" in (audio.content_type or "") else ".m4a"
    try:
        with timing.stage("stt"):
            text = await voice.transcribe(data, name)
    except voice.SttTimeout as e:
        logger.warning("transcription timed out: %s", e)
        raise HTTPException(status_code=504, detail="Speech recognition timed out")
    except Exception:
        logger.exception("transcription failed")
        text = ""
    t = timing.current()
    logger.info("voice transcribe bytes=%d transcript=%r %s", len(data), text, " ".join(f"{k}={v}" for k, v in t.items()))
    return {"transcript": text, "timings": t}


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
    if step not in ("name", "truck", "prefs"):
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
    logger.info("onboarding step=%s transcript=%r updates=%s", step, text, updates)
    return {"transcript": text, "updates": updates}


_RANGE = re.compile(r"bytes=(\d*)-(\d*)")


@public.get("/voice/audio/{audio_id}")
async def voice_audio(audio_id: str, request: Request):
    """mp3 with byte-range support (AVPlayer on iOS probes with Range: bytes=0-1)."""
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


app.include_router(public)
app.include_router(api)
app.include_router(approach_api.router, dependencies=[Depends(auth.rate_limited)])
app.include_router(parking_warm.router)


@app.on_event("startup")
async def _memory_up():
    _bg(memory.warm())


@app.on_event("shutdown")
async def _memory_down():
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
)
