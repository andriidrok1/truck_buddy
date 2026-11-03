"""Breakdown dialog: "I'm stopped, find someone who can fix it".

Only the driver starts it (no automatic stop detection, never calls 911). Flow:
  1. intent breakdown (kind + the driver's own words) from the agent's classification (PROMPT block);
  2. at most one clarifying question, only when it changes the search (tire, mechanic);
  3. "Give me a minute" -> research runs in the background (providers_search.research);
     the app fetches the result from GET /api/breakdown/{id};
  4. spoken comparison (<= ~25 s) + ProvidersList + "What to tell them" card;
  5. follow-ups ("call the cheapest one", "which one takes Comchek") answered from stored facts only.
"""
import asyncio
import json
import logging
import os
import re
import time
import uuid
from typing import Optional


from models import AgentRequest, AgentResponse, CallCard, Place, Provider
from services import kv, llm, session_store
from services import providers_search as ps
from services.http import client
from services.routing import get_routing_provider
from services.timing import stage

logger = logging.getLogger("truckbuddy.breakdown")

MODEL = ("openai", "gpt-5.4-mini")
START = "Okay. Give me a minute, I'll check who's around and what drivers say about them."
NOT_IN_FACTS = "They don't mention that, ask when you call."
NO_ONE = "I couldn't find anyone nearby who works on trucks. Want me to look for a tow instead?"
NO_REVIEWS = "I couldn't pull reviews, here's who's nearby."
QUESTIONS = {
    "tire": "Which tire, steer, drive or trailer? And can you roll on it or you need them to come out?",
    "mechanic": "Can it still drive, or does it need to come to you?",
}
FOLLOWUP_TTL_S = 30 * 60
RESULT_WAIT_S = 30

# Block for the agent's classification prompt (services/agent.py SYSTEM, same pattern as approach.PROMPT).
PROMPT = """- breakdown: the truck or trailer is broken or stuck and the driver wants someone to fix it, tow it or bring fuel ("I blew a tire", "flat on the trailer", "truck won't start", "losing power", "need a tow", "reefer quit", "ran out of fuel", "brake light on", "landing gear stuck", "who can fix ... near me"). Use intent "breakdown" and also return
  "kind": "tire"|"mechanic"|"tow"|"reefer"|"fuel"|"electrical"|"trailer" (tire = flat/blowout; tow = needs a tow or wrecker; reefer = refrigeration unit; fuel = out of fuel/DEF; electrical = lights, battery, alternator, wiring, brake/ABS light; trailer = landing gear, doors, air lines, trailer brakes; mechanic = engine, won't start, losing power, check engine, overheating, anything else),
  "detail": the problem in the driver's own words (short), "mode": "mobile_needed" (can't move, needs them to come out) | "shop_ok" (ONLY if the driver says it still drives / can roll to a shop; "losing power" alone is unknown) | "unknown",
  "tire_position": "steer"|"drive"|"trailer"|null, "tire_size": e.g. "295/75R22.5" or null. Only what the driver said; no safety lectures."""

PARSE_SYSTEM = """A truck driver describes a mechanical problem. Return JSON only:
{"breakdown": true|false, "kind": "tire"|"mechanic"|"tow"|"reefer"|"fuel"|"electrical"|"trailer"|null,
 "detail": "<the problem in the driver's own words, short, e.g. blew a drive tire on the trailer>",
 "mode": "mobile_needed"|"shop_ok"|"unknown",
 "tire_position": "steer"|"drive"|"trailer"|null, "tire_size": "<e.g. 295/75R22.5>"|null,
 "changed_topic": true|false}
changed_topic is true only if the driver's answer is not an answer to our question but a new request or a cancel ("never mind", "take me to Reno", "find a truck stop", "forget it"); then breakdown is false too.
breakdown is false if this is not about the truck or trailer needing repair, tow or fuel (e.g. "find a truck stop").
kind: tire = flat/blowout/tire; tow = needs a tow or wrecker; reefer = refrigeration unit; fuel = ran out of fuel/DEF;
electrical = lights, battery, alternator, wiring, brake/ABS light; trailer = landing gear, doors, air lines, trailer brakes;
mechanic = engine, won't start, losing power, check engine, overheating, air leak, anything else.
mode: mobile_needed if the truck can't move, is stuck, needs them to come out, or a tow; shop_ok ONLY if the driver explicitly says it still drives or they can roll to a shop ("losing power" alone is unknown); else unknown.
Never add details the driver didn't say."""

FOLLOWUP_SYSTEM = """You help a stranded truck driver choose between roadside service providers found a moment ago.
Providers (1-based index, facts extracted from their reviews and websites; anything not listed is unknown):
{providers}
Classify the driver's LAST utterance. JSON only:
{{"about_providers": true|false, "action": "call"|"route"|"answer"|"more", "index": integer|null, "reply": string}}
- about_providers false if the utterance is a new, unrelated request (e.g. find a truck stop, take me to Reno).
- call: driver wants to phone one ("call the cheapest one", "call Big Rig"). index = that provider. reply = "Calling <name>."
- route: driver wants to drive/be routed there ("route me to the mobile one"). index = that provider. reply = "".
- more: "anyone else", "any other options". reply = "".
- answer: any question about them ("which one takes Comchek", "how far is the second one", "read me their reviews"). reply = spoken answer, max 3 short sentences, ONLY from the facts above; quote reviews from the facts when asked to read them. Only if the asked fact is missing, say exactly: "They don't mention that, ask when you call." Never add that sentence after a real answer.
"cheapest" = lowest price amount in the facts; "fastest" = lowest wait time; "closest" = smallest distance. If nobody has that fact, answer and say they don't mention it.
English, spoken style, no markdown, no numbers as digits with units glued (say "six miles")."""

SUMMARY_SYSTEM = """You are a truck driver's copilot. The driver is stopped with: {detail} (need: {mode}).
Here are the ranked providers. "known" lists the ONLY things that are true about each one; anything not in "known" is unknown and must not be said:
{providers}
Cheapest by price mentions: {cheapest}. Fastest by reviews: {fastest}.
Write JSON only: {{"spoken": "...", "summary": "..."}}
spoken: what you say out loud, max 60 words, natural like a dispatcher who just called around. Start with "Okay, here's what I found." The list is already ranked: the best bet is ALWAYS provider i=1; say why using its "known" facts. Name the cheapest and the fastest only if given above (not "none") and they are someone else. If a provider has red flags, say you'd skip them and why in a few words; if nobody has red flags, say nothing about skipping. End with "Tap one to call, or ask me anything about them."
{price_rule}
summary: two sentences for the screen (digits are fine here: 2.6 mi, 24/7, 4.7 stars): who is the best choice and why, who to avoid if anyone.
Never invent prices, times, hours or services. Never lecture about safety. Write numbers in words for spoken (four point seven stars, six miles)."""

_JOBS: dict[str, "asyncio.Task[AgentResponse]"] = {}
_tasks: set = set()


def _parse(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        d = {}
    return d if isinstance(d, dict) else {}


async def _llm(system: str, text: str, timeout: float = 25) -> dict:  # first call per process is a ~13 s cold start
    return _parse(await asyncio.wait_for(llm.complete(system, text, MODEL[1]), timeout=timeout))


def needs_question(d: dict) -> Optional[str]:
    """One question, only if it changes the search and the driver hasn't already answered it."""
    kind = d.get("kind")
    if kind == "tire" and (d.get("mode") == "unknown" or not d.get("tire_position")):
        return QUESTIONS["tire"]
    if kind == "mechanic" and d.get("mode") == "unknown":
        return QUESTIONS["mechanic"]
    return None


def defaulted(d: dict) -> str:
    """What the search goes with when the driver did not answer the question: "I'll look for someone who can come
    out to you, tell them which tire when you call"."""
    what = ("I'll look for a shop you can roll to" if resolve_mode(d) == "shop_ok"
            else "I'll look for someone who can come out to you")
    if d.get("kind") == "tire" and not d.get("tire_position"):
        what += ", tell them which tire when you call"
    return what


def resolve_mode(d: dict) -> str:
    if d.get("kind") in ("tow", "fuel"):
        return "mobile_needed"
    return d.get("mode") if d.get("mode") in ("mobile_needed", "shop_ok") else "mobile_needed"


# ---------------------------------------------------------------- call card

async def where(lat: float, lng: float) -> str:
    """Road + nearest cross street / town: HERE reverse geocode, else OpenStreetMap Nominatim."""
    try:
        if os.environ.get("HERE_API_KEY"):
            r = await client().get("https://revgeocode.search.hereapi.com/v1/revgeocode",
                                   params={"at": f"{lat},{lng}", "limit": 1, "apiKey": os.environ["HERE_API_KEY"]},
                                   timeout=4)
            items = r.json().get("items", []) if r.status_code == 200 else []
            if items:
                return (items[0].get("address") or {}).get("label") or ""
        r = await client().get("https://nominatim.openstreetmap.org/reverse",
                               params={"lat": lat, "lon": lng, "format": "jsonv2", "zoom": 17},
                               headers={"User-Agent": "TruckBuddy/1.0 (truck copilot demo)"}, timeout=4)
        a = (r.json() or {}).get("address", {}) if r.status_code == 200 else {}
        town = a.get("city") or a.get("town") or a.get("village") or a.get("county")
        parts = [a.get("road"), town, a.get("state")]
        return ", ".join(p for p in parts if p)
    except Exception as e:  # noqa: BLE001
        logger.info("reverse geocode failed: %s", e)
        return ""


def call_card(d: dict, req: AgentRequest, place_text: str) -> CallCard:
    t = req.truck_profile
    map_link = f"https://maps.google.com/?q={req.lat:.5f},{req.lng:.5f}"
    gps = f"{req.lat:.5f}, {req.lng:.5f}"
    tire = " ".join(x for x in (d.get("tire_position") and f"{d['tire_position']} tire",
                                d.get("tire_size")) if x) or None
    at = place_text or "my GPS location"
    return CallCard(
        problem=d.get("detail") or d.get("kind") or "breakdown",
        tire=tire,
        location=place_text or None,
        gps=gps,
        map_link=map_link,
        truck=t.label,
        questions=["What's your road call fee?", "What's your ETA to me?", "Do you take Comchek or EFS?"],
        share_text=f"I'm stuck at {at}. GPS {gps}. {map_link}",
    )


# ---------------------------------------------------------------- research job

def _place(p: Provider) -> Place:
    return Place(id=p.id, name=p.name, category="service", lat=p.lat, lng=p.lng, address=p.address,
                 rating=p.rating, rating_count=p.rating_count, distance_mi=p.distance_mi)


def _quotes(p: dict) -> list[str]:
    """Up to 2 verbatim review snippets (first sentence, best rated first)."""
    out = []
    for r in sorted(p.get("reviews") or [], key=lambda r: -(r.get("rating") or 0)):
        s = re.split(r"(?<=[.!?])\s+", (r.get("text") or "").strip())[0][:140]
        if len(s) > 20:
            out.append(s)
    return out[:2]


def to_provider(p: dict) -> Provider:
    """A researched candidate (a dict with its raw reviews) as the app and the dialog state keep it."""
    return Provider(**{k: p.get(k) for k in ("id", "name", "lat", "lng", "phone", "website", "address", "rating",
                                             "distance_mi", "open_now", "listed_24_7", "shop_only", "score",
                                             "source")},
                    rating_count=p.get("rating_count") or 0, facts=p["facts"], quotes=_quotes(p))


def wait_text(w: Optional[dict]) -> Optional[str]:
    """'within an hour' / 'within 45 min' / '30-60 min' / 'about 2 hours' as drivers report it."""
    if not w:
        return None
    lo, hi = int(w["typical_min_low"]), int(w["typical_min_high"])
    if hi >= 90:
        return f"about {round(hi / 60)} hours" if lo == hi or lo >= 60 else f"up to {round(hi / 60)} hours"
    if lo == 0 or lo == hi:
        return "within an hour" if hi == 60 else f"within {hi} min"
    return f"{lo}-{hi} min"


def known(p: dict) -> list[str]:
    """Plain-English facts the copilot may say about a provider (nothing else is known)."""
    f = p["facts"]
    out = [f"{p['distance_mi']} miles away"]
    if p.get("rating"):
        out.append(f"{p['rating']} stars on {p.get('rating_count') or 0} reviews")
    if f["mobile_service"] is True:
        out.append("mobile service, they come out to you")
    elif p.get("shop_only"):
        out.append("shop only, you'd have to roll there")
    if p.get("listed_24_7") or f["hours_24_7"] is True:
        out.append("open 24/7")
    else:
        out.append("NOT listed as 24/7 (never say 24/7 for this one)")
        if p.get("open_now") is True:
            out.append("open now")
        elif p.get("open_now") is False:
            out.append("closed right now")
    for x in f["prices"]:
        amt = f"${x['amount_usd_low']:.0f}" + (f"-{x['amount_usd_high']:.0f}" if x["amount_usd_high"] != x["amount_usd_low"] else "")
        out.append(f"{'drivers mention' if x['source'] == 'review' else 'website says'} {amt} {x['what']}")
    if f["wait_time"]:
        who = "drivers say they" if f["wait_time"].get("source") != "website" else "their website says they"
        out.append(f"{who} come out {wait_text(f['wait_time'])}")
    if f["red_flags"]:
        out.append("red flags in reviews: " + ", ".join(f["red_flags"]))
    if f["payment"]:
        out.append("takes " + ", ".join(f["payment"]))
    return out


def cheapest_fastest(providers: list[dict]) -> tuple[Optional[str], Optional[str]]:
    priced = [p for p in providers if p["facts"]["prices"]]
    timed = [p for p in providers if p["facts"]["wait_time"]]
    cheap = min(priced, key=lambda p: min(x["amount_usd_low"] for x in p["facts"]["prices"]))["name"] if len(priced) > 1 else None
    fast = min(timed, key=lambda p: p["facts"]["wait_time"]["typical_min_high"])["name"] if timed else None
    return cheap, fast


def _brief(providers: list[Provider]) -> str:
    rows = []
    for i, p in enumerate(providers, 1):
        rows.append(json.dumps({"i": i, "name": p.name, "known": known(p.model_dump()), "phone": p.phone,
                                "review_snippets": p.quotes}))
    return "\n".join(rows)


SHOP_ONLY_NOTE = "None of them say they come out to you, so you'd have to get to them."


def shop_only_note(providers: list[dict], mode: str) -> str:
    """The driver can't roll and nobody nearby is known to do road calls: said up front, not left to a
    "Shop only" badge on the card (QA 2026-10-31, H21). Empty when the top pick comes out, or the truck can move."""
    if mode != "mobile_needed" or not providers or not providers[0].get("shop_only"):
        return ""
    return SHOP_ONLY_NOTE


NO_MOBILE = ("I couldn't find anyone nearby who comes out to you. The closest shop is {name}, about {mi} miles, but "
             "you'd need a tow to get there. Tap it to call, or ask me to look for a tow.")
NO_MOBILE_SUMMARY = "Nobody nearby says they come out. Closest shop: {name}, {mi} mi, needs a tow."


def nobody_comes_out(d: dict, mode: str, providers: list[dict]) -> bool:
    """The truck can't move and none of the providers is known to come out (a tow company is the one that comes)."""
    return mode == "mobile_needed" and d.get("kind") != "tow" and bool(providers) and \
        not any(p["facts"]["mobile_service"] is True for p in providers)


def fallback_spoken(providers: list[dict]) -> str:
    best = providers[0]
    mobile = " and they come out to you" if best["facts"]["mobile_service"] is True else ""
    stars = f", {best['rating']} stars" if best.get("rating") else ""
    return (f"Okay, here's what I found. Best bet is {best['name']}, about {best['distance_mi']} miles{mobile}{stars}. "
            "Tap one to call, or ask me anything about them.")


async def summarize(d: dict, mode: str, providers: list[dict]) -> tuple[str, str]:
    with_prices = [p for p in providers if p["facts"]["prices"]]
    priced = len(with_prices)
    if priced == 0:
        rule = ("Nobody posts prices: say \"Nobody posts prices, you'll have to ask. Ask for the road call fee "
                "and their ETA.\"")
    elif priced == 1:
        rule = (f"Only {with_prices[0]['name']} posts a price: mention it and say the rest you'll have to ask. "
                "Do not say nobody posts prices.")
    else:
        rule = ""
    try:
        cheap, fast = cheapest_fastest(providers)
        out = await _llm(SUMMARY_SYSTEM.format(detail=d.get("detail") or d.get("kind"), mode=mode.replace("_", " "),
                                               providers=_brief([to_provider(p) for p in providers]),
                                               price_rule=rule,
                                               cheapest=cheap or "none", fastest=fast or "none"),
                         "Write it.", timeout=8)
        spoken, summary = str(out.get("spoken") or "").strip(), str(out.get("summary") or "").strip()
    except Exception as e:  # noqa: BLE001
        logger.warning("summary failed: %s", e)
        spoken = summary = ""
    if not spoken:
        spoken = fallback_spoken(providers)
    note = shop_only_note(providers, mode)
    if note and "come out" not in spoken.lower():
        spoken = f"{note} {spoken}"
    if priced == 0 and "nobody posts prices" not in spoken.lower():
        spoken = spoken.replace("Tap one to call", "Nobody posts prices, you'll have to ask. Tap one to call")
    spoken = fit_spoken(no_false_24_7(spoken, providers))
    return spoken, no_false_24_7(summary, providers) or spoken


_247 = re.compile(r",?\s*(?:and\s+)?(?:open\s+)?(?:24/7|twenty[- ]four seven|around the clock)", re.I)


def no_false_24_7(text: str, providers: list[dict]) -> str:
    """Removes "24/7" from sentences about a provider that is not listed as 24/7 (model guard)."""
    def is247(p: dict) -> bool:
        return bool(p.get("listed_24_7") or p["facts"]["hours_24_7"] is True)

    def key(p: dict) -> str:
        words = [w for w in re.findall(r"[A-Za-z0-9']+", p["name"]) if len(w) >= 3]
        return words[0].lower() if words else ""

    out = []
    for s in re.split(r"(?<=[.!?])\s+", text or ""):
        low = s.lower()
        named = [p for p in providers if key(p) and re.search(rf"\b{re.escape(key(p))}\b", low)]
        if named and not any(is247(p) for p in named):
            s = _247.sub("", s)
        out.append(s)
    return " ".join(out)


END = "Tap one to call, or ask me anything about them."
MAX_WORDS = 75  # ~25 s of speech


def fit_spoken(spoken: str) -> str:
    """Cuts at whole sentences (never mid-sentence) so the answer stays under ~25 s, keeping the closing line."""
    if len(spoken.split()) <= MAX_WORDS:
        return spoken
    body = spoken.replace(END, "").strip()
    out = []
    for s in re.split(r"(?<=[.!?])\s+", body):
        if len(" ".join(out + [s, END]).split()) > MAX_WORDS:
            break
        out.append(s)
    return " ".join(out + [END])


async def _run(d: dict, req: AgentRequest, sess: dict, source: str) -> AgentResponse:
    mode = resolve_mode(d)
    budget = ps.Budget()
    res, place_text = await asyncio.gather(ps.research(d["kind"], mode, req.lat, req.lng, budget),
                                           where(req.lat, req.lng))
    ranked = res["providers"]
    card = call_card(d, req, place_text)
    if not ranked:
        return AgentResponse(status="chat", transcript="", reply=NO_ONE, call_card=card,
                             research_stats=res["stats"], source=source)
    t = time.perf_counter()
    if nobody_comes_out(d, mode, ranked):
        # "I can't roll on it" and only shops found: the top pick read as the answer, its "Shop only" badge the
        # one hint (QA 2026-11-02). Said plainly, with the closest shop first and the tow it takes to get there.
        ranked = sorted(ranked, key=lambda p: p.get("distance_mi") or 0)
        spoken = NO_MOBILE.format(name=ranked[0]["name"], mi=ranked[0]["distance_mi"])
        summary = NO_MOBILE_SUMMARY.format(name=ranked[0]["name"], mi=ranked[0]["distance_mi"])
    elif res["source"] == "here":
        spoken, summary = f"{NO_REVIEWS} The closest is {ranked[0]['name']}, about {ranked[0]['distance_mi']} miles.", NO_REVIEWS
    else:
        spoken, summary = await summarize(d, mode, ranked)
    stats = res["stats"]
    stats["stages_ms"]["summary"] = round((time.perf_counter() - t) * 1000)
    stats["llm_calls"] += 1
    # The dialog keeps Provider models, the type it has after a trip through the database store; dicts here
    # came back as models on another worker and "call the cheapest" crashed on p["facts"] (QA 2026-11-02, bug 3).
    providers = [to_provider(p) for p in ranked]
    sess["providers"] = providers
    sess["providers_at"] = time.time()  # epoch time: the state crosses processes
    sess["places"] = [_place(p) for p in providers]
    sess["breakdown"] = {**d, "mode": mode}
    logger.info("breakdown research kind=%s mode=%s n=%d stats=%s", d["kind"], mode, len(ranked), stats)
    return AgentResponse(status="providers", transcript="", reply=spoken, summary=summary, breakdown_mode=mode,
                         breakdown_kind=d["kind"], providers=providers, call_card=card,
                         provider_source=res["source"], research_stats=stats, source=source)


def _persist(rid: str, driver: Optional[str], session_id: Optional[str], sess, task: "asyncio.Task[AgentResponse]") -> None:
    """The finished research into the jobs table (any worker can answer GET /breakdown/{rid}) and the dialog
    state it changed (providers, places) back into the session store: the turn that started it saved before."""
    async def _go():
        try:
            res = task.result()
            state = res.model_dump(mode="json")
        except Exception as e:  # noqa: BLE001
            state = {"status": "error", "error": type(e).__name__}
        try:
            await kv.job_put(rid, driver, "breakdown", state)
        except Exception as e:  # noqa: BLE001
            logger.warning("breakdown job %s not saved: %s", rid, e)
        if session_id and hasattr(sess, "to_json"):
            try:
                await session_store.get_store().save(session_id, sess)
            except Exception as e:  # noqa: BLE001
                logger.warning("breakdown session %s not saved: %s", session_id, e)

    t = asyncio.create_task(_go())
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


def start_research(d: dict, req: AgentRequest, sess: dict, text: str, source: str) -> AgentResponse:
    rid = uuid.uuid4().hex
    task = asyncio.create_task(_run(d, req, sess, source))
    _JOBS[rid] = task
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    driver = sess.get("driver") if hasattr(sess, "get") else None
    opened = asyncio.create_task(kv.job_put(rid, driver, "breakdown", {"status": "researching"}))
    _tasks.add(opened)
    opened.add_done_callback(_tasks.discard)
    task.add_done_callback(lambda t, rid=rid, driver=driver, sid=req.session_id, sess=sess: _persist(rid, driver, sid, sess, t))
    while len(_JOBS) > 50:
        _JOBS.pop(next(iter(_JOBS)))
    sess.pop("breakdown_pending", None)
    return AgentResponse(status="researching", transcript=text, reply=START, research_id=rid,
                         breakdown_kind=d["kind"], breakdown_mode=resolve_mode(d), source=source)


TOO_LONG = "Sorry, the search took too long. Want me to try again?"
RESULT_POLL_S = 0.5


async def result(rid: str) -> Optional[AgentResponse]:
    """The research result: awaited when it runs in this worker, otherwise read from the jobs table (another
    worker ran it, or this one restarted) and polled while it is still researching. None = unknown id."""
    task = _JOBS.get(rid)
    if task is not None:
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=RESULT_WAIT_S)
        except Exception:  # noqa: BLE001
            logger.exception("breakdown research failed")
            return AgentResponse(status="chat", transcript="", reply=TOO_LONG, source=MODEL[1])
    deadline = time.monotonic() + RESULT_WAIT_S
    while True:
        try:
            row = await kv.job_get(rid)
        except Exception as e:  # noqa: BLE001
            logger.warning("breakdown job %s unreadable: %s", rid, e)
            return None
        if row is None:
            return None
        if row.get("status") == "error":
            return AgentResponse(status="chat", transcript="", reply=TOO_LONG, source=MODEL[1])
        if row.get("status") != "researching":
            try:
                return AgentResponse(**row)
            except Exception as e:  # noqa: BLE001
                logger.warning("breakdown job %s malformed: %s", rid, e)
                return AgentResponse(status="chat", transcript="", reply=TOO_LONG, source=MODEL[1])
        if time.monotonic() >= deadline:
            return AgentResponse(status="chat", transcript="", reply=TOO_LONG, source=MODEL[1])
        await asyncio.sleep(RESULT_POLL_S)


# ---------------------------------------------------------------- follow-ups

SUPERLATIVE = re.compile(r"\b(cheapest|fastest|quickest|closest|nearest)\b", re.I)
DO_CALL = re.compile(r"\b(call|dial|phone|ring)\b", re.I)
DO_ROUTE = re.compile(r"\b(route|take me|go to|drive to|navigate)\b", re.I)


def pick_superlative(providers: list[Provider], word: str) -> Optional[Provider]:
    """cheapest = lowest posted price (one priced provider is enough), fastest = lowest ETA, closest = distance."""
    w = word.lower()
    if w == "cheapest":
        priced = [p for p in providers if p.facts.prices]
        return min(priced, key=lambda p: min(x["amount_usd_low"] for x in p.facts.prices)) if priced else None
    if w in ("fastest", "quickest"):
        timed = [p for p in providers if p.facts.wait_time]
        return min(timed, key=lambda p: p.facts.wait_time["typical_min_high"]) if timed else None
    return min(providers, key=lambda p: p.distance_mi or 0)


async def followup(req: AgentRequest, sess: dict, text: str, source: str) -> Optional[AgentResponse]:
    providers: list[Provider] = sess["providers"]
    sup = SUPERLATIVE.search(text)
    if sup and (DO_CALL.search(text) or DO_ROUTE.search(text)):
        # "call the cheapest one" / "route me to the closest": decided in code, not by the model.
        chosen = pick_superlative(providers, sup.group(1))
        if chosen is None:
            what = "prices" if sup.group(1).lower() == "cheapest" else "how fast they come out"
            return AgentResponse(status="chat", transcript=text, source=source,
                                 reply=f"None of them mention {what}. Want me to call {providers[0].name}, the top pick?")
        out = {"about_providers": True, "action": "call" if DO_CALL.search(text) else "route",
               "index": providers.index(chosen) + 1, "reply": ""}
    else:
        out = await _llm(FOLLOWUP_SYSTEM.format(providers=_brief(providers)), f"Driver said: {text}")
    if out.get("about_providers") is not True:
        return None
    action = out.get("action")
    try:
        idx = int(out.get("index"))
    except (TypeError, ValueError):
        idx = None
    chosen = providers[idx - 1] if idx and 1 <= idx <= len(providers) else None
    reply = str(out.get("reply") or "").strip()
    if action == "call" and chosen:
        return AgentResponse(status="chat", transcript=text, reply=reply or f"Calling {chosen.name}.",
                             dial_phone=chosen.phone, source=source)
    if action == "route" and chosen:
        place = _place(chosen)
        with stage("tools"):
            route = await get_routing_provider().route(req.lat, req.lng, place.lat, place.lng, req.truck_profile)
        others = [_place(p) for p in providers if p.id != chosen.id]
        from services.agent import _with_warnings  # agent imports this module; the warnings are the route answers' own
        return AgentResponse(status="route_set", transcript=text, category="service", place=place, route=route,
                             options=others, source=source,
                             reply=_with_warnings(f"Okay, route to {chosen.name} is set, {route.summary.split(' · ')[0]}.", route))
    if action == "more":
        return AgentResponse(status="chat", transcript=text, source=source,
                             reply="That's everyone I found who works on trucks around here. Want me to look for a tow instead?")
    if action == "answer" and reply:
        return AgentResponse(status="chat", transcript=text, reply=reply, source=source)
    return AgentResponse(status="chat", transcript=text, reply=NOT_IN_FACTS, source=source)


# ---------------------------------------------------------------- entry point

async def from_intent(d: dict, req: AgentRequest, sess: dict, text: str, source: str) -> AgentResponse:
    """intent=breakdown from the agent's classification: one question if it matters, else start the research."""
    if d.get("kind") not in ps.KINDS:
        d["kind"] = "mechanic"
    q = needs_question(d)
    if q and sess.pop("breakdown_asked", None) == q:
        # Asked a moment ago and the driver said something else ("call the cheapest" before "which tire", QA
        # 2026-11-02): never the same question twice in a row. The search starts with the default and says which.
        res = start_research(d, req, sess, text, source)
        res.reply = f"Okay, {defaulted(d)}. " + START.removeprefix("Okay. ")
        return res
    if q:
        sess["breakdown_pending"] = {"text": text, "kind": d["kind"], "detail": d.get("detail"), "question": q}
        return AgentResponse(status="chat", transcript=text, reply=q, breakdown_kind=d["kind"], source=source)
    return start_research(d, req, sess, text, source)


async def handle(req: AgentRequest, sess: dict, text: str, source: str) -> Optional[AgentResponse]:
    """Before classification: the answer to our clarifying question, or a follow-up about found providers.
    Returns None to let the normal agent classify the utterance (a new breakdown comes back as intent=breakdown)."""
    try:
        return await _handle(req, sess, text, source)
    except Exception:  # noqa: BLE001 - never break the normal agent because of this scenario
        logger.exception("breakdown turn failed")
        if sess.get("breakdown_pending"):
            sess.pop("breakdown_pending", None)
            return AgentResponse(status="chat", transcript=text, source=source,
                                 reply="Sorry, I lost that. Tell me again what happened?")
        return None


async def _handle(req: AgentRequest, sess: dict, text: str, source: str) -> Optional[AgentResponse]:
    sess.pop("breakdown_asked", None)  # only ever about the turn right after the question
    pending = sess.get("breakdown_pending")
    if pending:
        with stage("llm_round_1"):
            d = await _llm(PARSE_SYSTEM, f"Driver said: {pending['text']} | We asked: {pending.get('question', '')} | "
                                         f"Then answered: {text}")
        sess.pop("breakdown_pending", None)
        if d.get("changed_topic") is True or d.get("breakdown") is False:
            # "never mind, take me to Reno" / "find a truck stop": the normal agent handles it. Should it read
            # the breakdown again, from_intent knows the question was just asked.
            sess["breakdown_asked"] = pending.get("question")
            return None
        # The answer only fills in how/which; the problem itself stays what the driver said first.
        d["kind"] = pending["kind"]
        d["detail"] = pending.get("detail") or d.get("detail")
        return start_research(d, req, sess, text, source)

    if sess.get("providers") and time.time() - sess.get("providers_at", 0) < FOLLOWUP_TTL_S:
        with stage("llm_round_1"):
            return await followup(req, sess, text, source)
    return None
