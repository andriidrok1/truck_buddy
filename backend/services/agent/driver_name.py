"""When a reply may address the driver by name, and cutting every other address out of the model's text."""
import re
from typing import Iterable

from models import Place
from services.agent.phrases import _name_tokens

# The driver's name: the profile line asks the model for "now and then" and it said "Mike" in three replies of four
# (QA 2026-11-02). The turn decides: at most once in NAME_EVERY replies of a dialog, never in a short reply, and
# every other direct address ("Mike, ...", "..., Mike.", "Thanks Mike!") is cut out of whatever was written.
# Only a vocative is an address: the name used to go wherever it was the last word before a comma or a full stop,
# and drivers are called Austin, Jackson and Will ("heading to, 240 miles", "Sounds good, I.", review 2026-11-02).
NAME_EVERY = 4
NAME_MIN_WORDS = 6
_SENTENCE_START = r"(?:^|(?<=[.!?])\s+)"
# what opens a sentence before the name in "Thanks Mike!", "No problem, Mike, I've got it", "Okay, Rob."
_INTERJECTION = (r"(?i:hey|hi|hello|thanks|thank you|okay|ok|alright|all right|sure(?: thing)?|no problem|you bet|"
                 r"got it|roger(?: that)?|copy(?: that)?|will do|sounds good|(?:good )?(?:morning|afternoon|evening|night)|"
                 r"yes|yeah|yep|no|nope|well)")


def _address(name: str) -> re.Pattern:
    """Case-sensitive: "Will" is a name, "will" is not."""
    n = re.escape(name)
    return re.compile(rf"(?P<lead>{_SENTENCE_START}(?:(?i:hey|hi|hello),?\s+)?{n}\s*[,!.]\s*)"  # "Hey Mike, you've got..."
                      rf"|{_SENTENCE_START}{_INTERJECTION}(?P<inter>,?\s+{n})(?=\s*[,.!?])"  # "Thanks Mike!", "Okay, Rob."
                      rf"|(?P<tail>\s*,\s*{n})(?=[.!?](?:\s|$)|\s*$|\s*[\u2014\u2013]|\s+-\s)")  # "Sure thing, Mike." / "..., Mike \u2014"


def _a_place(text: str, name: str, places: Iterable[Place]) -> bool:
    """The name is a town or a word of a place: one of the dialog's ("Jackson, MS") or written as one ("Austin, TX")."""
    if re.search(rf"\b{re.escape(name)},?\s+[A-Z]{{2}}\b", text):
        return True
    word = name.lower()
    return any(word in _name_tokens(f"{p.name} {p.address or ''}") for p in places if p)


def _cut(text: str, spans: list[re.Match]) -> str:
    for m in reversed(spans):
        if m.group("lead") is not None:  # the sentence now starts with what followed the name
            rest = text[m.end():]
            rest = rest[:1].upper() + rest[1:]
            text = text[:m.start()] + (" " if m.start() > 0 else "") + rest
        else:
            g = "inter" if m.group("inter") is not None else "tail"
            text = text[:m.start(g)] + text[m.end(g):]
    return text


GREETING = re.compile(r"(?i:hey|hi|hello|thanks|thank you|good (?:morning|afternoon|evening|night))$")
SHORT_LEAD = 3  # "You're welcome, Austin." / "Doing good, Austin.": a social phrase this short before ", Name" is an address


def _vocatives(text: str, name: str, places: Iterable[Place]) -> list[re.Match]:
    """Where the reply addresses the driver. When the name is also a place here ("Austin" driving to Austin), a
    ", Austin." at the end of a sentence may be the town, so only the plain addresses go: "Hey Austin,", "Thanks
    Austin!", and ", Austin" after a phrase of at most SHORT_LEAD words. Skipping them all kept the name in every
    small-talk reply (stand 2026-11-02: three in a row)."""
    if not name or not text:
        return []
    spans = list(_address(name).finditer(text))
    if not _a_place(text, name, places):
        return spans

    def plain(m: re.Match) -> bool:
        if m.group("lead") is not None:  # "Austin, TX is 240 miles out" is the town
            return not re.match(r"[A-Z]{2}\b", text[m.end():])
        if m.group("inter") is not None:  # "Thanks Austin!" is him; "Okay, Jackson." may answer "take me to Jackson"
            return bool(GREETING.match(text[m.start():m.start("inter")].strip()))
        lead = re.split(r"[.!?]\s+", text[:m.start("tail")])[-1]
        return len(lead.split()) <= SHORT_LEAD

    return [m for m in spans if plain(m)]


def without_name(text: str, name: str, places: Iterable[Place] = ()) -> str:
    """The reply with no direct address to the driver."""
    return _cut(text, _vocatives(text, name, list(places)))


def name_policy(text: str, name: str, allowed: bool, places: Iterable[Place] = ()) -> tuple[str, bool]:
    """(the reply, whether it still addresses the driver): the first address is kept only when the dialog is due
    one and the reply is long enough to carry it; the rest go. A name that is not an address is left alone and
    does not count as one."""
    spans = _vocatives(text, name, list(places))
    if not spans:
        return text, False
    keep = allowed and len(_cut(text, spans).split()) >= NAME_MIN_WORDS
    return _cut(text, spans[1:] if keep else spans), keep
