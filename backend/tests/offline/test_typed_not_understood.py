"""A typed request the copilot does not understand is told so in words about the meaning, never "didn't catch that"
(the words arrived as written). Voice, and an older app that does not say how the request was made, keep the old line."""
from conftest import run

from models import AgentRequest
from services.agent.replies import NOT_UNDERSTOOD, NOT_UNDERSTOOD_TYPED

SAC = {"lat": 38.5816, "lng": -121.4944}


def _turn(agent_sandbox, text, **kw):
    return run(agent_sandbox.provider().handle(AgentRequest(text=text, session_id="typed", **SAC, **kw), None))


def test_typed_noise_is_not_told_it_was_misheard(agent_sandbox):
    res = _turn(agent_sandbox, "??", input="text")
    assert res.status == "not_understood"
    assert res.reply == res.message == NOT_UNDERSTOOD_TYPED and "catch" not in res.reply
    assert agent_sandbox.llm.calls == []  # no letters: never sent to the model


def test_typed_unclear_intent_gets_the_typed_line(agent_sandbox):
    agent_sandbox.llm.on(r"blorp", {"intent": "unclear"})
    res = _turn(agent_sandbox, "blorp blorp", input="text")
    assert res.status == "not_understood" and res.reply == NOT_UNDERSTOOD_TYPED


def test_voice_and_older_apps_keep_the_hearing_line(agent_sandbox):
    agent_sandbox.llm.on(r"blorp", {"intent": "unclear"})
    assert _turn(agent_sandbox, "blorp blorp", input="voice").reply == NOT_UNDERSTOOD
    assert _turn(agent_sandbox, "??").reply == NOT_UNDERSTOOD


def test_unknown_input_kind_is_refused(api_client):
    assert api_client.post("/api/agent", json={"text": "hi", "input": "fax", **SAC}).status_code == 422


def test_typed_line_in_russian_is_about_meaning(api_client):
    r = api_client.post("/api/agent", json={"text": "??", "input": "text", "lang": "ru", **SAC})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "not_understood"
    assert body["reply"].startswith("Не понял, о чём ты.") and "расслыш" not in body["reply"]
    r = api_client.post("/api/agent", json={"text": "??", "lang": "ru", **SAC})
    assert "расслыш" in r.json()["reply"]


# ---- review of #181: a typed miss is "couldn't find", not "I heard... say it again" ----------------

def _go_narnia(agent_sandbox, **kw):
    agent_sandbox.llm.on(r"Narnia", {"intent": "go", "query": "Narnia"})
    return _turn(agent_sandbox, "take me to Narnia", **kw)


def test_typed_destination_not_found_is_not_heard():
    from services.agent.replies import NOT_FOUND_TYPED, heard_not_found
    assert heard_not_found("Narnia", typed=True) == NOT_FOUND_TYPED.format(query="Narnia") == \
        "Couldn't find Narnia. Try another name or a nearby town."
    assert heard_not_found("Narnia") == "I heard Narnia, but can't find it. Say it again or type it."


def test_typed_go_nowhere_gets_the_typed_line(agent_sandbox):
    res = _go_narnia(agent_sandbox, input="text")
    assert res.status == "chat" and res.reply == "Couldn't find Narnia. Try another name or a nearby town."
    assert "heard" not in _go_narnia(agent_sandbox, input="text").reply


def test_spoken_go_nowhere_keeps_the_heard_line(agent_sandbox):
    assert _go_narnia(agent_sandbox, input="voice").reply.startswith("I heard Narnia")
    assert _go_narnia(agent_sandbox).reply.startswith("I heard Narnia")  # an older app sends no input


def test_at_highway_speed_a_typed_miss_keeps_its_hint(agent_sandbox):
    fast = {"speed_mps": 30.0}
    assert _go_narnia(agent_sandbox, input="text", **fast).reply.endswith("Try another name or a nearby town.")
    agent_sandbox.llm.on(r"blorp", {"intent": "unclear"})
    assert _turn(agent_sandbox, "blorp blorp", input="text", **fast).reply == NOT_UNDERSTOOD_TYPED
    # voice is still cut to one sentence at speed
    assert _go_narnia(agent_sandbox, input="voice", **fast).reply == "I heard Narnia, but can't find it."


def test_typed_not_found_in_the_russian_catalog():
    from services import i18n
    i18n.catalog.cache_clear()
    misses: list[str] = []
    ru = i18n.translate("Couldn't find Narnia. Try another name or a nearby town.", "ru",
                        fallback=lambda s, c: misses.append(s) or None)
    assert ru == "Не нашёл «Narnia». Попробуй другое название или город рядом." and not misses
