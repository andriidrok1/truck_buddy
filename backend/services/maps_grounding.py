"""What drivers say about a place, from Gemini with Grounding with Google Maps (reviews included).

Why this and not Google Places reviews: the Places terms forbid speaking Google content and storing reviews, and
the copilot is a voice product (2026-11-03). Grounding with Google Maps is Google's sanctioned way to answer from
Maps data, under the Gemini API Additional Terms (read 2026-11-04):

- the answer ("Maps Grounded Result") is shown to the driver who asked, unmodified, nothing mixed into it, with its
  Maps links right after it, attributed to "Google Maps" (AgentResponse.maps_sources; the app renders them under
  the reply);
- it may stay in that driver's chat history up to six months; no other caching (so no shared per-place cache:
  every question is a billable Maps query, $14 per 1 000 after 5 000 free a month on Gemini 3+ models);
- the terms say nothing about voice; the copilot speaks the text exactly as returned.

English only (the tool's limit). Off unless GEMINI_API_KEY is set and MAPS_GROUNDING is on (default on when the
key is there); any failure falls back to services.reviews (the star rating, or "no reviews").
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from models import Place
from services import spend
from services.flags import on
from services.http import client

logger = logging.getLogger("truckbuddy.maps_grounding")

URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
API_REVISION = "2026-05-20"
DEFAULT_MODEL = "gemini-3.5-flash-lite"  # cheapest model with the Maps tool (docs 2026-11-04)
TIMEOUT_S = 8.0
PROMPT = ("A truck driver is asking what other drivers and reviewers say about this place: {name}{address}. "
          "Answer about this one place only, in at most two short spoken sentences, plain text: truck parking, "
          "showers, safety, staff or food, whatever reviewers mention most, and one clear warning if there is one. "
          "If you cannot find this place, say so in one sentence.")


@dataclass
class Grounded:
    text: str
    sources: list[dict] = field(default_factory=list)  # [{"name": ..., "url": ...}]


def enabled() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY")) and on("MAPS_GROUNDING", "1")


def model() -> str:
    return os.environ.get("GEMINI_MAPS_MODEL") or DEFAULT_MODEL


def _get(obj, *names, default=None):
    """A field by its snake_case or camelCase name (the REST shape of the Interactions API is not pinned here)."""
    if not isinstance(obj, dict):
        return default
    for n in names:
        if n in obj:
            return obj[n]
    return default


def parse(body: dict) -> Optional[Grounded]:
    """Interactions API response -> the model's text and its place citations, or None without text.

    Shape per the docs' samples: steps[] with type "model_output", each with content[] blocks of type "text"
    (text, annotations[] of type "place_citation" with name and url)."""
    texts: list[str] = []
    sources: list[dict] = []
    for step in _get(body, "steps", default=[]) or []:
        if _get(step, "type") != "model_output":
            continue
        for block in _get(step, "content", default=[]) or []:
            if _get(block, "type") != "text":
                continue
            if _get(block, "text"):
                texts.append(str(block["text"]).strip())
            for a in _get(block, "annotations", default=[]) or []:
                if _get(a, "type") == "place_citation" and _get(a, "url"):
                    src = {"name": str(_get(a, "name", default="") or "Google Maps"), "url": str(a["url"])}
                    if src not in sources:
                        sources.append(src)
    text = " ".join(t for t in texts if t)
    return Grounded(text, sources) if text else None


async def drivers_say(place: Place) -> Optional[Grounded]:
    """The grounded answer for one place, or None (off, no key, timeout, no text, no Maps source)."""
    if not enabled() or not spend.allow_call("gemini"):  # off past the spend stage or the call cap (services/spend.py)
        return None
    address = f", {place.address}" if place.address else ""
    body = {"model": model(), "input": PROMPT.format(name=place.name, address=address),
            "tools": [{"type": "google_maps", "latitude": place.lat, "longitude": place.lng}]}
    headers = {"x-goog-api-key": os.environ["GEMINI_API_KEY"], "Api-Revision": API_REVISION}
    try:
        res = await client().post(URL, json=body, headers=headers, timeout=TIMEOUT_S)
        res.raise_for_status()
        out = parse(res.json())
    except Exception as e:  # noqa: BLE001 - the caller falls back to the rating
        logger.warning("maps grounding failed for %s: %s", place.id, e)
        return None
    if out is None or not out.sources:
        # Without a Maps source the text is the model's own words, not a grounded result: not worth saying.
        logger.info("maps grounding: no grounded answer for %s", place.id)
        return None
    return out
