import asyncio
import json
import re
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from typing import Literal, Optional

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

from models import AgentRequest, AgentResponse, Category, ParkingCheck, ParkingRequest, Place, PlacesResponse, RouteRequest, RouteResponse, TruckProfile  # noqa: E402
from services.agent import get_agent_provider  # noqa: E402
from services.google_places import PlacesUnavailable  # noqa: E402
from services.parking import check_parking, fetch_satellite  # noqa: E402
from services.places import get_places_provider  # noqa: E402
from services.routing import get_routing_provider  # noqa: E402
from services import onboarding, timing, voice  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("truckbuddy")

app = FastAPI(
    title="TruckBuddy API",
    version="0.1.0",
    description="Voice copilot for truckers. Phase 1 MVP: places, routing and agent are MOCK providers.",
    openapi_url="/api/openapi.json",
    docs_url="/api/docs",
    redoc_url=None,
)
api = APIRouter(prefix="/api")


@api.get("/health")
async def health():
    return {
        "ok": True,
        "providers": {
            "places": get_places_provider().source,
            "routing": get_routing_provider().source,
            "agent": get_agent_provider().source,
            "tts": voice.provider(),
        },
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
    route_task = get_routing_provider().route(req.lat, req.lng, place.lat, place.lng, req.truck_profile)
    if place.category == "destination":  # addresses/cities: no parking check
        return RouteResponse(place=place, route=await route_task, parking=None)
    route, parking = await asyncio.gather(route_task, check_parking(place))
    return RouteResponse(place=place, route=route, parking=parking)


@api.post("/parking", response_model=ParkingCheck)
async def parking_check(req: ParkingRequest):
    return await check_parking(req.place)


@api.get("/satellite")
async def satellite(lat: float = Query(..., ge=-90, le=90), lng: float = Query(..., ge=-180, le=180)):
    try:
        img = await fetch_satellite(lat, lng)
    except Exception:
        raise HTTPException(status_code=502, detail="Satellite image unavailable")
    return Response(content=img, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


SENTENCE_END = re.compile(r"(?<!\bSt\.)(?<!\bDr\.)(?<!U\.S\.)(?<=[.!?])\s+(?=[A-Z])")


async def _run_agent(req: AgentRequest, speak: bool) -> AgentResponse:
    res = await get_agent_provider().handle(req)
    logger.info("agent text=%r status=%s place=%s", req.text, res.status, res.place.name if res.place else None)
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
async def agent(req: AgentRequest, speak: bool = Query(False)):
    timing.begin()
    return _finish(await _run_agent(req, speak))


def _bg(coro) -> None:
    t = asyncio.create_task(coro)
    _BG.add(t)
    t.add_done_callback(_BG.discard)


_BG: set = set()
WARM_TOP = 1  # satellite check only for the first place of each category


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
async def warm(lat: float = Query(..., ge=-90, le=90), lng: float = Query(..., ge=-180, le=180)):
    """Pre-fetch Places for all categories (+ satellite checks for the top 2) around the driver."""
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
    return _finish(await _run_agent(req, speak=True))


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
            task = asyncio.create_task(voice.synthesize(req.text, req.voice, req.model,
                                                        eleven_voice_id=req.eleven_voice_id))
            _SPEAK_PENDING[key] = task
            task.add_done_callback(lambda _t: _SPEAK_PENDING.pop(key, None))
        audio_id = await asyncio.shield(task)
        if not audio_id:
            raise HTTPException(status_code=502, detail="Voice unavailable")
        audio_id = voice.pin(audio_id, key)  # fixed lines survive cache eviction and restarts
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


@api.get("/voice/audio/{audio_id}")
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


app.include_router(api)


@app.exception_handler(PlacesUnavailable)
async def places_unavailable(_request, _exc):
    return JSONResponse(status_code=502, content={"detail": "Places unavailable right now"})

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
