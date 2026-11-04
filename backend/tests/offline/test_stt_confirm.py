"""STT confidence from logprobs, and the one-time "Did you say ...?" before acting on an unsure place name."""
from types import SimpleNamespace

from conftest import fixture_json, run

from models import AgentRequest, Place
from services import agent, voice
from services.voice import Transcript, confidence_of

SAC = {"lat": 38.5816, "lng": -121.4944}
RENO = Place(id="here-reno", name="Reno, NV", category="destination", lat=39.5296, lng=-119.8138)


def test_confidence_of_logprobs():
    assert confidence_of([{"token": "Reno", "logprob": 0.0}]) == 1.0
    assert confidence_of([{"logprob": -0.5}, {"logprob": -1.5}]) == 0.368  # exp(-1)
    assert confidence_of([SimpleNamespace(logprob=-0.1), SimpleNamespace(logprob="x")]) == 0.905
    assert confidence_of([]) is None and confidence_of(None) is None and confidence_of([{"nope": 1}]) is None


def test_transcribe_requests_logprobs_and_returns_confidence(monkeypatch):
    calls = []

    class _Stt:
        async def create(self, **kw):
            calls.append(kw)
            return SimpleNamespace(text="take me to Reno", logprobs=[{"logprob": -0.2}, {"logprob": -0.4}])

    monkeypatch.setattr(voice.llm, "client", lambda: SimpleNamespace(audio=SimpleNamespace(transcriptions=_Stt())))
    tr = run(voice.transcribe_full(b"abc", "a.m4a"))
    assert tr == Transcript("take me to Reno", 0.741)
    assert calls[0]["include"] == ["logprobs"] and calls[0]["model"] == voice.STT_MODEL
    assert run(voice.transcribe(b"abc", "a.m4a")) == "take me to Reno"


def test_whisper_fallback_maps_avg_logprob(monkeypatch):
    class _Stt:
        async def create(self, **kw):
            if kw["model"] == voice.STT_MODEL:
                raise RuntimeError("primary down")
            return SimpleNamespace(segments=[{"text": "find fuel", "avg_logprob": -0.3, "no_speech_prob": 0.0,
                                              "compression_ratio": 1.0}])

    monkeypatch.setattr(voice.llm, "client", lambda: SimpleNamespace(audio=SimpleNamespace(transcriptions=_Stt())))
    tr = run(voice.transcribe_full(b"abc", "a.m4a"))
    assert tr.text == "find fuel" and tr.confidence == 0.741


def test_transcribe_endpoint_returns_confidence(api_client, monkeypatch):
    async def fake(data, name, hint=None):
        return Transcript("take me to Reno", 0.42)

    monkeypatch.setattr(voice, "transcribe_full", fake)
    r = api_client.post("/api/voice/transcribe", files={"audio": ("a.m4a", b"abc", "audio/m4a")})
    assert r.status_code == 200 and r.json()["transcript"] == "take me to Reno"
    assert r.json()["stt_confidence"] == 0.42


def _setup(agent_sandbox, fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", json=fixture_json("here/route_sac_reno"))
    fake_http.add("api.weather.gov", "/alerts/active", json={"features": []})
    fake_http.add("cwwp2.dot.ca.gov", "/data/", json={"data": []})
    agent_sandbox.llm.on(r"reno|rina", {"intent": "go", "query": "Reno, NV"})  # the model corrects the mishearing
    agent_sandbox.llm.on(r"fuel", {"intent": "find", "category": "truck_stop"})
    agent_sandbox.llm.on(r"thanks", {"intent": "chat", "reply": "Anytime."})
    agent_sandbox.geocode["reno"] = [RENO]
    return agent_sandbox.provider()


def test_unsure_destination_is_confirmed_then_acted_on(agent_sandbox, fake_http):
    prov = _setup(agent_sandbox, fake_http)
    res = run(prov.handle(AgentRequest(text="take me to Rina", session_id="c1", stt_confidence=0.3, **SAC), None))
    assert res.status == "confirm" and res.reply == "Did you say Reno, NV?"
    calls_before = len(agent_sandbox.llm.calls)
    res = run(prov.handle(AgentRequest(text="yeah", session_id="c1", stt_confidence=0.9, **SAC), None))
    assert res.status == "route_set" and res.place.id == RENO.id
    assert len(agent_sandbox.llm.calls) == calls_before  # the pending classification is reused, no new LLM call
    assert "pending" not in agent._SESSIONS["c1"]


def test_no_clears_and_asks_again_and_only_once_per_dialog(agent_sandbox, fake_http):
    prov = _setup(agent_sandbox, fake_http)
    res = run(prov.handle(AgentRequest(text="take me to Rina", session_id="c2", stt_confidence=0.3, **SAC), None))
    assert res.status == "confirm"
    res = run(prov.handle(AgentRequest(text="no", session_id="c2", stt_confidence=0.9, **SAC), None))
    assert res.status == "chat" and res.reply == "Okay, say it again."
    res = run(prov.handle(AgentRequest(text="take me to Rina", session_id="c2", stt_confidence=0.3, **SAC), None))
    assert res.status == "route_set"  # second time in the same dialog: act, don't nag


def test_other_text_after_the_question_is_a_fresh_turn(agent_sandbox, fake_http):
    prov = _setup(agent_sandbox, fake_http)
    run(prov.handle(AgentRequest(text="take me to Rina", session_id="c3", stt_confidence=0.3, **SAC), None))
    res = run(prov.handle(AgentRequest(text="thanks", session_id="c3", stt_confidence=0.9, **SAC), None))
    assert res.status == "chat" and res.reply == "Anytime." and "pending" not in agent._SESSIONS["c3"]


def test_no_question_when_confident_or_no_name_or_flag_off(agent_sandbox, fake_http, monkeypatch):
    prov = _setup(agent_sandbox, fake_http)
    assert run(prov.handle(AgentRequest(text="take me to Reno", session_id="c4", stt_confidence=0.8, **SAC),
                           None)).status == "route_set"
    assert run(prov.handle(AgentRequest(text="take me to Reno", session_id="c5", **SAC), None)).status == "route_set"
    pilot = Place(id="g1", name="Pilot", category="truck_stop", lat=38.58, lng=-121.55, distance_mi=3.0)
    agent_sandbox.places_provider.places = [pilot]
    assert run(prov.handle(AgentRequest(text="I need fuel", session_id="c6", stt_confidence=0.2, **SAC),
                           None)).status == "route_set"  # a category search has no name to confirm
    monkeypatch.setenv("STT_CONFIRM", "0")
    assert run(prov.handle(AgentRequest(text="take me to Rina", session_id="c7", stt_confidence=0.2, **SAC),
                           None)).status == "route_set"
    monkeypatch.setenv("STT_CONFIRM", "1")
    monkeypatch.setenv("STT_CONFIRM_BELOW", "0.95")
    assert run(prov.handle(AgentRequest(text="take me to Rina", session_id="c8", stt_confidence=0.9, **SAC),
                           None)).status == "confirm"


FRESNO = Place(id="here-fresno", name="Fresno, CA", category="destination", lat=36.7378, lng=-119.7871)
PILOT = Place(id="g-pilot", name="Pilot Travel Center", category="truck_stop", lat=38.59, lng=-121.50, distance_mi=1.0,
              address="1 A St, West Sacramento, CA")


def test_no_with_a_new_request_is_that_request(agent_sandbox, fake_http):
    """Review 2026-11-02, bug 17: "No, take me to Fresno" after "Did you say Reno?" got "Okay, say it again";
    the correction never reached the classifier."""
    prov = _setup(agent_sandbox, fake_http)
    agent_sandbox.llm.on(r"fresno", {"intent": "go", "query": "Fresno, CA"})
    agent_sandbox.geocode["fresno"] = [FRESNO]
    run(prov.handle(AgentRequest(text="take me to Rina", session_id="c9", stt_confidence=0.3, **SAC), None))
    res = run(prov.handle(AgentRequest(text="No, take me to Fresno", session_id="c9", stt_confidence=0.9, **SAC), None))
    assert res.status == "route_set" and res.place.id == FRESNO.id
    assert agent_sandbox.llm.calls[-1][1].endswith("Driver said: No, take me to Fresno")


def test_yes_with_a_new_request_is_not_a_yes(agent_sandbox, fake_http):
    """Review 2026-11-02, bug 17: "Right, actually find me a truck stop" was a YES and drove to Reno."""
    prov = _setup(agent_sandbox, fake_http)
    agent_sandbox.llm.on(r"truck stop", {"intent": "find", "category": "truck_stop"})
    agent_sandbox.places_provider.places = [PILOT]
    run(prov.handle(AgentRequest(text="take me to Rina", session_id="c10", stt_confidence=0.3, **SAC), None))
    res = run(prov.handle(AgentRequest(text="Right, actually find me a truck stop", session_id="c10", **SAC), None))
    assert res.status == "route_set" and res.place.id == PILOT.id


def test_bare_answers_with_filler_stay_answers():
    for text in ("yes", "yeah, that's it", "Yes please.", "yep, take me there", "right, that's the one", "sure, go ahead",
                 "yeah go ahead and add it"):
        assert agent.bare_answer(text, agent.YES) or agent.bare_answer(text, agent.DEST_YES), text
    for text in ("no", "No.", "nope, not that one", "no thanks", "nah, wrong one"):
        assert agent.bare_answer(text, agent.NO), text
    for text in ("No, take me to Fresno", "Right, actually find me a truck stop", "yes and find me a hotel there",
                 "no, I said Reno"):
        assert not agent.bare_answer(text, agent.YES) and not agent.bare_answer(text, agent.NO), text


def test_noise_only_rules(monkeypatch):
    monkeypatch.delenv("STT_MAYBE_MIN_CONFIDENCE", raising=False)
    assert voice.noise_only("Thank you.", 0.95)  # the classic made-up line
    assert voice.noise_only("", None)
    assert voice.noise_only("Reno", 0.3) and not voice.noise_only("go", None)  # no logprobs: not judged on it
    assert not voice.noise_only("yes", 0.9) and not voice.noise_only("Go.", 0.7)
    assert not voice.noise_only("find me a truck stop", 0.2)  # three words and more are speech
    monkeypatch.setenv("STT_MAYBE_MIN_CONFIDENCE", "0.95")
    assert voice.noise_only("yes", 0.9)


def _post(api_client, monkeypatch, text, conf, maybe):
    async def fake(data, name, hint=None):
        return Transcript(text, conf)

    monkeypatch.setattr(voice, "transcribe_full", fake)
    data = {"maybe": "1"} if maybe else {}
    r = api_client.post("/api/voice/transcribe", files={"audio": ("a.m4a", b"abc", "audio/m4a")}, data=data)
    assert r.status_code == 200
    return r.json()["transcript"]


def test_transcribe_maybe_drops_noise_keeps_words(api_client, monkeypatch):
    assert _post(api_client, monkeypatch, "Thank you.", 0.9, maybe=True) == ""
    assert _post(api_client, monkeypatch, "uh", 0.2, maybe=True) == ""
    assert _post(api_client, monkeypatch, "Yes.", 0.88, maybe=True) == "Yes."
    assert _post(api_client, monkeypatch, "take me to Reno", 0.3, maybe=True) == "take me to Reno"
    # without the flag nothing changes (a clear recording that says "thank you" is the driver)
    assert _post(api_client, monkeypatch, "Thank you.", 0.9, maybe=False) == "Thank you."
