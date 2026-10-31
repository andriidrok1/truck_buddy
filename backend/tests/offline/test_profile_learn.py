"""Learned driver preferences: summarising, the context line, place choice, rejection learning, show/forget."""
from datetime import datetime, timedelta, timezone

from conftest import fixture_json, run

from models import AgentRequest, Place, TruckProfile
from services import agent, memory, profile_learn
from services.agent import _chain_of, choose_place
from services.profile_learn import facts_line, is_empty, parse_facts

SAC = {"lat": 38.5816, "lng": -121.4944}
FACTS = {"chains_liked": ["Pilot"], "chains_avoided": ["Love's"], "avoids": ["tolls"],
         "stops_for_night_at": "truck stops", "style": "short", "notes": ["hauls reefer"]}


def test_parse_facts_is_strict_and_tolerant():
    raw = 'Sure: {"chains_liked": ["Pilot", "Pilot", " TA "], "chains_avoided": [], "avoids": "tolls", '\
          '"stops_for_night_at": "truck stops", "style": "short", "notes": ["a", "b", "c", "d"], "extra": 1}'
    f = parse_facts(raw)
    assert f["chains_liked"] == ["Pilot", "TA"] and f["avoids"] == [] and f["notes"] == ["a", "b", "c", "d"]
    assert f["stops_for_night_at"] == "truck stops" and f["style"] == "short" and "extra" not in f
    assert parse_facts("no json here") is None and parse_facts("[1,2]") is None
    assert is_empty(parse_facts("{}")) and not is_empty(FACTS) and is_empty(None)


def test_facts_line():
    assert facts_line(FACTS) == ("likes Pilot; avoids Love's; avoids tolls; stops for the night at truck stops; "
                                 "likes short answers; hauls reefer")
    assert facts_line({}) == "" and facts_line(None) == ""


def test_chain_of():
    assert _chain_of("Love's Travel Stop #123") == "Love's" and _chain_of("Pilot Travel Center") == "Pilot"
    assert _chain_of("TA Sparks") == "TA" and _chain_of("Flying J Travel Plaza") == "Flying J"
    assert _chain_of("Joe's Diner") is None


def _p(id, name, mi):
    return Place(id=id, name=name, category="truck_stop", lat=38.5, lng=-121.4, distance_mi=mi)


def test_choose_place_uses_learned_chains():
    loves, pilot, ta = _p("l", "Love's Travel Stop", 3.0), _p("p", "Pilot Travel Center", 6.0), _p("t", "TA", 9.0)
    assert choose_place([loves, pilot, ta], TruckProfile(), set()).id == "l"
    assert choose_place([loves, pilot, ta], TruckProfile(), set(), {"chains_liked": ["Pilot"]}).id == "p"
    assert choose_place([loves, pilot, ta], TruckProfile(), set(), {"chains_avoided": ["Love's"]}).id == "p"
    assert choose_place([loves], TruckProfile(), set(), {"chains_avoided": ["Love's"]}).id == "l"  # last resort


def test_summarize_uses_the_model_and_parses(fake_llm):
    fake_llm.on(r"^Driver said", '{"chains_liked": ["Pilot"], "style": "short"}')
    f = run(profile_learn.summarize(["I like Pilot", "keep it short"]))
    assert f["chains_liked"] == ["Pilot"] and f["style"] == "short" and f["chains_avoided"] == []
    assert run(profile_learn.summarize([])) is None
    fake_llm.rules.clear()
    assert run(profile_learn.summarize(["x"])) is None  # model down -> no facts, no crash


def test_refresh_if_due(monkeypatch, fake_llm):
    store = {}
    calls = {"recent": 0}

    async def get_facts(driver):
        return store.get(driver)

    async def set_facts(driver, facts, turns_seen):
        store[driver] = {"facts": facts, "turns_seen": turns_seen, "updated_at": datetime.now(timezone.utc)}

    async def count_turns(driver):
        return calls["turns"]

    async def recent(driver, limit):
        calls["recent"] += 1
        return [{"role": "driver", "text": "I like Pilot", "at": "x"}, {"role": "copilot", "text": "ok", "at": "x"}]

    for name, fn in (("get_facts", get_facts), ("set_facts", set_facts), ("count_turns", count_turns),
                     ("recent", recent)):
        monkeypatch.setattr(memory, name, fn)
    fake_llm.on(r"^Driver said", '{"chains_liked": ["Pilot"]}')
    calls["turns"] = 3
    assert run(profile_learn.refresh_if_due("d")) is None and calls["recent"] == 0  # too little history
    calls["turns"] = 7
    assert run(profile_learn.refresh_if_due("d"))["chains_liked"] == ["Pilot"] and calls["recent"] == 1
    calls["turns"] = 15
    assert run(profile_learn.refresh_if_due("d"))["chains_liked"] == ["Pilot"] and calls["recent"] == 1  # not due
    calls["turns"] = 27
    run(profile_learn.refresh_if_due("d"))
    assert calls["recent"] == 2  # 20 new turns
    store["d"]["updated_at"] = datetime.now(timezone.utc) - timedelta(days=2)
    calls["turns"] = 28
    run(profile_learn.refresh_if_due("d"))
    assert calls["recent"] == 3  # a day old and new turns
    monkeypatch.setenv("PROFILE_LEARN", "0")
    assert run(profile_learn.refresh_if_due("d")) is None


def test_context_prefers_facts_over_raw_lines(agent_sandbox):
    prov = agent_sandbox.provider()
    sess = {"history": [], "places": [], "past": ["I like Pilot"], "facts": FACTS}
    ctx = prov._context(AgentRequest(text="hi", **SAC), sess)
    assert "knows about this driver" in ctx and "likes Pilot" in ctx and "Long-term memory" not in ctx
    sess["facts"] = None
    ctx = prov._context(AgentRequest(text="hi", **SAC), sess)
    assert "Long-term memory" in ctx and "I like Pilot" in ctx


def test_three_rejections_teach_an_avoided_chain(agent_sandbox, fake_http, monkeypatch):
    fake_http.add("router.hereapi.com", "/v8/routes", json=fixture_json("here/route_sac_reno"))
    fake_http.add("api.weather.gov", "/alerts/active", json={"features": []})
    fake_http.add("cwwp2.dot.ca.gov", "/data/", json={"data": []})
    saved = {}

    async def set_facts(driver, facts, turns_seen):
        saved[driver] = facts

    async def get_facts(driver):
        return None

    monkeypatch.setattr(memory, "set_facts", set_facts)
    monkeypatch.setattr(memory, "get_facts", get_facts)
    places = [_p(f"l{i}", f"Love's Travel Stop #{i}", 2.0 + i) for i in range(4)]
    places.append(_p("p", "Pilot Travel Center", 9.0))
    for pl in places:
        pl.address = "1 Main St, Lodi, CA"
    agent_sandbox.places_provider.places = places
    agent_sandbox.llm.on(r"truck stop", {"intent": "find", "category": "truck_stop"})
    agent_sandbox.llm.on(r"not that one", {"intent": "pick", "place_index": None, "category": "truck_stop"})
    prov = agent_sandbox.provider()
    first = run(prov.handle(AgentRequest(text="find a truck stop", session_id="rj", **SAC), "drv"))
    assert first.place.id == "l0"
    r2 = run(prov.handle(AgentRequest(text="not that one", session_id="rj", active_place=first.place, **SAC), "drv"))
    r3 = run(prov.handle(AgentRequest(text="not that one", session_id="rj", active_place=r2.place, **SAC), "drv"))
    assert "stop suggesting" not in r2.reply and "stop suggesting" not in r3.reply
    r4 = run(prov.handle(AgentRequest(text="not that one", session_id="rj", active_place=r3.place, **SAC), "drv"))
    assert r4.reply.endswith("Got it, I'll stop suggesting Love's.")
    assert r4.place.id == "p"  # the learned avoid steers the pick away from the chain
    assert saved["drv"]["chains_avoided"] == ["Love's"]


def test_memory_intents(agent_sandbox, monkeypatch):
    cleared = []

    async def clear_facts(driver):
        cleared.append(driver)
        return 1

    monkeypatch.setattr(memory, "clear_facts", clear_facts)
    agent_sandbox.llm.on(r"know about me", {"intent": "memory", "query": "show"})
    agent_sandbox.llm.on(r"forget", {"intent": "memory", "query": "forget"})
    prov = agent_sandbox.provider()
    res = run(prov.handle(AgentRequest(text="what do you know about me", session_id="mm", **SAC), "drv"))
    assert res.reply == profile_learn.KNOW_NOTHING
    agent._SESSIONS["mm"]["facts"] = FACTS
    res = run(prov.handle(AgentRequest(text="what do you know about me", session_id="mm", **SAC), "drv"))
    assert res.reply == f"Here's what I know: {facts_line(FACTS)}."
    res = run(prov.handle(AgentRequest(text="forget that", session_id="mm", **SAC), "drv"))
    assert res.reply == profile_learn.FORGOT and cleared == ["drv"] and agent._SESSIONS["mm"]["facts"] is None


def test_memory_question_before_facts_answers_from_the_past(agent_sandbox, monkeypatch):
    """Fewer than 5 turns = no facts yet; "what's my dog's name?" still gets an answer from the raw lines."""
    async def recent(driver, limit):
        return [{"role": "driver", "text": "Remember this: my dog's name is Biscuit.", "at": "x"}]

    async def get_facts(driver):
        return None

    monkeypatch.setattr(memory, "recent", recent)
    monkeypatch.setattr(memory, "get_facts", get_facts)
    agent_sandbox.llm.on(r"^Driver said earlier", "Your dog's name is Biscuit.")  # the recall call, matched first
    agent_sandbox.llm.on(r"^What is my dog", {"intent": "memory", "query": "show"})  # anchored: the recall text has "dog" too
    prov = agent_sandbox.provider()
    res = run(prov.handle(AgentRequest(text="What is my dog's name?", session_id="rc", **SAC), "drv"))
    assert res.status == "chat" and res.reply == "Your dog's name is Biscuit."
    sys_prompt, user_text, _ = agent_sandbox.llm.calls[-1]
    assert sys_prompt == profile_learn.RECALL_PROMPT and "- Remember this: my dog's name is Biscuit." in user_text
    assert user_text.endswith("Question: What is my dog's name?")

    # general question, lines are only requests: the model says NONE and the driver hears the old line
    # (QA 2026-10-30 heard "You're looking for a place to park tonight, and you want a truck stop.")
    async def requests_only(driver, limit):
        return [{"role": "driver", "text": "where can I park tonight", "at": "x"},
                {"role": "driver", "text": "find me a truck stop", "at": "x"}]

    monkeypatch.setattr(memory, "recent", requests_only)
    agent_sandbox.llm.rules = [r for r in agent_sandbox.llm.rules if "earlier" not in r[0].pattern]
    agent_sandbox.llm.on(r"^Driver said earlier", "NONE.")
    agent_sandbox.llm.on(r"^what do you know", {"intent": "memory", "query": "show"})
    res = run(prov.handle(AgentRequest(text="what do you know about me", session_id="rc4", **SAC), "drv4"))
    assert res.reply == profile_learn.KNOW_NOTHING
    assert "NONE" in profile_learn.RECALL_PROMPT and "preference" in profile_learn.RECALL_PROMPT
    agent_sandbox.llm.rules = [r for r in agent_sandbox.llm.rules if "earlier" not in r[0].pattern]
    agent_sandbox.llm.on(r"^Driver said earlier", "Your dog's name is Biscuit.")

    # nothing said before: the old line
    async def nothing(driver, limit):
        return []

    monkeypatch.setattr(memory, "recent", nothing)
    res = run(prov.handle(AgentRequest(text="What is my dog's name?", session_id="rc2", **SAC), "drv2"))
    assert res.reply == profile_learn.KNOW_NOTHING

    # the recall model down (no canned reply = the fake raises): the old line, never an error
    monkeypatch.setattr(memory, "recent", recent)
    agent_sandbox.llm.rules = [r for r in agent_sandbox.llm.rules if "earlier" not in r[0].pattern]
    res = run(prov.handle(AgentRequest(text="What is my dog's name?", session_id="rc3", **SAC), "drv3"))
    assert res.status == "chat" and res.reply == profile_learn.KNOW_NOTHING


def test_facts_load_with_the_past(agent_sandbox, monkeypatch):
    async def recent(driver, limit):
        return [{"role": "driver", "text": "hello", "at": "x"}]

    async def get_facts(driver):
        return {"facts": FACTS, "turns_seen": 30, "updated_at": datetime.now(timezone.utc)}

    monkeypatch.setattr(memory, "recent", recent)
    monkeypatch.setattr(memory, "get_facts", get_facts)
    agent_sandbox.llm.default = {"intent": "chat", "reply": "Hi."}
    prov = agent_sandbox.provider()
    run(prov.handle(AgentRequest(text="hi", session_id="lf", **SAC), "drv"))
    assert agent._SESSIONS["lf"]["facts"] == FACTS and agent._SESSIONS["lf"]["past"] == ["hello"]
    assert "likes Pilot" in agent_sandbox.llm.calls[-1][1]
