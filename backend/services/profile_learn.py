"""What the copilot knows about a driver, as a handful of facts instead of forty raw lines.

Every ~20 turns (or once a day) the driver's own lines from memory are condensed by the model into
preferences: liked and avoided chains, what they avoid on the road, where they stop for the night,
how they like their answers. The facts go into the prompt context, feed place choice, can be read
back ("what do you know about me?") and erased ("forget that"). PROFILE_LEARN=0 turns it off.
"""
import asyncio
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
KEYS = ("chains_liked", "chains_avoided", "places_liked", "places_avoided", "avoids", "stops_for_night_at", "style",
        "notes")
LIST_KEYS = ("chains_liked", "chains_avoided", "places_liked", "places_avoided", "avoids", "notes")
MAX_ITEMS = 6
PROMPT = (
    "You read what a truck driver said to a voice copilot over several trips and write down the driver's "
    "preferences as JSON, nothing else. Keys: chains_liked (truck stop chains, brands with many locations, the "
    "driver SAID they like or prefer), chains_avoided (chains they said they dislike or avoid), places_liked (one "
    "particular place they said they like), places_avoided (particular places they said they dislike), avoids "
    "(things on the road: tolls, ferries, downtown, night driving), stops_for_night_at (\"truck stops\", \"rest "
    "areas\", \"Walmart\" or null), style (\"short\" when they want brief answers, \"chatty\" when they chat, else "
    "null), notes (up to 3 other durable facts about the driver, the truck or the load, each under 10 words, "
    "written to the driver: \"you haul reefer\"). A place the driver asked for or was sent to is not a like, and a "
    "request (\"find a Walmart\", \"is there a CAT scale\") is not a note. Only what the driver actually said; "
    "empty lists and nulls when unsure. No names of people."
)
# Truck stop chains by the words of their name (generic words like "travel center" left out), spelled the usual way.
CHAINS = {"pilot": "Pilot", "flying j": "Flying J", "pilot flying j": "Pilot Flying J", "love's": "Love's",
          "loves": "Love's", "ta": "TA", "travelcenters of america": "TA", "petro": "Petro", "sapp bros": "Sapp Bros",
          "roady's": "Roady's", "roadys": "Roady's", "ambest": "AmBest", "kwik trip": "Kwik Trip",
          "buc-ee's": "Buc-ee's", "maverik": "Maverik", "speedway": "Speedway", "casey's": "Casey's",
          "town pump": "Town Pump", "little america": "Little America"}
_GENERIC = {"the", "a", "truck", "stop", "stops", "travel", "center", "centers", "centre", "plaza", "plazas",
            "station", "stations"}
# What the old prompt wrote as notes: the driver's requests, not facts about the driver (QA 2026-11-02, bug 5).
REQUEST_NOTE = re.compile(r"^\s*(?:asked|asks|wants|wanted|looking|looked|needs|needed|requested|searched|"
                          r"searching|inquired|checked)\b", re.I)
# A like counts only when the driver said it: "I like Pilot", "Love's is my go-to", "my favorite is the 49er".
LIKE_SAID = re.compile(r"\bi\s+(?:really\s+|do\s+)?(?:like|love|prefer)\b|\bfavou?rite\b|\bgo-?to\b", re.I)
KNOW = "Here's what I know. {facts}"
PREFS = "From your profile: {prefs}."


def prefs_line(truck) -> str:
    """"you like Pilot and Love's, you avoid toll roads": what the driver set in the profile (or said and the
    app saved there). "What do you know about me" never mentioned the profile (QA 2026-10-31, H19)."""
    parts = []
    chains = [c for c in (getattr(truck, "favorite_chains", None) or []) if c]
    if chains:
        parts.append("you like " + (" and ".join(chains) if len(chains) <= 2 else ", ".join(chains[:-1]) + f" and {chains[-1]}"))
    if getattr(truck, "avoid_tolls", False):
        parts.append("you avoid toll roads")
    if getattr(truck, "avoid_ferries_tunnels", False):
        parts.append("you avoid ferries and tunnels")
    return PREFS.format(prefs=", ".join(parts)) if parts else ""


KNOW_NOTHING = "I don't know anything about you yet. Tell me what you like and I'll remember."
# Before the facts exist (fewer than 5 turns), a memory question is answered from the raw lines instead.
RECALL_PROMPT = (
    "You are TruckBuddy, a truck driver's voice copilot. Answer the driver's question in one short spoken "
    "sentence using only what the driver said in earlier conversations (listed, oldest first). If the answer "
    "is not there, say you don't know that yet. No preamble. One exception: when the question is general "
    "(\"what do you know about me\", \"what have you learned\") and the lines hold nothing personal and no "
    "preference, only requests for places, routes, fuel or weather, reply with exactly NONE."
)
RECALL_NONE = "NONE"  # the model's "nothing worth repeating": the caller falls back to KNOW_NOTHING
RECALL_TIMEOUT_S = 4.0
SAVE_TIMEOUT_S = 3.0
FORGOT = "Done, I've forgotten what I'd learned about you."
REMEMBERED = "Got it, I'll remember {what}."
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
    return tidy(out)


def chain_name(item: str) -> Optional[str]:
    """"Love's Travel Stops" -> "Love's"; None for a particular place ("Pilot Travel Center, Fernley", "the 49er")."""
    words = [w for w in re.findall(r"[a-z0-9'-]+", (item or "").lower().replace("\u2019", "'")) if w not in _GENERIC]
    return CHAINS.get(" ".join(words))


def _dedupe(items) -> list[str]:
    seen, out = set(), []
    for x in items:
        x = str(x).strip()
        if x and x.lower() not in seen:
            seen.add(x.lower())
            out.append(x)
    return out


def _blank() -> dict:
    return {k: ([] if k in LIST_KEYS else None) for k in KEYS}


def tidy(facts: Optional[dict]) -> Optional[dict]:
    """Chains apart from particular places, and no requests among the notes. A row written before places had keys
    of their own kept the stops the driver was sent to among the liked chains (QA 2026-11-02, bug 5: "likes West
    Sac Truck Stop, Sacramento 49er Travel Plaza..."): those were never likes, so they go."""
    if not facts:
        return facts
    legacy = "places_liked" not in facts and "places_avoided" not in facts
    out = {**_blank(), **{k: v for k, v in facts.items() if k in KEYS}}
    for side in ("liked", "avoided"):
        chains, places = [], list(out.get(f"places_{side}") or [])
        for item in out.get(f"chains_{side}") or []:
            chain = chain_name(item)
            if chain:
                chains.append(chain)
            elif not (legacy and side == "liked"):
                places.append(item)
        out[f"chains_{side}"] = _dedupe(chains)[-MAX_ITEMS:]
        out[f"places_{side}"] = _dedupe(places)[-MAX_ITEMS:]
    out["notes"] = [n for n in out.get("notes") or [] if not REQUEST_NOTE.match(n)]
    return out


def _said_liked(item: str, lines: list[str]) -> bool:
    """The driver said they like this one: a line with a like word and the item's own word."""
    own = {w for w in re.findall(r"[a-z0-9]+", item.lower().replace("'", "")) if w not in _GENERIC and len(w) > 1}
    for ln in lines:
        if LIKE_SAID.search(ln) and own & set(re.findall(r"[a-z0-9]+", ln.lower().replace("'", ""))):
            return True
    return False


def add(facts: Optional[dict], *, like_chains=(), like_places=(), avoid_chains=(), avoid_places=(), roads=(),
        roads_ok=(), notes=()) -> dict:
    """The facts with these added; a like takes the item off the dislikes and the other way round (the latest
    word wins)."""
    out = tidy(facts) or _blank()

    def put(key: str, other: str, items) -> None:
        for item in items:
            low = item.lower()
            out[other] = [x for x in out[other] if x.lower() != low]
            out[key] = _dedupe([x for x in out[key] if x.lower() != low] + [item])[-MAX_ITEMS:]

    put("chains_liked", "chains_avoided", like_chains)
    put("places_liked", "places_avoided", like_places)
    put("chains_avoided", "chains_liked", avoid_chains)
    put("places_avoided", "places_liked", avoid_places)
    out["avoids"] = _dedupe([a for a in out["avoids"] if a.lower() not in {r.lower() for r in roads_ok}]
                            + list(roads))[-MAX_ITEMS:]
    out["notes"] = _dedupe(list(out["notes"]) + list(notes))[-MAX_ITEMS:]
    return out


def combine(old: Optional[dict], new: dict) -> dict:
    """A fresh summary over what was stored: the summary reads only the latest lines, and dislikes learned from
    what the driver turned down never were in the lines at all, so likes and dislikes accumulate; a dislike beats a
    like of the same name."""
    old = tidy(old) or _blank()
    out = dict(new)
    for k in ("chains_liked", "places_liked", "chains_avoided", "places_avoided", "avoids"):
        out[k] = _dedupe(list(old.get(k) or []) + list(new.get(k) or []))
    avoided = {x.lower() for x in out["chains_avoided"] + out["places_avoided"]}
    for k in ("chains_liked", "places_liked"):
        out[k] = [x for x in out[k] if x.lower() not in avoided]
    for k in LIST_KEYS:
        out[k] = list(out.get(k) or [])[-MAX_ITEMS:]
    return out


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" and {items[-1]}"


def speech(facts: Optional[dict], truck=None) -> str:
    """"What do you know about me?" in sentences ("You like Love's. You'd rather skip TA."), never the raw notes.
    What the profile already says is left to prefs_line."""
    facts = tidy(facts)
    if is_empty(facts):
        return ""
    fav = {c.lower() for c in (getattr(truck, "favorite_chains", None) or [])}
    out = []
    liked = [x for x in facts["chains_liked"] + facts["places_liked"] if x.lower() not in fav]
    if liked:
        out.append(f"You like {_join(liked)}.")
    skipped = facts["chains_avoided"] + facts["places_avoided"]
    if skipped:
        out.append(f"You'd rather skip {_join(skipped)}.")
    avoids = [a for a in facts["avoids"] if not (getattr(truck, "avoid_tolls", False) and re.search(r"\btoll", a, re.I))
              and not (getattr(truck, "avoid_ferries_tunnels", False) and re.search(r"ferr|tunnel", a, re.I))]
    if avoids:
        out.append(f"You avoid {_join(avoids)}.")
    if facts.get("stops_for_night_at"):
        out.append(f"You stop for the night at {facts['stops_for_night_at']}.")
    if facts.get("style") == "short":
        out.append("You like short answers.")
    for n in facts["notes"]:
        n = n.strip().rstrip(".")
        out.append(f"{n[0].upper()}{n[1:]}.")
    return " ".join(out)


_REMEMBER = re.compile(r"\b(?:remember|keep in mind|make a note|note|don'?t forget)\b(?:\s+(?:that|this))?[,:]?\s*", re.I)
_DISLIKE = r"hate|avoid|don'?t like|do not like|can'?t stand|never (?:go to|use|stop at)|stay away from"
_LIKE = r"like|love|prefer"
_CLAUSE = re.compile(rf"^(?:i\s+)?(?:really\s+|also\s+|do\s+)?(?P<verb>{_DISLIKE}|{_LIKE})\s+(?P<what>.+)$", re.I)


def said_prefs(text: str) -> list[tuple[str, str, bool]]:
    """"Remember I like Pilot and I hate tolls" -> [("like", "Pilot", True), ("hate", "tolls", False)]:
    (the verb as said, what, liked). Empty when the words are not a like or a dislike to remember."""
    m = _REMEMBER.search(text or "")
    if not m:
        return []
    out: list[tuple[str, str, bool]] = []
    verb = None
    for part in re.split(r",|;|\.|!|\band\b|\bbut\b", text[m.end():]):
        part = re.sub(r"^\s*(?:also\s+|that\s+)*", "", part).strip()
        c = _CLAUSE.match(part)
        if c:
            verb, what = c.group("verb").lower(), c.group("what")
        elif verb and part:
            what = part  # "I like Pilot and Love's": the verb carries over
        else:
            continue
        what = re.sub(r"\s+(?:please|too|as well|a lot)$", "", what.strip(" .!?"), flags=re.I).strip()
        if what and len(what) <= 40 and len(what.split()) <= 5:
            out.append((verb, what, re.fullmatch(_LIKE, verb, re.I) is not None))
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
    if facts.get("places_liked"):
        parts.append("likes these places: " + ", ".join(facts["places_liked"]))
    if facts.get("places_avoided"):
        parts.append("turned down these places: " + ", ".join(facts["places_avoided"]))
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
    facts = parse_facts(raw)
    if facts:
        # the model still lists where the driver was sent as a like now and then: a like needs the driver's words
        for k in ("chains_liked", "places_liked"):
            facts[k] = [x for x in facts[k] if _said_liked(x, lines)]
    return facts


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
            return tidy(stored["facts"]) if stored else None
        past = await memory.recent(driver, MAX_LINES * 2)
        facts = await summarize([t["text"] for t in past if t["role"] == "driver"])
        if facts is None:
            return tidy(stored["facts"]) if stored else None
        facts = combine(stored["facts"] if stored else None, facts)
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
        raw = await asyncio.wait_for(
            llm.complete(RECALL_PROMPT, "Driver said earlier:\n" + "\n".join(f"- {ln}" for ln in lines)
                         + f"\nQuestion: {question.strip()}"), RECALL_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 - fall back to "nothing yet"
        logger.info("recall failed: %s", type(e).__name__)
        return None
    text = " ".join(str(raw or "").split())
    if text.strip(" .\"'").upper() == RECALL_NONE:
        return None  # the caller says KNOW_NOTHING instead of echoing the driver's last requests back
    return text[:240] or None


async def save(driver: Optional[str], facts: dict) -> None:
    """Persist the facts now (bounded; a slow or down store only costs the memory)."""
    if not driver:
        return
    try:
        stored = await asyncio.wait_for(memory.get_facts(driver), SAVE_TIMEOUT_S)
        await asyncio.wait_for(memory.set_facts(driver, facts, int((stored or {}).get("turns_seen") or 0)), SAVE_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001
        logger.warning("profile facts save failed: %s", type(e).__name__)


async def learn_avoid(driver: Optional[str], facts: dict, chain: str) -> dict:
    """Add a chain to chains_avoided (from three rejections in one dialog) and persist it."""
    facts = add(facts, avoid_chains=[chain])
    await save(driver, facts)
    return facts
