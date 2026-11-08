"""QA 2026-11-02, bug 3 (Н21): "call the cheapest" after the breakdown list crashed with
TypeError: 'Provider' object is not subscriptable (breakdown.pick_superlative). The research turn left the
providers in the dialog as dicts, the database store brought them back as Provider models, and the follow-up
indexed them as dicts. The session holds Provider models from the research on; every follow-up reads those."""
from conftest import run

from models import AgentRequest, Provider
from services import breakdown
from services import providers_search as ps
from services.dialog_state import DialogState

SAC = {"lat": 38.5816, "lng": -121.4944}


def _cand(id, name, mi, phone, low=None, wait=None, reviews=()):
    facts = {**ps.EMPTY_FACTS, "mobile_service": True}
    if low is not None:
        facts["prices"] = [{"what": "road call", "amount_usd_low": low, "amount_usd_high": low, "source": "review",
                            "quote": f"${low} road call"}]
    if wait is not None:
        facts["wait_time"] = {"typical_min_low": wait, "typical_min_high": wait, "quote": "came fast"}
    return {"id": id, "name": name, "lat": 38.58, "lng": -121.5, "phone": phone, "website": None,
            "address": "1 A St, Sacramento, CA", "rating": 4.5, "rating_count": 20, "distance_mi": mi,
            "open_now": True, "listed_24_7": False, "shop_only": False, "score": 1.0, "source": "google",
            "types": ["car_repair"], "reviews": [{"rating": 5, "text": t, "date": None} for t in reviews],
            "facts": facts}


RANKED = [_cand("p1", "Saa Mobile Tire", 3.0, "+15550001", low=250, wait=90,
                reviews=["They came out to the yard in forty minutes, fair price."]),
          _cand("p2", "Gold Rush Truck Repair", 6.0, "+15550002", low=180),
          _cand("p3", "Delta Road Service", 1.5, "+15550003", wait=30)]


def _research_turn(monkeypatch) -> DialogState:
    async def research(kind, mode, lat, lng, budget=None):
        return {"providers": [dict(p) for p in RANKED], "source": "google", "stats": {"stages_ms": {}, "llm_calls": 0}}

    async def where(lat, lng):
        return "I-80, Sacramento, CA"

    async def summarize(d, mode, providers):
        return "Three shops nearby.", "Three shops nearby."

    monkeypatch.setattr(ps, "research", research)
    monkeypatch.setattr(breakdown, "where", where)
    monkeypatch.setattr(breakdown, "summarize", summarize)
    sess = DialogState()
    res = run(breakdown._run({"kind": "tire", "detail": "trailer tire", "mode": "mobile_needed"},
                             AgentRequest(text="trailer tire, I can't roll on it", session_id="bd", **SAC), sess, "test"))
    assert res.status == "providers" and [p.id for p in res.providers] == ["p1", "p2", "p3"]
    return sess


def _stores(sess: DialogState) -> dict:
    """The dialog as this worker keeps it, and as another worker loads it from the database store."""
    return {"memory": sess, "db": DialogState.from_json(sess.to_json())}


def test_call_the_cheapest_after_research(monkeypatch):
    for store, sess in _stores(_research_turn(monkeypatch)).items():
        res = run(breakdown.followup(AgentRequest(text="call the cheapest one", **SAC), sess, "call the cheapest one", "t"))
        assert res.dial_phone == "+15550002", store  # $180 beats $250; the closest has no price
        res = run(breakdown.followup(AgentRequest(text="call the fastest", **SAC), sess, "call the fastest", "t"))
        assert res.dial_phone == "+15550003", store


def test_route_to_the_closest_and_no_prices_line(monkeypatch, fake_http):
    from conftest import fixture_json
    fake_http.add("router.hereapi.com", "/v8/routes", json=fixture_json("here/route_sac_reno"))
    for store, sess in _stores(_research_turn(monkeypatch)).items():
        res = run(breakdown.followup(AgentRequest(text="route me to the closest", **SAC), sess, "route me to the closest", "t"))
        assert res.status == "route_set" and res.place.id == "p3", store
        assert {o.id for o in res.options} == {"p1", "p2"}, store
        sess["providers"] = [p.model_copy(update={"facts": p.facts.model_copy(update={"prices": []})})
                             for p in sess["providers"]]
        res = run(breakdown.followup(AgentRequest(text="call the cheapest", **SAC), sess, "call the cheapest", "t"))
        assert res.reply == "None of them mention prices. Want me to call Saa Mobile Tire, the top pick?", store


def test_session_keeps_provider_models_and_the_model_list_sees_reviews(monkeypatch):
    sess = _research_turn(monkeypatch)
    assert all(isinstance(p, Provider) for p in sess["providers"])
    assert [p.id for p in sess["places"]] == ["p1", "p2", "p3"] and sess["places"][0].category == "service"
    brief = breakdown._brief(sess["providers"])
    assert "Saa Mobile Tire" in brief and "forty minutes" in brief and "$180 road call" in brief


def test_a_new_request_with_a_superlative_is_not_a_pick_from_the_list(monkeypatch):
    """QA 2026-11-08: after a breakdown list, "cheapest diesel on my route" answered "None of them mention prices.
    Want me to call Saa Mobile...". A search for another kind of stop goes back to the normal agent, before any
    model call."""
    async def no_llm(*a, **k):
        raise AssertionError("the follow-up model must not be asked")

    monkeypatch.setattr(breakdown, "_llm", no_llm)
    sess = _research_turn(monkeypatch)
    for text in ("cheapest diesel on my route", "route me to the closest truck stop", "where can I park tonight",
                 "find the nearest rest area"):
        assert run(breakdown.followup(AgentRequest(text=text, **SAC), sess, text, "t")) is None, text
    res = run(breakdown.followup(AgentRequest(text="call the cheapest one", **SAC), sess, "call the cheapest one", "t"))
    assert res.dial_phone == "+15550002"


def test_questions_about_the_shops_that_mention_a_stop_stay_follow_ups(monkeypatch):
    """Review of the fix: these talk about the shops on the list, so they still reach the follow-up model."""
    asked = []

    async def llm(system, user):
        asked.append(user)
        return {"about_providers": True, "action": "answer", "index": 1, "reply": "Yes."}

    monkeypatch.setattr(breakdown, "_llm", llm)
    sess = _research_turn(monkeypatch)
    for text in ("can they come to the truck stop?", "I'm parked at a truck stop, who comes out fastest?",
                 "can I park my rig there overnight while they fix it", "is the second one open at the scale house",
                 "does Saa Mobile Tire come to the rest area"):
        res = run(breakdown.followup(AgentRequest(text=text, **SAC), sess, text, "t"))
        assert res is not None and res.reply == "Yes.", text
    assert len(asked) == 5


def test_out_of_fuel_the_diesel_questions_are_about_the_providers():
    p = []
    assert breakdown.is_new_search("where can I get diesel", p, "tire")
    assert not breakdown.is_new_search("where can I get diesel", p, "fuel")
    assert not breakdown.is_new_search("any of them bring diesel", p, "fuel")
    assert breakdown.is_new_search("find a truck stop", p, "fuel")


def test_superlative_pick_only_matches_the_shops():
    for text in ("call the cheapest", "call the cheapest one", "route me to the closest.", "call the fastest guy",
                 "take me to the nearest shop", "call the cheapest one please"):
        assert breakdown.SUPERLATIVE_PICK.search(text), text
    for text in ("cheapest diesel on my route", "closest truck stop", "the nearest Pilot please"):
        assert not breakdown.SUPERLATIVE_PICK.search(text), text
