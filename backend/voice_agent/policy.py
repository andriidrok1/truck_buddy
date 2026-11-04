"""Barge-in and end-of-turn rules for duplex voice, kept apart from LiveKit so they can be tested as a table.

Starting values from the plan (TruckBuddy — План голосовой системы, 2026-11-03); the audio rig tunes them.
"""
from __future__ import annotations

import re

MIN_ENDPOINT_S = 0.4  # pause after which the end-of-turn model is asked whether the thought is finished
MAX_ENDPOINT_S = 3.0  # an unfinished thought ("I need to find a...") waits at most this long
MIN_INTERRUPT_S = 0.5  # speech over the agent shorter than this never stops it
MIN_INTERRUPT_WORDS = 2  # nor does speech with fewer words (a cough, the radio, "uh-huh")
FALSE_INTERRUPT_S = 1.5  # after a stop with no words, the agent picks up where it was
FILLER_AFTER_S = 0.7  # the agent hasn't answered by then: a short "Hang on." first
FILLERS = ("Hang on.", "On it.", "Checking.", "Okay, one sec.")  # the short end of the app's list (useFillers.ts)

BACKCHANNEL = {"uh-huh", "uh huh", "mhm", "mm-hmm", "mm", "hmm", "yeah", "yep", "ok", "okay", "right", "sure",
               "got it", "alright", "all right", "uh", "um"}
_WORD = re.compile(r"[a-z']+(?:-[a-z']+)?")


def words(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


def is_backchannel(text: str) -> bool:
    """Only acknowledgement sounds: the driver is listening, not taking the turn."""
    t = " ".join(words(text))
    if not t:
        return True
    if t in BACKCHANNEL:
        return True
    return all(w in BACKCHANNEL for w in t.split())


def should_interrupt(heard: str, agent_text: str) -> bool:
    """Speech over the agent stops it when it has enough real words. After a question any word is an answer
    ("yeah" to "Want me to route you there?")."""
    w = words(heard)
    if agent_text.rstrip().endswith("?"):
        return len(w) >= 1
    return len(w) >= MIN_INTERRUPT_WORDS and not is_backchannel(heard)
