"""POST /api/parking/answer: spoken parking answer for a place (waits up to wait_s for the check)."""
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from models import ParkingAnswerRequest, ParkingCheck
from services import approach, voice

router = APIRouter(prefix="/api")


class ParkingAnswer(BaseModel):
    status: str  # ready | pending
    reply: str
    audio_url: Optional[str] = None
    parking: Optional[ParkingCheck] = None


@router.post("/parking/answer", response_model=ParkingAnswer)
async def parking_answer(req: ParkingAnswerRequest):
    status, reply, check = await approach.answer(req.place, req.wait_s)
    audio_id = await voice.synthesize(reply)
    return ParkingAnswer(status=status, reply=reply, parking=check,
                         audio_url=f"/api/voice/audio/{audio_id}" if audio_id else None)
