"""What drivers say about a place: a one-sentence spoken gist of its Google reviews.

The reviews themselves stay on the Place for the app (author + link, as Google's terms want);
the voice gets a summary, cached per place for a day so repeat questions cost no LLM call.
"""
import logging
import time
from collections import OrderedDict
from typing import Optional

from models import Place
from services import llm

logger = logging.getLogger("truckbuddy.reviews")

GIST_TTL_S = 24 * 3600
GIST_MAX = 500
SAY = "Drivers say: {gist}"
NO_REVIEWS = "No driver reviews there yet."
RATING_ONLY = "Drivers rate it {rating:.1f} out of 5, but I can't read the reviews right now."
PROMPT = ("You summarise Google reviews of a truck stop, hotel or rest area for a truck driver who is driving. "
          "Answer with ONE spoken sentence, at most 25 words, plain text, no quotes, no author names, no star "
          "numbers. Lead with the point reviewers agree on most (parking, showers, fuel lanes, staff, food, "
          "safety, cleanliness); mention one clear warning if there is one.")
_gists: "OrderedDict[str, tuple[float, str]]" = OrderedDict()


def _cached(place_id: str, now: float) -> Optional[str]:
    hit = _gists.get(place_id)
    if hit and now - hit[0] < GIST_TTL_S:
        _gists.move_to_end(place_id)
        return hit[1]
    return None


async def gist(place: Place, now: Optional[float] = None) -> Optional[str]:
    """One sentence from the place's reviews, or None when there are none or the model is down."""
    if not place.reviews:
        return None
    now = time.monotonic() if now is None else now
    hit = _cached(place.id, now)
    if hit:
        return hit
    lines = "\n".join(f"- ({r.rating or '?'} stars) {r.text}" for r in place.reviews)
    try:
        text = (await llm.complete(PROMPT, f"Place: {place.name}\nReviews:\n{lines}")).strip().strip('"')
    except Exception as e:  # noqa: BLE001 - the card still shows the reviews; voice just skips the gist
        logger.warning("review gist failed for %s: %s", place.id, e)
        return None
    text = " ".join(text.split())
    if not text:
        return None
    if not text.endswith((".", "!", "?")):
        text += "."
    _gists[place.id] = (now, text)
    while len(_gists) > GIST_MAX:
        _gists.popitem(last=False)
    return text


async def speech(place: Place) -> str:
    """The spoken answer to "what do drivers say about it?"."""
    g = await gist(place)
    if g:
        return SAY.format(gist=g)
    if place.reviews and place.rating:
        return RATING_ONLY.format(rating=place.rating)
    return NO_REVIEWS
