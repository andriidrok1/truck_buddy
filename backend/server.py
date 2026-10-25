import asyncio
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from typing import Optional

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Query, Response, UploadFile
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
from services import onboarding, voice  # noqa: E402
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


async def _run_agent(req: AgentRequest, speak: bool) -> AgentResponse:
    res = await get_agent_provider().handle(req)
    logger.info("agent text=%r status=%s place=%s", req.text, res.status, res.place.name if res.place else None)
    if speak and res.reply:
        audio_id = await voice.synthesize(res.reply)
        if audio_id:
            res.audio_url = f"/api/voice/audio/{audio_id}"
    return res


@api.post("/agent", response_model=AgentResponse)
async def agent(req: AgentRequest, speak: bool = Query(False)):
    return await _run_agent(req, speak)


@api.post("/voice", response_model=AgentResponse)
async def voice_turn(
    audio: UploadFile = File(...),
    lat: float = Form(..., ge=-90, le=90),
    lng: float = Form(..., ge=-180, le=180),
    active_place: Optional[str] = Form(None),  # JSON of the current destination Place
    truck_profile: Optional[str] = Form(None),  # JSON TruckProfile from the in-app editor
):
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
        text = await voice.transcribe(data, name)
    except Exception:
        logger.exception("transcription failed")
        text = ""
    logger.info("voice transcript=%r", text)
    return await _run_agent(AgentRequest(text=text, lat=lat, lng=lng, active_place=current, truck_profile=truck), speak=True)


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=600)


_SPEAK_CACHE: dict[str, str] = {}
_SPEAK_PENDING: dict[str, "asyncio.Task[Optional[str]]"] = {}


@api.post("/speak")
async def speak(req: SpeakRequest):
    """TTS for fixed copilot lines (onboarding, reminders). Cached per text, one generation per text."""
    audio_id = _SPEAK_CACHE.get(req.text)
    if not audio_id or voice.get_audio(audio_id) is None:
        task = _SPEAK_PENDING.get(req.text)
        if task is None:
            task = asyncio.create_task(voice.synthesize(req.text))
            _SPEAK_PENDING[req.text] = task
            task.add_done_callback(lambda _t: _SPEAK_PENDING.pop(req.text, None))
        audio_id = await asyncio.shield(task)
        if not audio_id:
            raise HTTPException(status_code=502, detail="Voice unavailable")
        if len(_SPEAK_CACHE) >= 200:
            _SPEAK_CACHE.pop(next(iter(_SPEAK_CACHE)))
        _SPEAK_CACHE[req.text] = audio_id
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


@api.get("/voice/audio/{audio_id}")
async def voice_audio(audio_id: str):
    data = voice.get_audio(audio_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Audio not found")
    return Response(content=data, media_type="audio/mpeg")


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
