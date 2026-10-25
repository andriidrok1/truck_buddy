"""Breakdown research: who can fix the truck nearby, with facts pulled from real text only.

Pipeline (budget ~20 s, everything in parallel):
  candidates  Google Places (New) Text Search, 3 queries per kind, merged by place id
              (fallback: HERE Discover, no reviews/prices)
  texts       up to 5 Google reviews + website home page and a services/pricing page
  facts       one gpt-5.4-mini JSON call per candidate; every price/wait quote must be
              found verbatim in the source text, otherwise it is dropped
  rank        score per mode (mobile_needed / shop_ok), top 6
"""
import asyncio
import html
import json
import logging
import math
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

from emergentintegrations.llm.chat import LlmChat, UserMessage

from services.geo import haversine_km, km_to_mi
from services.http import client

logger = logging.getLogger("truckbuddy.providers")

KINDS = ("tire", "mechanic", "tow", "reefer", "fuel", "electrical", "trailer")
QUERIES = {
    "tire": ["semi truck tire repair", "mobile truck tire service", "24 hour commercial tire"],
    "mechanic": ["mobile truck mechanic", "heavy duty truck repair", "diesel repair shop"],
    "tow": ["heavy duty towing", "semi truck towing", "rotator wrecker"],
    "reefer": ["reefer repair", "Thermo King", "Carrier Transicold"],
    "fuel": ["mobile diesel delivery", "roadside fuel service"],
    "electrical": ["truck electrical repair", "mobile diesel mechanic"],
    "trailer": ["trailer repair", "mobile trailer repair"],
}
FIELD_MASK = ",".join(f"places.{f}" for f in (
    "id", "displayName", "location", "rating", "userRatingCount", "nationalPhoneNumber",
    "currentOpeningHours", "regularOpeningHours", "reviews", "websiteUri", "shortFormattedAddress",
    "businessStatus", "types",
))
TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
HERE_DISCOVER_URL = "https://discover.search.hereapi.com/v1/discover"
RADIUS_MI = {"shop_ok": 50, "mobile_needed": 75}
MAX_CANDIDATES = 10
TOP = 6
BUDGET_S = 20.0
SITE_TIMEOUT_S = 4.0
SITE_STAGE_S = 6.0
SITE_CHARS = 6000
EXTRACT_MODEL = ("openai", "gpt-5.4-mini")
# List prices for the cost report (USD).
GOOGLE_TEXT_SEARCH_ATMOSPHERE_USD = 0.040  # Text Search Enterprise + Atmosphere (reviews), per request
LLM_IN_USD_PER_M, LLM_OUT_USD_PER_M = 0.75, 4.50  # gpt-5.4-mini list price

# A place counts as truck-capable only if one of these shows up in its name, types or reviews.
TRUCK_WORDS = re.compile(
    r"\b(truck|trucks|trucking|semi|semis|commercial|heavy[\s-]?duty|diesel|fleet|trailer|trailers|tractor|"
    r"big rig|rig|18[\s-]?wheeler|class 8|rotator|wrecker|reefer|thermo king|carrier transicold|"
    r"refrigerat\w*|kenworth|peterbilt|freightliner|volvo trucks|mack|international trucks|road ?call)\b",
    re.I,
)

EXTRACT_SYSTEM = """You read Google reviews and website text of a roadside service business for a stranded truck driver.
Extract ONLY facts that are written in the text. If something is not written, use "unknown" or an empty list. Never guess, never use general knowledge.
Answer with JSON only:
{"mobile_service": true|false|"unknown",
 "hours_24_7": true|false|"unknown",
 "truck_focused": true|false,
 "prices": [{"what": "road call fee"|"trailer tire installed"|"steer tire installed"|"drive tire installed"|"hourly labor"|"tow hookup"|"per mile"|"other: <3 words>", "amount_usd_low": number, "amount_usd_high": number, "source": "review"|"website", "quote": "<exact words copied from the text, max 120 chars, must contain the amount>"}],
 "wait_time": {"typical_min_low": number, "typical_min_high": number, "quote": "<exact words copied from the text>"} | null,
 "payment": ["comchek"|"efs"|"t-chek"|"card"|"cash"],
 "red_flags": ["<max 2 short phrases, only complaints written in the text, e.g. upcharge at night, no-show, damaged rim>"],
 "praise": ["<max 2 short phrases written in the text>"],
 "last_review_date": "YYYY-MM-DD" | null}
Rules: mobile_service is true only if the text says they come out / do road calls / mobile service. hours_24_7 true only if the text says 24/7, 24 hours or around the clock. truck_focused true if they clearly serve semis, commercial or heavy-duty trucks. A price needs a dollar amount in the text; a single amount means low = high. wait_time is how fast they arrived or finished, in minutes ("showed up in 40 minutes" -> 40-40; "within an hour" -> 0-60)."""


def _now() -> float:
    return time.perf_counter()


class Budget:
    """Wall-clock budget + stage timings + cost counters for one research run."""

    def __init__(self, seconds: float = BUDGET_S):
        self.t0 = _now()
        self.deadline = self.t0 + seconds
        self.stages: dict[str, int] = {}
        self.google_calls = 0
        self.here_calls = 0
        self.llm_calls = 0
        self.llm_in_chars = 0
        self.llm_out_chars = 0

    def left(self, reserve: float = 0.0) -> float:
        return max(0.5, self.deadline - _now() - reserve)

    def mark(self, name: str, since: float) -> float:
        now = _now()
        self.stages[name] = round((now - since) * 1000)
        return now

    def report(self) -> dict[str, Any]:
        tokens_in, tokens_out = self.llm_in_chars // 4, self.llm_out_chars // 4
        return {
            "stages_ms": {**self.stages, "total": round((_now() - self.t0) * 1000)},
            "google_text_search_calls": self.google_calls,
            "here_calls": self.here_calls,
            "llm_calls": self.llm_calls,
            "llm_tokens_in_est": tokens_in,
            "llm_tokens_out_est": tokens_out,
            "google_cost_usd": round(self.google_calls * GOOGLE_TEXT_SEARCH_ATMOSPHERE_USD, 3),
            "llm_cost_usd": round(tokens_in * LLM_IN_USD_PER_M / 1e6 + tokens_out * LLM_OUT_USD_PER_M / 1e6, 4),
        }


# ---------------------------------------------------------------- candidates

def _rect(lat: float, lng: float, radius_mi: float) -> dict:
    dlat = radius_mi / 69.0
    dlng = radius_mi / max(1.0, 69.0 * math.cos(math.radians(lat)))
    return {"rectangle": {"low": {"latitude": lat - dlat, "longitude": lng - dlng},
                          "high": {"latitude": lat + dlat, "longitude": lng + dlng}}}


def review_texts(p: dict) -> list[dict]:
    out = []
    for r in (p.get("reviews") or [])[:5]:
        text = ((r.get("text") or r.get("originalText") or {}).get("text") or "").strip()
        out.append({"text": text, "rating": r.get("rating"), "date": (r.get("publishTime") or "")[:10]})
    return out


def is_truck_capable(p: dict) -> bool:
    """False for places that look car-only: no truck word in name, types or reviews."""
    name = (p.get("displayName") or {}).get("text") or ""
    types = " ".join(p.get("types") or []).replace("_", " ")
    reviews = " ".join(r["text"] for r in review_texts(p))
    return bool(TRUCK_WORDS.search(f"{name} {types} {reviews}"))


def keep_candidate(p: dict) -> bool:
    return (bool(p.get("nationalPhoneNumber"))
            and p.get("businessStatus", "OPERATIONAL") != "CLOSED_PERMANENTLY"
            and is_truck_capable(p))


def is_24_7(hours: Optional[dict]) -> bool:
    if not hours:
        return False
    periods = hours.get("periods") or []
    if len(periods) == 1 and not periods[0].get("close"):
        return True  # Google's encoding of "open 24 hours, every day"
    desc = hours.get("weekdayDescriptions") or []
    return len(desc) == 7 and all("open 24 hours" in d.lower() for d in desc)


def _candidate(p: dict, lat: float, lng: float) -> dict:
    loc = p.get("location") or {}
    km = haversine_km(lat, lng, loc.get("latitude", lat), loc.get("longitude", lng))
    current = p.get("currentOpeningHours") or {}
    return {
        "id": p["id"],
        "name": (p.get("displayName") or {}).get("text") or "Unnamed",
        "lat": loc.get("latitude"),
        "lng": loc.get("longitude"),
        "phone": p.get("nationalPhoneNumber"),
        "website": p.get("websiteUri"),
        "address": p.get("shortFormattedAddress"),
        "rating": p.get("rating"),
        "rating_count": p.get("userRatingCount") or 0,
        "open_now": current.get("openNow"),
        "listed_24_7": is_24_7(p.get("regularOpeningHours")) or is_24_7(current),
        "types": p.get("types") or [],
        "reviews": review_texts(p),
        "distance_mi": round(km_to_mi(km), 1),
        "source": "google",
    }


async def _google_query(q: str, lat: float, lng: float, radius_mi: float, budget: Budget) -> list[dict]:
    budget.google_calls += 1
    r = await client().post(
        TEXT_SEARCH_URL,
        headers={"X-Goog-Api-Key": os.environ["GOOGLE_MAPS_API_KEY"], "X-Goog-FieldMask": FIELD_MASK},
        json={"textQuery": q, "pageSize": 20, "locationRestriction": _rect(lat, lng, radius_mi)},
        timeout=min(8.0, budget.left(12)),
    )
    if r.status_code != 200:
        raise RuntimeError(f"Google Text Search {r.status_code}: {r.text[:200]}")
    return r.json().get("places", [])


def pre_rank(cands: list[dict]) -> list[dict]:
    """Up to 10 by distance and rating, before reading any text."""
    def key(c: dict) -> float:
        rating = (c["rating"] or 3.5) * math.log1p(c["rating_count"] or 0)
        return -rating / 4 + c["distance_mi"] / 10
    return sorted(cands, key=key)[:MAX_CANDIDATES]


async def google_candidates(kind: str, lat: float, lng: float, radius_mi: float, budget: Budget) -> list[dict]:
    results = await asyncio.gather(*(_google_query(q, lat, lng, radius_mi, budget) for q in QUERIES[kind]),
                                   return_exceptions=True)
    errors = [r for r in results if isinstance(r, Exception)]
    if len(errors) == len(results):
        raise errors[0]
    merged: dict[str, dict] = {}
    for res in results:
        if isinstance(res, Exception):
            logger.warning("text search failed: %s", res)
            continue
        for p in res:
            merged.setdefault(p["id"], p)
    kept = [_candidate(p, lat, lng) for p in merged.values() if keep_candidate(p)]
    kept = [c for c in kept if c["distance_mi"] <= radius_mi]
    logger.info("breakdown candidates kind=%s raw=%d kept=%d", kind, len(merged), len(kept))
    return pre_rank(kept)


async def here_candidates(kind: str, lat: float, lng: float, radius_mi: float, budget: Budget) -> list[dict]:
    key = os.environ.get("HERE_API_KEY")
    if not key:
        return []

    async def one(q: str) -> list[dict]:
        budget.here_calls += 1
        r = await client().get(HERE_DISCOVER_URL, params={"q": q, "at": f"{lat},{lng}", "limit": 20,
                                                          "in": "countryCode:USA", "apiKey": key}, timeout=8)
        r.raise_for_status()
        return r.json().get("items", [])

    results = await asyncio.gather(*(one(q) for q in QUERIES[kind]), return_exceptions=True)
    merged: dict[str, dict] = {}
    for res in results:
        for it in res if isinstance(res, list) else []:
            merged.setdefault(it.get("id"), it)
    out = []
    for it in merged.values():
        phones = [ph.get("value") for c in it.get("contacts") or [] for ph in c.get("phone") or []]
        pos = it.get("position") or {}
        if not phones or not pos or not TRUCK_WORDS.search(it.get("title", "")):
            continue
        km = haversine_km(lat, lng, pos["lat"], pos["lng"])
        if km_to_mi(km) > radius_mi:
            continue
        sites = [w.get("value") for c in it.get("contacts") or [] for w in c.get("www") or []]
        out.append({"id": f"here-{it.get('id')}", "name": it.get("title"), "lat": pos["lat"], "lng": pos["lng"],
                    "phone": phones[0], "website": sites[0] if sites else None,
                    "address": (it.get("address") or {}).get("label"), "rating": None, "rating_count": 0,
                    "open_now": None, "listed_24_7": False, "types": [], "reviews": [],
                    "distance_mi": round(km_to_mi(km), 1), "source": "here"})
    return sorted(out, key=lambda c: c["distance_mi"])[:MAX_CANDIDATES]


# ---------------------------------------------------------------- website text

_TAGS = re.compile(r"<(script|style|noscript|svg)[^>]*>.*?</\1>|<[^>]+>", re.S | re.I)
_LINK = re.compile(r"<a[^>]+href=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", re.S | re.I)
_SERVICE_LINK = re.compile(r"servic|pric|rate|road.?call|mobile|tire", re.I)


def html_text(raw: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAGS.sub(" ", raw))).strip()


def services_link(base: str, raw: str) -> Optional[str]:
    host = urlparse(base).netloc
    for href, label in _LINK.findall(raw):
        url = urljoin(base, href)
        if urlparse(url).netloc == host and url.rstrip("/") != base.rstrip("/") and \
                (_SERVICE_LINK.search(href) or _SERVICE_LINK.search(html_text(label))):
            return url
    return None


async def site_text(url: Optional[str]) -> str:
    if not url or not url.startswith("http"):
        return ""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; TruckBuddy/1.0)"}
    try:
        r = await client().get(url, headers=headers, timeout=SITE_TIMEOUT_S, follow_redirects=True)
        if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
            return ""
        home = r.text
        text = html_text(home)[:SITE_CHARS]
        link = services_link(str(r.url), home)
        if link and len(text) < SITE_CHARS:
            r2 = await client().get(link, headers=headers, timeout=SITE_TIMEOUT_S, follow_redirects=True)
            if r2.status_code == 200:
                text = (text + " | " + html_text(r2.text))[:SITE_CHARS]
        return text
    except Exception as e:  # noqa: BLE001 - a slow or broken site just means "no website text"
        logger.info("site fetch failed %s: %s", url, e)
        return ""


# ---------------------------------------------------------------- facts

EMPTY_FACTS = {"mobile_service": "unknown", "hours_24_7": "unknown", "truck_focused": False, "prices": [],
               "wait_time": None, "payment": [], "red_flags": [], "praise": [], "last_review_date": None}
PAYMENTS = ("comchek", "efs", "t-chek", "card", "cash")
SERVICE_PRICE = re.compile(r"repair|tire|tow|road ?call|service call|labor|per hour|hourly|an hour|/ ?hr|hook|"
                           r"per mile|a mile|/ ?mi\b|deliver|install|mount|patch|jump|unlock|reefer|diag", re.I)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9$.]+", " ", (s or "").lower()).strip()


def _quoted(quote: str, source: str) -> bool:
    """The quote (or its first 40 normalized characters) must appear in the source text."""
    q = _norm(quote)
    return bool(q) and (q in source or q[:40] in source)


def _tri(v: Any) -> Any:
    return v if isinstance(v, bool) else "unknown"


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f >= 0 else None


def clean_facts(raw: dict, reviews_text: str, website_text: str) -> dict:
    """Keeps only facts backed by the text: quotes must be verbatim and contain the amount."""
    src = {"review": _norm(reviews_text), "website": _norm(website_text)}
    both = src["review"] + " " + src["website"]
    out = dict(EMPTY_FACTS)
    out["mobile_service"] = _tri(raw.get("mobile_service"))
    out["hours_24_7"] = _tri(raw.get("hours_24_7"))
    out["truck_focused"] = raw.get("truck_focused") is True
    prices = []
    for p in raw.get("prices") or []:
        if not isinstance(p, dict):
            continue
        lo, hi = _num(p.get("amount_usd_low")), _num(p.get("amount_usd_high"))
        quote = str(p.get("quote") or "")[:120]
        source = p.get("source") if p.get("source") in src else "review"
        if lo is None or hi is None or not quote:
            continue
        lo, hi = min(lo, hi), max(lo, hi)
        if not _quoted(quote, src[source]):
            source = "website" if source == "review" else "review"
            if not _quoted(quote, src[source]):
                continue  # not in the text: drop it
        amounts = {int(a) for a in re.findall(r"\d+", quote.replace(",", ""))}
        if int(lo) not in amounts and int(hi) not in amounts:
            continue  # the number must be the one written in the quote
        what = str(p.get("what") or "price")[:40]
        if not SERVICE_PRICE.search(quote):
            continue  # fuel, showers, food: the quote itself must be about a repair/tow/road call
        if any((x["what"], x["amount_usd_low"], x["amount_usd_high"]) == (what, lo, hi) for x in prices):
            continue  # same price twice (review + website)
        prices.append({"what": what, "amount_usd_low": lo, "amount_usd_high": hi,
                       "source": source, "quote": quote})
    out["prices"] = prices[:6]
    w = raw.get("wait_time")
    wq = str((w or {}).get("quote") or "") if isinstance(w, dict) else ""
    # A wait time needs a number or "hour" in the quote ("showed up quick" is not a time).
    if isinstance(w, dict) and _quoted(wq, both) and re.search(r"\d|hour|half", wq, re.I):
        lo, hi = _num(w.get("typical_min_low")), _num(w.get("typical_min_high"))
        nums = {int(n) for n in re.findall(r"\d+", wq)} or ({30, 60} if re.search(r"hour|half", wq, re.I) else set())
        if lo is not None and hi is not None:
            # Minutes or hours written in the quote bound the range ("usually 30 min" -> 30-30, not 30-45).
            allowed = nums | {n * 60 for n in nums} | {0}
            if int(lo) not in allowed or int(hi) not in allowed:
                lo = hi = None
        if lo is not None and hi is not None and max(lo, hi) > 0:
            in_site = _quoted(wq, src["website"]) and not _quoted(wq, src["review"])
            out["wait_time"] = {"typical_min_low": min(lo, hi), "typical_min_high": max(lo, hi),
                                "quote": str(w["quote"])[:120], "source": "website" if in_site else "review"}
    out["payment"] = [p for p in PAYMENTS if p in [str(x).lower() for x in raw.get("payment") or []]
                      and (p.replace("-", "") in both.replace("-", "") or p in ("card", "cash"))]
    out["red_flags"] = [str(x)[:60] for x in (raw.get("red_flags") or [])[:2] if str(x).strip()]
    out["praise"] = [str(x)[:60] for x in (raw.get("praise") or [])[:2] if str(x).strip()]
    d = str(raw.get("last_review_date") or "")
    out["last_review_date"] = d[:10] if re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", d) else None
    return out


def _parse_json(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


def source_texts(c: dict, website: str) -> tuple[str, str]:
    reviews = "\n".join(f"- ({r['date'] or 'date unknown'}, {r['rating'] or '?'} stars) {r['text']}"
                        for r in c["reviews"] if r["text"])
    return reviews, website


async def extract_facts(c: dict, website: str, budget: Budget) -> dict:
    reviews, site = source_texts(c, website)
    if not reviews.strip() and not site.strip():
        return dict(EMPTY_FACTS)  # nothing to read: everything stays unknown, no LLM call
    prompt = f"Business: {c['name']}\nREVIEWS:\n{reviews or '(none)'}\nWEBSITE TEXT:\n{site or '(none)'}"
    chat = LlmChat(api_key=os.environ["EMERGENT_LLM_KEY"], session_id=f"facts-{uuid.uuid4()}",
                   system_message=EXTRACT_SYSTEM).with_model(*EXTRACT_MODEL)
    budget.llm_calls += 1
    budget.llm_in_chars += len(EXTRACT_SYSTEM) + len(prompt)
    raw = await asyncio.wait_for(chat.send_message(UserMessage(text=prompt)), timeout=budget.left(1.5))
    budget.llm_out_chars += len(raw or "")
    facts = clean_facts(_parse_json(raw), reviews, site)
    if not facts["last_review_date"]:
        dates = sorted(r["date"] for r in c["reviews"] if r["date"])
        facts["last_review_date"] = dates[-1] if dates else None
    return facts


# ---------------------------------------------------------------- ranking

def _fresh(date: Optional[str], now: datetime) -> bool:
    try:
        d = datetime.strptime(date or "", "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return (now - d).days <= 183


def score(p: dict, mode: str, now: Optional[datetime] = None) -> float:
    f = p["facts"]
    now = now or datetime.now(timezone.utc)
    s = 0.0
    s += 3 if p.get("open_now") else 0
    s += 2 if (p.get("listed_24_7") or f["hours_24_7"] is True) else 0
    s += 3 if (mode == "mobile_needed" and f["mobile_service"] is True) else 0
    s += 2 if f["truck_focused"] else 0
    if p.get("rating"):
        s += min(3.0, (p["rating"] / 5) * math.log1p(p.get("rating_count") or 0) / math.log(101) * 3)
    s += 1 if f["prices"] else 0
    s += 1 if _fresh(f["last_review_date"], now) else 0
    s -= 2 * len(f["red_flags"])
    s -= (0.5 if mode == "mobile_needed" else 1.0) * (p.get("distance_mi") or 0) / 10
    return round(s, 2)


def rank(providers: list[dict], mode: str, now: Optional[datetime] = None) -> list[dict]:
    """Best first. In mobile_needed, shop-only places go to the end with a 'shop only' tag."""
    for p in providers:
        p["score"] = score(p, mode, now)
        p["shop_only"] = mode == "mobile_needed" and p["facts"]["mobile_service"] is not True
    return sorted(providers, key=lambda p: (p["shop_only"], -p["score"]))[:TOP]


# ---------------------------------------------------------------- run

async def research(kind: str, mode: str, lat: float, lng: float, budget: Optional[Budget] = None) -> dict:
    """Returns {"providers": [...ranked], "source": "google"|"here"|"none", "stats": {...}}."""
    budget = budget or Budget()
    radius = RADIUS_MI.get(mode, 50)
    t = _now()
    source = "google"
    cands: list[dict] = []
    if os.environ.get("GOOGLE_MAPS_API_KEY"):
        try:
            cands = await asyncio.wait_for(google_candidates(kind, lat, lng, radius, budget), timeout=budget.left(10))
        except Exception as e:  # noqa: BLE001
            logger.warning("Google candidates failed, trying HERE: %s", e)
            cands = []
    if not cands:
        source = "here"
        try:
            cands = await here_candidates(kind, lat, lng, radius, budget)
        except Exception as e:  # noqa: BLE001
            logger.warning("HERE candidates failed: %s", e)
        if not cands:
            source = "none"
    t = budget.mark("candidates", t)
    if not cands:
        return {"providers": [], "source": source, "stats": budget.report()}

    if source == "here":
        providers = [{**c, "facts": dict(EMPTY_FACTS)} for c in cands]
        return {"providers": rank(providers, mode), "source": source, "stats": budget.report()}

    # Websites: whatever isn't back within 6 s goes without its site.
    site_tasks = [asyncio.create_task(site_text(c["website"])) for c in cands]
    done, pending = await asyncio.wait(site_tasks, timeout=min(SITE_STAGE_S, budget.left(8)))
    for p in pending:
        p.cancel()
    sites = [st.result() if st in done and not st.exception() else "" for st in site_tasks]
    t = budget.mark("websites", t)

    results = await asyncio.gather(*(extract_facts(c, s, budget) for c, s in zip(cands, sites)),
                                   return_exceptions=True)
    providers = []
    for c, s, f in zip(cands, sites, results):
        if isinstance(f, Exception):
            logger.warning("facts failed for %s: %r", c["name"], f)
            f = dict(EMPTY_FACTS)
        providers.append({**c, "facts": f, "website_read": bool(s)})
    budget.mark("facts", t)
    return {"providers": rank(providers, mode), "source": source, "stats": budget.report()}
