"""Hours of service via the driver's Motive key: parsing, spoken lines, error mapping, the header, no leaks."""
import logging

import httpx
from conftest import fixture_json, run

from models import AgentRequest
from services import hos
from services.hos import Hos, parse, speech

DATA = fixture_json("motive/available_time")
SAC = {"lat": 38.5816, "lng": -121.4944}
KEY = "motive-secret-key-ABC123"


def test_parse_motive_shape():
    h = parse(DATA)
    assert h == Hos(drive_min=260, shift_min=540, cycle_min=2520, break_min=180, driver="Test Driver")
    assert parse({}) is None and parse({"users": []}) is None and parse({"users": [{"available_time": {}}]}) is None
    zero = Hos(drive_min=0, shift_min=0, cycle_min=0)
    assert parse({"users": [{"available_time": {"drive": "x", "shift": None}}]}) == zero
    assert parse({"users": [{"available_time": {"drive": 59}}]}).drive_min == 1  # rounds


def test_speech_lines():
    assert speech(Hos(drive_min=260, shift_min=540, cycle_min=2520)) == \
        "You've got 4 hours 20 minutes of drive time, 9 hours on the shift, and 42 hours left on the cycle."
    assert speech(Hos(drive_min=61, shift_min=60, cycle_min=45)) == \
        "You've got 1 hour 1 minutes of drive time, 1 hour on the shift, and 45 minutes left on the cycle."
    assert speech(Hos(drive_min=20, shift_min=100, cycle_min=100)) == \
        "Only 20 minutes of drive time left, let's find you a place to stop."
    assert speech(Hos(drive_min=0, shift_min=100, cycle_min=100)) == \
        "You're out of drive time. Let's find you a place to stop."


def test_available_time_errors(fake_http):
    fake_http.add("api.gomotive.com", "/v1/available_time", status=401, json={"error": "unauthorized"})
    try:
        run(hos.available_time(KEY))
        raise AssertionError("expected HosAuthError")
    except hos.HosAuthError:
        pass
    fake_http.routes.clear()
    fake_http.add("api.gomotive.com", "/v1/available_time", status=500, text="boom")
    try:
        run(hos.available_time(KEY))
        raise AssertionError("expected HosDown")
    except hos.HosDown:
        pass
    fake_http.routes.clear()
    fake_http.raise_for("api.gomotive.com", "/v1/available_time", httpx.ReadTimeout("slow"))
    try:
        run(hos.available_time(KEY))
        raise AssertionError("expected HosDown")
    except hos.HosDown:
        pass
    fake_http.routes.clear()
    fake_http.add("api.gomotive.com", "/v1/available_time", json={"users": [{"nothing": 1}]})
    try:
        run(hos.available_time(KEY))
        raise AssertionError("expected HosDown")
    except hos.HosDown:
        pass


def test_available_time_sends_the_key_as_header_only(fake_http):
    fake_http.add("api.gomotive.com", "/v1/available_time", json=DATA)
    h = run(hos.available_time(KEY))
    assert h.drive_min == 260
    req = fake_http.calls[0]
    assert req.headers["X-Api-Key"] == KEY and KEY not in str(req.url)


def test_dialog_with_and_without_the_key(agent_sandbox, fake_http, caplog):
    fake_http.add("api.gomotive.com", "/v1/available_time", json=DATA)
    agent_sandbox.llm.on(r"hours", {"intent": "hos"})
    prov = agent_sandbox.provider()
    with caplog.at_level(logging.DEBUG):
        res = run(prov.handle(AgentRequest(text="how many hours do I have", session_id="h1", motive_key=KEY, **SAC),
                              None))
        assert res.reply.startswith("You've got 4 hours 20 minutes of drive time")
        res = run(prov.handle(AgentRequest(text="how many hours do I have", session_id="h2", **SAC), None))
        assert res.reply == "Connect Motive in settings and I'll keep track of your hours."
    assert KEY not in caplog.text
    assert "motive_key" not in res.model_dump_json() and KEY not in AgentRequest(text="x", motive_key=KEY,
                                                                                 **SAC).model_dump_json()


def test_dialog_error_lines(agent_sandbox, fake_http, monkeypatch):
    agent_sandbox.llm.on(r"hours", {"intent": "hos"})
    prov = agent_sandbox.provider()
    fake_http.add("api.gomotive.com", "/v1/available_time", status=403, text="no")
    res = run(prov.handle(AgentRequest(text="hours left?", session_id="e1", motive_key=KEY, **SAC), None))
    assert res.reply == "Motive didn't accept the key. Check it in settings."
    fake_http.routes.clear()
    fake_http.raise_for("api.gomotive.com", "/v1/available_time", httpx.ConnectError("no"))
    res = run(prov.handle(AgentRequest(text="hours left?", session_id="e2", motive_key=KEY, **SAC), None))
    assert res.reply == "I can't reach Motive right now. Try again in a minute."
    monkeypatch.setenv("HOS_MOTIVE", "0")
    res = run(prov.handle(AgentRequest(text="hours left?", session_id="e3", motive_key=KEY, **SAC), None))
    assert res.reply == hos.CONNECT


def test_header_reaches_the_agent_through_the_api(api_client, agent_sandbox, fake_http, caplog):
    fake_http.add("api.gomotive.com", "/v1/available_time", json=DATA)
    agent_sandbox.llm.on(r"hours", {"intent": "hos"})
    with caplog.at_level(logging.INFO):
        r = api_client.post("/api/agent", json={"text": "how many hours do I have", **SAC},
                            headers={"X-Motive-Key": KEY})
    assert r.status_code == 200, r.text
    assert r.json()["reply"].startswith("You've got 4 hours 20 minutes")
    assert "motive_key" not in r.text and KEY not in r.text and KEY not in caplog.text
    r = api_client.post("/api/agent", json={"text": "how many hours do I have", **SAC})
    assert r.json()["reply"] == hos.CONNECT
