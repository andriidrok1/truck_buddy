"""What the copilot knows about a driver, as a handful of facts instead of forty raw lines.

Every ~20 turns (or once a day) the driver's own lines from memory are condensed by the model into
preferences: liked and avoided chains, what they avoid on the road, where they stop for the night,
how they like their answers. The facts go into the prompt context, feed place choice, can be read
back ("what do you know about me?") and erased ("forget that"). PROFILE_LEARN=0 turns it off.
"""
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from services import llm, memory
from services.flags import on

logger = logging.getLogger("truckbuddy.profile")

REFRESH_TURNS = 20
REFRESH_AGE = timedelta(hours=24)
MAX_LINES = 200
KEYS = ("chains_liked", "chains_avoided", "avoids", "stops_for_night_at", "style", "notes")
LIST_KEYS = ("chains_liked", "chains_avoided", "avoids", "notes")
MAX_ITEMS = 6
PROMPT = (
    "You read what a truck driver said to a voice copilot over several trips and write down the driver's "
    "preferences as JSON, nothing else. Keys: chains_liked (truck stop chains they prefer), chains_avoided "
    "(chains they rejected or complained about), avoids (things on the road: tolls, ferries, downtown, night "
    "driving), stops_for_night_at (\"truck stops\", \"rest areas\", \"Walmart\" or null), style (\"short\" when "
    "they want brief answers, \"chatty\" when they chat, else null), notes (up to 3 other durable facts, each under "
    "10 words). Only what the driver actually said; empty lists and nulls when unsure. No names of people."
)
KNOW = "Here's what I know: {facts}."
KNOW_NOTHING = "I don't know anything about you yet. Tell me what you like and I'll remember."
# Before the facts exist (fewer than 5 turns), a memory question is answered from the raw lines instead.
RECALL_PROMPT = (
    "You are TruckBuddy, a truck driver's voice copilot. Answer the driver's question in one short spoken "
    "sentence using only what the driver said in earlier conversations (listed, oldest first). If the answer "
    "is not there, say you don't know that yet. No preamble."
)
RECALL_TIMEOUT_S = 4.0
FORGOT = "Done, I've forgotten what I'd learned about you."
LEARNED_AVOID = " Got it, I'll stop suggesting {chain}."


def parse_facts(raw: str) -> Optional[dict]:
    """Model output -> facts dict with only the known keys, lists capped; None when there is no JSON."""
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    out: dict = {}
    for k in KEYS:
        v = data.get(k)
        if k in LIST_KEYS:
            items = [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []
            out[k] = list(dict.fromkeys(items))[:MAX_ITEMS]
        else:
            out[k] = str(v).strip()[:60] if isinstance(v, str) and v.strip() else None
    return out


def is_empty(facts: Optional[dict]) -> bool:
    return not facts or not any(facts.get(k) for k in KEYS)


def facts_line(facts: Optional[dict]) -> str:
    """One sentence for the prompt context and for "what do you know about me?"."""
    if is_empty(facts):
        return ""
    parts = []
    if facts.get("chains_liked"):
        parts.append("likes " + ", ".join(facts["chains_liked"]))
    if facts.get("chains_avoided"):
        parts.append("avoids " + ", ".join(facts["chains_avoided"]))
    if facts.get("avoids"):
        parts.append("avoids " + ", ".join(facts["avoids"]))
    if facts.get("stops_for_night_at"):
        parts.append(f"stops for the night at {facts['stops_for_night_at']}")
    if facts.get("style") == "short":
        parts.append("likes short answers")
    for n in facts.get("notes") or []:
        parts.append(n)
    return "; ".join(parts)


async def summarize(driver_lines: list[str]) -> Optional[dict]:
    lines = [ln.strip()[:200] for ln in driver_lines if ln and ln.strip()][-MAX_LINES:]
    if not lines:
        return None
    try:
        raw = await llm.complete(PROMPT, "Driver said, oldest first:\n" + "\n".join(f"- {ln}" for ln in lines))
    except Exception as e:  # noqa: BLE001
        logger.warning("profile summary failed: %s", type(e).__name__)
        return None
    return parse_facts(raw)


def _due(stored: Optional[dict], turns_now: int) -> bool:
    if not stored:
        return turns_now >= 5  # don't summarise a two-line history
    seen = int(stored.get("turns_seen") or 0)
    updated = stored.get("updated_at")
    stale = updated is None or (datetime.now(timezone.utc) - updated) > REFRESH_AGE
    return turns_now - seen >= REFRESH_TURNS or (stale and turns_now > seen)


async def refresh_if_due(driver: str) -> Optional[dict]:
    """Recompute the facts when enough new turns have accumulated; returns the facts in force."""
    if not on("PROFILE_LEARN"):
        return None
    try:
        stored = await memory.get_facts(driver)
        turns_now = await memory.count_turns(driver)
        if not _due(stored, turns_now):
            return stored["facts"] if stored else None
        past = await memory.recent(driver, MAX_LINES * 2)
        facts = await summarize([t["text"] for t in past if t["role"] == "driver"])
        if facts is None:
            return stored["facts"] if stored else None
        await memory.set_facts(driver, facts, turns_now)
        return facts
    except Exception as e:  # noqa: BLE001 - learning is best effort
        logger.warning("profile refresh failed: %s", type(e).__name__)
        return None


async def recall(question: str, past: list[str]) -> Optional[str]:
    """"What's my dog's name?" with no learned facts yet: one short answer from the driver's earlier lines."""
    lines = [ln.strip()[:200] for ln in past if ln and ln.strip()][-MAX_LINES:]
    if not lines or not question.strip():
        return None
    try:
        import asyncio
        raw = await asyncio.wait_for(
            llm.complete(RECALL_PROMPT, "Driver said earlier:\n" + "\n".join(f"- {ln}" for ln in lines)
                         + f"\nQuestion: {question.strip()}"), RECALL_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 - fall back to "nothing yet"
        logger.info("recall failed: %s", type(e).__name__)
        return None
    text = " ".join(str(raw or "").split())
    return text[:240] or None


async def learn_avoid(driver: Optional[str], facts: dict, chain: str) -> dict:
    """Add a chain to chains_avoided (from three rejections in one dialog) and persist it."""
    facts = dict(facts or {k: ([] if k in LIST_KEYS else None) for k in KEYS})
    avoided = [c for c in facts.get("chains_avoided") or []]
    if chain.lower() not in [c.lower() for c in avoided]:
        avoided.append(chain)
    facts["chains_avoided"] = avoided[:MAX_ITEMS]
    facts["chains_liked"] = [c for c in facts.get("chains_liked") or [] if c.lower() != chain.lower()]
    if driver:
        try:
            stored = await memory.get_facts(driver)
            await memory.set_facts(driver, facts, int((stored or {}).get("turns_seen") or 0))
        except Exception as e:  # noqa: BLE001
            logger.warning("profile learn_avoid failed: %s", type(e).__name__)
    return facts
