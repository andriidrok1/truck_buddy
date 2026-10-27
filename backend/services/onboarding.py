"""First-run onboarding: turn a spoken answer into profile fields (name, truck, preferences)."""
import json
import logging
import re
from typing import Literal

from services import llm


logger = logging.getLogger("truckbuddy.onboarding")

Step = Literal["name", "truck", "prefs"]
CHAINS = ["Pilot", "Flying J", "Love's", "TA", "Petro", "Sapp Bros", "Kwik Trip", "Buc-ee's"]

PROMPTS = {
    "name": 'Extract the driver\'s first name. JSON: {"driver_name": "<name or empty>"}',
    "truck": (
        "Extract truck dimensions. Convert to: height_in (total inches, e.g. 13 ft 6 in = 162), trailer_ft (trailer length "
        "in feet), weight_lb (gross weight in pounds, '80 thousand' = 80000), hazmat (true/false). Include only fields the "
        'driver mentioned. JSON: {"height_in": int, "trailer_ft": int, "weight_lb": int, "hazmat": bool}'
    ),
    "prefs": (
        "Extract route preferences. Fields: avoid_tolls (bool), avoid_ferries_tunnels (bool), favorite_chains (list, use "
        f"these spellings when they match: {', '.join(CHAINS)}), break_hours (int hours between break reminders). "
        "Include only what the driver mentioned. If they say none/no preferences, return {}. JSON only."
    ),
}

LIMITS = {"height_in": (60, 203), "trailer_ft": (10, 65), "weight_lb": (5000, 200000), "break_hours": (1, 14)}


def _clean(data: dict) -> dict:
    out = {}
    for k, v in data.items():
        if k in LIMITS:
            try:
                v = int(v)
            except (TypeError, ValueError):
                continue
            lo, hi = LIMITS[k]
            if lo <= v <= hi:
                out[k] = v
        elif k in ("hazmat", "avoid_tolls", "avoid_ferries_tunnels") and isinstance(v, bool):
            out[k] = v
        elif k == "favorite_chains" and isinstance(v, list):
            out[k] = [str(c).strip()[:30] for c in v if str(c).strip()][:8]
        elif k == "driver_name" and isinstance(v, str) and v.strip():
            out[k] = v.strip().split()[0][:40].title()
    return out


async def extract(step: Step, text: str) -> dict:
    if not re.search(r"[a-zA-Z0-9]{2,}", text or ""):
        return {}
    try:
        raw = await llm.complete("You extract structured data from a truck driver's spoken answer. Reply with JSON only.",
                                 f"{PROMPTS[step]}\n\nDriver said: {text}")
        m = re.search(r"\{.*\}", raw or "", re.S)
        return _clean(json.loads(m.group(0)) if m else {})
    except Exception:
        logger.exception("onboarding extract failed")
        return {}
