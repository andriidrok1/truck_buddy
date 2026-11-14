"""POST /api/parking/answer: spoken parking answer for a place (waits up to wait_s for the check)."""
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from models import ParkingAnswerRequest, ParkingCheck
from services import approach, i18n, voice

router = APIRouter(prefix="/api")


class ParkingAnswer(BaseModel):
    status: str  # ready | pending
    reply: str
    audio_url: Optional[str] = None
    parking: Optional[ParkingCheck] = None


@router.post("/parking/answer", response_model=ParkingAnswer)
async def parking_answer(req: ParkingAnswerRequest):
    status, reply, check = await approach.answer(req.place, req.wait_s)
    # The driver's language (X-Lang): the spoken reply and the card, like /parking. Both stayed English here.
    reply = await i18n.localize(reply)
    check = await i18n.localize_parking(check)
    # The app asks again for the same place (a retry, the late answer): one clip per text, none past the day's cap.
    audio_id = await voice.synthesize_same(reply)
    return ParkingAnswer(status=status, reply=reply, parking=check,
                         audio_url=f"/api/voice/audio/{audio_id}" if audio_id else None)
