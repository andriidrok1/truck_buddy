"""Breakdown scenario unit tests: car-only filter, fact extraction guard, ranking in both modes.

Deterministic tests need no network. test_live_extraction_* call gpt-5.4-mini on fixture reviews
(skipped without OPENAI_API_KEY).
"""
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from services import breakdown as bd  # noqa: E402
from services import providers_search as ps  # noqa: E402


def gplace(name, types=(), reviews=(), phone="(916) 555-0100", status="OPERATIONAL"):
    return {"id": name, "displayName": {"text": name}, "types": list(types), "nationalPhoneNumber": phone,
            "businessStatus": status, "reviews": [{"text": {"text": t}, "rating": 5} for t in reviews]}


# ---------------------------------------------------------------- car-only filter

def test_car_only_shop_dropped():
    p = gplace("Joe's Auto Tire", ["car_repair"], ["Great price on my Civic tires", "Fast oil change"])
    assert not ps.is_truck_capable(p)
    assert not ps.keep_candidate(p)


@pytest.mark.parametrize("p", [
    gplace("Big Rig Tire Service", ["car_repair"]),
    gplace("Valley Commercial Tire"),
    gplace("Mike's Mobile Service", reviews=["Came out to my semi on I-80 at 2am"]),
    gplace("AAA Repair", ["car_repair"], ["They fixed our fleet trucks"]),
    gplace("Heavy-Duty Diesel Works"),
])
def test_truck_words_kept(p):
    assert ps.is_truck_capable(p)
    assert ps.keep_candidate(p)


def test_no_phone_or_closed_dropped():
    assert not ps.keep_candidate(gplace("Big Rig Tire", phone=None))
    assert not ps.keep_candidate(gplace("Big Rig Tire", status="CLOSED_PERMANENTLY"))


def test_24_7_detection():
    assert ps.is_24_7({"periods": [{"open": {"day": 0, "hour": 0, "minute": 0}}]})
    assert ps.is_24_7({"weekdayDescriptions": [f"Day: Open 24 hours" for _ in range(7)]})
    assert not ps.is_24_7({"weekdayDescriptions": ["Monday: 8:00 AM – 5:00 PM"] * 7})


# ---------------------------------------------------------------- extraction guard

REVIEWS = ("- (2026-08-01, 5 stars) Blew a trailer tire on I-80, they came out in 45 minutes and charged $450 "
           "for the tire installed. Took Comchek.\n"
           "- (2026-05-02, 2 stars) Called at 2am, they doubled the price at night.")
SITE = "Mobile truck tire service 24/7. Road call fee $150. We accept EFS and Comchek."


def test_clean_facts_keeps_quoted_prices():
    raw = {"mobile_service": True, "hours_24_7": True, "truck_focused": True,
           "prices": [{"what": "trailer tire installed", "amount_usd_low": 450, "amount_usd_high": 450,
                       "source": "review", "quote": "charged $450 for the tire installed"},
                      {"what": "road call fee", "amount_usd_low": 150, "amount_usd_high": 150,
                       "source": "website", "quote": "Road call fee $150"}],
           "wait_time": {"typical_min_low": 45, "typical_min_high": 45, "quote": "they came out in 45 minutes"},
           "payment": ["comchek", "efs", "t-chek"], "red_flags": ["doubled the price at night"],
           "praise": ["came out fast"], "last_review_date": "2026-08-01"}
    f = ps.clean_facts(raw, REVIEWS, SITE)
    assert [p["amount_usd_low"] for p in f["prices"]] == [450, 150]
    assert f["wait_time"]["typical_min_high"] == 45
    assert f["payment"] == ["comchek", "efs"]  # t-chek is not in the text
    assert f["red_flags"] == ["doubled the price at night"]


def test_clean_facts_drops_invented():
    raw = {"prices": [{"what": "road call fee", "amount_usd_low": 99, "amount_usd_high": 99, "source": "review",
                       "quote": "road call is only $99"},  # not in the text
                      {"what": "trailer tire installed", "amount_usd_low": 300, "amount_usd_high": 300,
                       "source": "review", "quote": "charged $450 for the tire installed"}],  # wrong amount
           "wait_time": {"typical_min_low": 0, "typical_min_high": 0, "quote": "they doubled the price"},
           "mobile_service": "maybe"}
    f = ps.clean_facts(raw, REVIEWS, SITE)
    assert f["prices"] == []
    assert f["wait_time"] is None
    assert f["mobile_service"] == "unknown"


def test_empty_text_is_unknown_without_llm_call():
    c = {"name": "X", "reviews": [], "website": None}
    budget = ps.Budget()
    f = asyncio.run(ps.extract_facts(c, "", budget))
    assert f == ps.EMPTY_FACTS and budget.llm_calls == 0


# ---------------------------------------------------------------- ranking

def prov(pid, miles, mobile=True, open_now=True, prices=False, flags=0, rating=4.6, n=120, date="2026-09-01"):
    facts = dict(ps.EMPTY_FACTS, mobile_service=mobile, truck_focused=True,
                 prices=[{"what": "x", "amount_usd_low": 1, "amount_usd_high": 1}] if prices else [],
                 red_flags=["no-show"] * flags, last_review_date=date)
    return {"id": pid, "distance_mi": miles, "open_now": open_now, "listed_24_7": False, "rating": rating,
            "rating_count": n, "facts": facts}


NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)


def test_rank_mobile_needed_puts_shops_last():
    shop = prov("shop", 3, mobile="unknown")
    far_mobile = prov("mobile", 30)
    ranked = ps.rank([shop, far_mobile], "mobile_needed", NOW)
    assert [p["id"] for p in ranked] == ["mobile", "shop"]
    assert ranked[1]["shop_only"] and not ranked[0]["shop_only"]


def test_rank_shop_ok_prefers_close_shop():
    ranked = ps.rank([prov("shop", 3, mobile="unknown"), prov("mobile", 30)], "shop_ok", NOW)
    assert ranked[0]["id"] == "shop" and not ranked[0]["shop_only"]


def test_red_flags_and_distance_penalties():
    assert ps.score(prov("a", 10, flags=1), "shop_ok", NOW) == pytest.approx(ps.score(prov("a", 10), "shop_ok", NOW) - 2)
    near, far = ps.score(prov("a", 0), "mobile_needed", NOW), ps.score(prov("a", 20), "mobile_needed", NOW)
    assert near - far == pytest.approx(1.0)  # -0.5 per 10 mi in mobile_needed
    assert ps.score(prov("a", 0), "shop_ok", NOW) - ps.score(prov("a", 20), "shop_ok", NOW) == pytest.approx(2.0)


def test_rank_top_six():
    assert len(ps.rank([prov(str(i), i) for i in range(9)], "shop_ok", NOW)) == 6


# ---------------------------------------------------------------- dialog

@pytest.mark.parametrize("d, q", [
    ({"kind": "tire", "mode": "unknown", "tire_position": None}, bd.QUESTIONS["tire"]),
    ({"kind": "tire", "mode": "mobile_needed", "tire_position": "trailer"}, None),
    ({"kind": "mechanic", "mode": "unknown"}, bd.QUESTIONS["mechanic"]),
    ({"kind": "mechanic", "mode": "shop_ok"}, None),
    ({"kind": "tow", "mode": "unknown"}, None),
])
def test_one_question_only_when_it_matters(d, q):
    assert bd.needs_question(d) == q


def test_prompt_block_has_triggers_and_fields():
    for phrase in ("I blew a tire", "flat on the trailer", "truck won't start", "losing power", "need a tow",
                   "reefer quit", "ran out of fuel", "brake light on", "landing gear stuck", "who can fix"):
        assert phrase in bd.PROMPT
    for field in ('"kind"', '"detail"', '"mode"', '"tire_position"', '"tire_size"'):
        assert field in bd.PROMPT


def test_system_prompt_includes_breakdown_block():
    from services import agent
    assert "{breakdown}" in agent.SYSTEM


def test_from_intent_asks_once_then_skips():
    from models import AgentRequest
    req = AgentRequest(text="x", lat=38.58, lng=-121.49)
    sess = {"history": [], "places": []}
    r = asyncio.run(bd.from_intent({"kind": "tire", "mode": "unknown"}, req, sess, "I blew a tire", "t"))
    assert r.status == "chat" and r.reply == bd.QUESTIONS["tire"] and sess["breakdown_pending"]["kind"] == "tire"


def test_fit_spoken_cuts_whole_sentences():
    long = "Okay, here's what I found. " + " ".join(f"Sentence number {i} has some words in it." for i in range(15))
    out = bd.fit_spoken(long + " " + bd.END)
    assert len(out.split()) <= bd.MAX_WORDS and out.endswith(bd.END)
    assert "in it. Tap one" in out  # ends on a full sentence


def test_fuel_and_shower_prices_are_not_repair_prices():
    raw = {"prices": [{"what": "other: diesel", "amount_usd_low": 6, "amount_usd_high": 6, "source": "review",
                       "quote": "$6.41 for diesel here"},
                      {"what": "hourly labor", "amount_usd_low": 18, "amount_usd_high": 18, "source": "review",
                       "quote": "purchase my shower for $18.00"}]}
    assert ps.clean_facts(raw, "- $6.41 for diesel here. purchase my shower for $18.00", "")["prices"] == []


def test_vague_hour_wait_is_bounded():
    raw = {"wait_time": {"typical_min_low": 60, "typical_min_high": 300, "quote": "back on the road in less than an hour"}}
    assert ps.clean_facts(raw, "- back on the road in less than an hour.", "")["wait_time"] is None
    raw = {"wait_time": {"typical_min_low": 0, "typical_min_high": 60, "quote": "back on the road in less than an hour"}}
    assert ps.clean_facts(raw, "- back on the road in less than an hour.", "")["wait_time"]["typical_min_high"] == 60


def test_wait_range_must_match_quote():
    raw = {"wait_time": {"typical_min_low": 30, "typical_min_high": 45, "quote": "usually 30 min out"}}
    assert ps.clean_facts(raw, "- usually 30 min out to me", "")["wait_time"] is None
    raw = {"wait_time": {"typical_min_low": 0, "typical_min_high": 120, "quote": "2 hr later they arrived"}}
    w = ps.clean_facts(raw, "- 2 hr later they arrived", "")["wait_time"]
    assert w["typical_min_high"] == 120 and w["source"] == "review"
    raw = {"wait_time": {"typical_min_low": 30, "typical_min_high": 60, "quote": "30-60 min ETA guaranteed"}}
    assert ps.clean_facts(raw, "", "30-60 min ETA guaranteed in the county")["wait_time"]["source"] == "website"


def test_pending_answer_keeps_kind_and_can_back_out(monkeypatch):
    from models import AgentRequest
    req = AgentRequest(text="x", lat=38.58, lng=-121.49)
    started = {}

    async def fake_llm(system, text, timeout=25):
        return {"kind": "trailer", "detail": "trailer", "mode": "mobile_needed", "changed_topic": "Reno" in text}

    monkeypatch.setattr(bd, "_llm", fake_llm)
    monkeypatch.setattr(bd, "start_research", lambda d, *a: started.update(d) or "started")
    sess = {"breakdown_pending": {"text": "I blew a tire", "kind": "tire", "detail": "blew a tire"}}
    assert asyncio.run(bd._handle(req, sess, "trailer, can't roll", "t")) == "started"
    assert started["kind"] == "tire" and started["detail"] == "blew a tire"
    sess = {"breakdown_pending": {"text": "I blew a tire", "kind": "tire", "detail": "blew a tire"}}
    assert asyncio.run(bd._handle(req, sess, "never mind, take me to Reno", "t")) is None
    assert "breakdown_pending" not in sess


def test_no_false_24_7_guard():
    jb = dict(prov("jb", 1), name="J B BROS TRUCK (Speak Punjabi)")
    reno = dict(prov("r", 2), name="Reno Truck and Trailer Repair", listed_24_7=True)
    text = "Best bet is J B BROS TRUCK: 0.7 miles away, open 24/7, mobile. Reno Truck is open twenty-four seven."
    out = bd.no_false_24_7(text, [jb, reno])
    assert "J B BROS TRUCK: 0.7 miles away, mobile." in out and "Reno Truck is open twenty-four seven." in out


def test_superlatives_in_code():
    a, b = dict(prov("a", 5, prices=True), name="A"), dict(prov("b", 1), name="B")
    assert bd.pick_superlative([a, b], "cheapest")["id"] == "a"  # one priced provider is enough
    assert bd.pick_superlative([b], "cheapest") is None
    assert bd.pick_superlative([a, b], "closest")["id"] == "b"
    assert bd.SUPERLATIVE.search("call the cheapest one") and bd.DO_CALL.search("call the cheapest one")


def test_price_filter_and_dedupe():
    raw = {"prices": [{"what": "other", "amount_usd_low": 15, "amount_usd_high": 15, "source": "review",
                       "quote": "showers $15, open 24 hours"},
                      {"what": "hourly labor", "amount_usd_low": 180, "amount_usd_high": 180, "source": "website",
                       "quote": "$180 per hour"},
                      {"what": "hourly labor", "amount_usd_low": 180, "amount_usd_high": 180, "source": "website",
                       "quote": "$180 per hour"}]}
    f = ps.clean_facts(raw, "- showers $15, open 24 hours", "Rates: $180 per hour")
    assert [p["amount_usd_low"] for p in f["prices"]] == [180]


def test_wait_text():
    assert bd.wait_text({"typical_min_low": 0, "typical_min_high": 60}) == "within an hour"
    assert bd.wait_text({"typical_min_low": 120, "typical_min_high": 120}) == "about 2 hours"
    assert bd.wait_text({"typical_min_low": 0, "typical_min_high": 120}) == "up to 2 hours"
    assert bd.wait_text({"typical_min_low": 30, "typical_min_high": 60}) == "30-60 min"


def test_known_facts_never_claim_unknowns():
    p = prov("a", 3, mobile="unknown", open_now=None)
    p.update(name="A", shop_only=False)
    facts = " ".join(bd.known(p))
    assert "open 24/7" not in facts and "mobile" not in facts and "$" not in facts
    assert "NOT listed as 24/7" in facts


def test_cheapest_needs_two_priced():
    a, b, c = prov("a", 1, prices=True), prov("b", 2), prov("c", 3)
    for x, n in ((a, "A"), (b, "B"), (c, "C")):
        x["name"] = n
    assert bd.cheapest_fastest([a, b, c])[0] is None


# ---------------------------------------------------------------- live extraction (LLM)

live = pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="no LLM key")
_LOOP = asyncio.new_event_loop()  # one loop for all live LLM calls (the LLM client is bound to its first loop)


def run_live(coro):
    return _LOOP.run_until_complete(asyncio.wait_for(coro, timeout=60))


@live
def test_live_extraction_prices_time_flags():
    c = {"name": "Fixture Tire", "reviews": [
        {"text": "Blew a trailer tire on I-80, they came out in 45 minutes and charged $450 for the tire installed.",
         "rating": 5, "date": "2026-08-01"},
        {"text": "Called at 2am, they doubled the price at night. Never again.", "rating": 1, "date": "2026-07-01"}],
         "website": None}
    f = run_live(ps.extract_facts(c, SITE, ps.Budget(40)))
    amounts = {p["amount_usd_low"] for p in f["prices"]}
    assert 450 in amounts and 150 in amounts
    assert f["wait_time"] and f["wait_time"]["typical_min_high"] <= 60
    assert f["mobile_service"] is True
    assert f["red_flags"]


@live
def test_live_extraction_no_facts_stays_unknown():
    c = {"name": "Fixture Shop", "reviews": [{"text": "Nice people.", "rating": 5, "date": "2026-08-01"}],
         "website": None}
    f = run_live(ps.extract_facts(c, "", ps.Budget(40)))
    assert f["prices"] == [] and f["wait_time"] is None
    assert f["mobile_service"] == "unknown" and f["hours_24_7"] == "unknown"
