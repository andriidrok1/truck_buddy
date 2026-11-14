"""Daily spend metering and the money cap (services/spend.py): prices, the store, the stages, provider caps, alerts,
and the first-day turn budget of a new device (services/quota.py + tenancy.device_created)."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from conftest import run

from models import AgentRequest, Place
from services import db, http, llm, maps_grounding, memory, quota, spend, tenancy, voice
from services.auth import Principal

SAC = {"lat": 38.5816, "lng": -121.4944}


@pytest.fixture(autouse=True)
def _fresh():
    quota.reset()
    spend.reset()
    tenancy.reset_caches()
    yield
    quota.reset()
    spend.reset()
    tenancy.reset_caches()


def _metered(fake_http):
    """The shared client with the production request hook, answering from fake_http."""
    http._client = httpx.AsyncClient(transport=httpx.MockTransport(fake_http), event_hooks=http.HOOKS)


async def _settle():
    while spend.pending_tasks():
        await asyncio.gather(*spend.pending_tasks(), return_exceptions=True)


# ---- prices ----------------------------------------------------------------------------------------------------

def test_chat_price_from_usage_tokens():
    inp, cached_in, out = spend.CHAT_PER_M["gpt-5.4-mini"]
    usage = {"prompt_tokens": 2000, "completion_tokens": 40, "cached_tokens": 1500}
    assert spend.price_chat("gpt-5.4-mini", usage) == pytest.approx((500 * inp + 1500 * cached_in + 40 * out) / 1e6)
    # a model missing from the table is priced as the dearest one, never as free
    assert spend.price_chat("gpt-9", usage) == spend.price_chat(spend.CHAT_UNKNOWN, usage) > 0
    assert spend.price_chat("gpt-5.4-mini", {}) == 0


def test_stt_price_from_tokens_seconds_or_size():
    m = "gpt-4o-mini-transcribe"
    tokens = SimpleNamespace(usage=SimpleNamespace(type="tokens", input_tokens=1000, output_tokens=20))
    a_in, t_out = spend.STT_TOKENS_PER_M[m]
    assert spend.price_stt(m, tokens) == pytest.approx((1000 * a_in + 20 * t_out) / 1e6)
    assert spend.price_stt(m, {"usage": {"type": "duration", "seconds": 30}}) == pytest.approx(0.5 * spend.STT_PER_MIN[m])
    assert spend.price_stt("whisper-1", SimpleNamespace(duration=60.0, usage=None)) == pytest.approx(spend.STT_PER_MIN["whisper-1"])
    # nothing in the response: the upload size at STT_BYTES_PER_S
    assert spend.price_stt(m, SimpleNamespace(text="hi"), nbytes=spend.STT_BYTES_PER_S * 120) == pytest.approx(2 * spend.STT_PER_MIN[m])


def test_tts_price_per_character():
    assert spend.price_tts("tts-1", 1_000_000) == pytest.approx(spend.TTS_PER_M_CHARS["tts-1"])
    assert spend.price_tts("gpt-4o-mini-tts", 100) == pytest.approx(100 * spend.TTS_PER_M_CHARS["gpt-4o-mini-tts"] / 1e6)
    assert spend.price_tts("new-voice", 10) == pytest.approx(10 * max(spend.TTS_PER_M_CHARS.values()) / 1e6)


def test_openai_calls_record_what_they_cost(monkeypatch):
    usage = SimpleNamespace(prompt_tokens=1200, completion_tokens=80, prompt_tokens_details=SimpleNamespace(cached_tokens=0))
    reply = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))], usage=usage)

    async def chat_create(**kw):
        return reply

    async def stt_create(**kw):
        return SimpleNamespace(text="take me to Reno", usage={"type": "duration", "seconds": 6})

    async def tts_create(**kw):
        return SimpleNamespace(content=b"mp3")

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=chat_create)),
                           audio=SimpleNamespace(transcriptions=SimpleNamespace(create=stt_create),
                                                 speech=SimpleNamespace(create=tts_create)))
    monkeypatch.setattr(llm, "_client", fake)

    async def same(mp3):
        return mp3

    async def no_store(audio_id, data):
        return None

    monkeypatch.setattr(voice, "louder", same)
    monkeypatch.setattr(voice, "store_clip", no_store)

    async def go():
        await llm.complete_full("sys", "hi", "gpt-5.4-mini")
        after_chat = spend.today_usd()
        await voice.transcribe_full(b"x" * 100, "a.m4a")
        after_stt = spend.today_usd()
        text = "Turn left in half a mile."
        await voice.synthesize(text)
        return after_chat, after_stt, spend.today_usd(), len(voice.spoken(text))

    chat, stt, total, chars = run(go())
    assert chat == pytest.approx(spend.price_chat("gpt-5.4-mini", {"prompt_tokens": 1200, "completion_tokens": 80}))
    assert stt - chat == pytest.approx(0.1 * spend.STT_PER_MIN["gpt-4o-mini-transcribe"])
    assert total - stt == pytest.approx(spend.price_tts(voice.fallback_model(), chars))


def test_tester_calls_are_not_counted():
    async def as_tester():
        spend.exempt_role("tester")
        spend.record("openai", 1.0)
        return spend.allow_call("here")

    async def as_driver():
        spend.exempt_role("driver")
        spend.record("openai", 0.25)

    assert run(as_tester()) is True and spend.today_usd() == 0
    run(as_driver())
    assert spend.today_usd() == pytest.approx(0.25)


# ---- the store -------------------------------------------------------------------------------------------------

def test_spend_survives_a_restart_and_is_shared_by_workers(sqlite_store, monkeypatch):
    monkeypatch.setenv("DAILY_USD_CAP", "100")

    async def first_life():
        spend.record("openai", 0.40)
        spend.record("here", 0.002, calls=2)
        assert spend.today_usd() == pytest.approx(0.402)  # counted before the store has it
        await spend.flush()

    run(first_life())
    spend.reset()  # the worker restarts: memory is gone
    run(memory.close())
    assert spend.today_usd() == 0
    run(spend.refresh())
    assert spend.today_usd() == pytest.approx(0.402) and spend.calls_today("here") == 2

    # another worker's spend shows up at the next read; this worker's unwritten spend is added on top
    run(db._sq_run(lambda con: con.execute(
        "UPDATE spend_daily SET value = value + 1.0 WHERE day = ? AND key = 'usd:openai'", (quota.day(),))))

    async def later():
        spend.record("openai", 0.05)
        assert spend.today_usd() == pytest.approx(0.452)
        await spend.flush()

    run(later())
    assert spend.today_usd() == pytest.approx(1.452)
    run(spend.refresh())
    assert spend.today_usd() == pytest.approx(1.452)  # read twice, counted once


def test_without_a_store_spend_lives_in_memory():
    spend.record("openai", 0.3)
    run(spend.flush())  # nothing to write to: no error, nothing lost
    assert db.store() == "off" and spend.today_usd() == pytest.approx(0.3)


# ---- stages ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("usd, stage", [(0.0, "normal"), (0.79, "normal"), (0.8, "alert"), (1.0, "cheap_voice"),
                                        (1.09, "cheap_voice"), (1.1, "no_satellite"), (1.2, "no_ai"), (9.0, "no_ai")])
def test_stages_by_share_of_the_cap(monkeypatch, usd, stage):
    monkeypatch.setenv("DAILY_USD_CAP", "1")
    spend._pending[(quota.day(), "usd:openai")] = usd
    assert spend.stage() == stage


def test_zero_cap_is_off(monkeypatch):
    monkeypatch.setenv("DAILY_USD_CAP", "0")
    spend._pending[(quota.day(), "usd:openai")] = 1000.0
    assert spend.stage() == "normal" and spend.turns_open() and spend.satellite_open() and not spend.voice_cheap()


def _spent(monkeypatch, frac: float) -> None:
    monkeypatch.setenv("DAILY_USD_CAP", "1")
    spend._pending[(quota.day(), "usd:openai")] = frac


def test_cheap_voice_skips_elevenlabs_and_new_speak_lines(monkeypatch, api_client):
    monkeypatch.setenv("ELEVENLABS_ENABLED", "1")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-eleven")
    assert voice.eleven_voice()
    _spent(monkeypatch, 1.0)
    assert voice.eleven_voice() is None and voice.provider() == "openai"

    async def must_not_make(*a, **k):
        raise AssertionError("no new clip past the cap")

    monkeypatch.setattr(voice, "synthesize", must_not_make)
    r = api_client.post("/api/speak", json={"text": "Love's in five miles, on the right."})
    assert r.status_code == 503  # the app says it with the phone's own voice


def test_no_satellite_stops_new_checks_and_grounding(monkeypatch, fake_http):
    _spent(monkeypatch, 1.1)
    assert quota.take_satellite() is False
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    place = Place(id="p", name="Pilot", category="truck_stop", lat=38.86, lng=-121.95)
    assert run(maps_grounding.drivers_say(place)) is None and fake_http.calls == []
    _spent(monkeypatch, 1.09)
    assert quota.take_satellite() is True


def test_no_ai_refuses_turns_with_a_spoken_line(monkeypatch, api_client, agent_sandbox):
    _spent(monkeypatch, 1.2)  # agent_sandbox's LLM has no canned reply: a model call would fail the test
    r = api_client.post("/api/agent", json={"text": "find me a truck stop", **SAC})
    body = r.json()
    assert r.status_code == 200 and body["reply"] == spend.AI_PAUSED and body["source"] == "spend"
    assert body["reply"] == ("I'm taking a break from answering for the rest of today; I'm back at midnight Pacific. "
                             "The map and the routes keep working.")
    assert agent_sandbox.llm.calls == []
    health = api_client.get("/api/health").json()
    assert health["ok"] and health["spend_stage"] == "no_ai" and health["spend_today_usd"] == 1.2
    assert spend.turns_open("tester")


def test_refused_turn_line_is_made_once(monkeypatch, api_client, agent_sandbox):
    _spent(monkeypatch, 1.5)
    made, pins = [], {}

    async def synth(text, *a, **k):
        made.append(text)
        return "tmp-id"

    monkeypatch.setattr(voice, "synthesize", synth)
    monkeypatch.setattr(voice, "pin", lambda aid, key: pins.setdefault(key, "pinned-id"))
    monkeypatch.setattr(voice, "pinned", lambda key: pins.get(key))
    for _ in range(3):
        r = api_client.post("/api/agent?speak=true", json={"text": "hello", **SAC})
        assert r.json()["audio_url"] == "/api/voice/audio/pinned-id"
    assert made == [spend.AI_PAUSED]


# ---- provider call caps ----------------------------------------------------------------------------------------

def test_here_cap_refuses_the_call_before_it_leaves(monkeypatch, fake_http):
    monkeypatch.setenv("HERE_CALLS_PER_DAY", "2")
    _metered(fake_http)
    fake_http.add("geocode.search.hereapi.com", "/v1/geocode", json={"items": []})
    fake_http.add("api.openrouteservice.org", "/", json={})

    async def go():
        c = http.client()
        for _ in range(2):
            assert (await c.get("https://geocode.search.hereapi.com/v1/geocode", params={"q": "x"})).status_code == 200
        with pytest.raises(spend.ProviderCapped):
            await c.get("https://geocode.search.hereapi.com/v1/geocode", params={"q": "x"})
        await c.get("https://api.openrouteservice.org/v2/x")  # other hosts are not counted or capped
        await _settle()

    run(go())
    assert len(fake_http.calls_to("geocode.search.hereapi.com")) == 2
    assert spend.calls_today("here") == 2 and spend.today_usd() == pytest.approx(2 * spend.PER_CALL["here"])


def test_gemini_cap(monkeypatch, fake_http):
    monkeypatch.setenv("GEMINI_CALLS_PER_DAY", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    _metered(fake_http)
    fake_http.add("generativelanguage.googleapis.com", "/", json={"steps": []})
    place = Place(id="p", name="Pilot", category="truck_stop", lat=38.86, lng=-121.95)
    run(maps_grounding.drivers_say(place))
    run(maps_grounding.drivers_say(place))
    assert len(fake_http.calls_to("generativelanguage.googleapis.com")) == 1
    assert spend.calls_today("gemini") == 1 and spend.today_usd() == pytest.approx(spend.PER_CALL["gemini"])


def test_zero_call_cap_is_off(monkeypatch):
    monkeypatch.setenv("HERE_CALLS_PER_DAY", "0")
    spend._pending[(quota.day(), "calls:here")] = 1e6
    assert spend.allow_call("here")


# ---- alerts ----------------------------------------------------------------------------------------------------

def test_alerts_once_per_threshold_per_day_across_workers(sqlite_store, monkeypatch, fake_http, caplog):
    monkeypatch.setenv("DAILY_USD_CAP", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    fake_http.add("api.telegram.org", "/bot123:test-token/sendMessage", json={"ok": True})

    async def worker(amounts):
        for usd in amounts:
            spend.record("openai", usd)
            await _settle()
        await spend.flush()
        await _settle()

    run(worker([0.5, 0.35, 0.05]))  # 80% crossed once, then more spend under 100%
    sent = [c.read().decode() for c in fake_http.calls_to("api.telegram.org")]
    assert len(sent) == 1 and "80% of the $1 daily cap" in sent[0]
    spend.reset()  # a second worker (or this one restarted) sees the same day
    run(memory.close())
    run(worker([0.01]))
    assert len(fake_http.calls_to("api.telegram.org")) == 1
    run(worker([0.15]))  # past 100% (not 110%): one more alert, for that threshold
    texts = [c.read().decode() for c in fake_http.calls_to("api.telegram.org")]
    assert len(texts) == 2 and "100% of the $1 daily cap; now: cheap_voice" in texts[1]
    assert "spend alert" in caplog.text and "test-token" not in caplog.text


def test_alert_without_telegram_is_a_log_line(monkeypatch, fake_http, caplog):
    monkeypatch.setenv("DAILY_USD_CAP", "1")

    async def go():
        spend.record("openai", 0.9)
        spend.record("openai", 0.01)
        await _settle()

    run(go())
    assert fake_http.calls == [] and caplog.text.count("spend alert") == 1


def test_provider_cap_alerts_once(monkeypatch, caplog):
    monkeypatch.setenv("HERE_CALLS_PER_DAY", "1")
    spend._pending[(quota.day(), "calls:here")] = 1

    async def go():
        assert not spend.allow_call("here") and not spend.allow_call("here")
        await _settle()

    run(go())
    assert caplog.text.count("here hit its daily call cap") == 1


# ---- new device ------------------------------------------------------------------------------------------------

def test_turn_budget_on_a_first_day(monkeypatch):
    monkeypatch.setenv("TURNS_PER_DAY", "150")
    monkeypatch.setenv("NEW_DEVICE_TURNS", "30")
    assert quota.turn_budget() == 150 and quota.turn_budget(new_device=True) == 30
    monkeypatch.setenv("TURNS_PER_DAY", "0")
    assert quota.turn_budget(new_device=True) == 30 and quota.turn_budget() == 0
    monkeypatch.setenv("NEW_DEVICE_TURNS", "0")
    monkeypatch.setenv("TURNS_PER_DAY", "150")
    assert quota.turn_budget(new_device=True) == 150


def test_new_device_gets_the_first_day_budget(sqlite_store, monkeypatch, agent_sandbox):
    import server
    monkeypatch.setenv("TURNS_PER_DAY", "150")
    monkeypatch.setenv("NEW_DEVICE_TURNS", "2")
    agent_sandbox.llm.default = {"intent": "chat", "reply": "Sure thing."}
    dev = run(tenancy.ensure_solo_org("drv_newphone"))
    who = Principal("drv_newphone", "driver", dev.driver_id, dev.org_id)

    def turn():
        return run(server._run_agent(AgentRequest(text="hi", **SAC), speak=False, driver=who)).reply

    assert run(tenancy.device_created("drv_newphone")) is not None
    assert [turn() for _ in range(3)] == ["Sure thing.", "Sure thing.", quota.OUT_OF_TURNS]

    # the same phone a day later: the normal budget (the turns it used still count today)
    old = db.iso(db.utcnow() - timedelta(hours=25))
    run(db._sq_run(lambda con: con.execute("UPDATE drivers SET created_at = ? WHERE id = ?", (old, dev.driver_id))))
    tenancy.reset_caches()
    assert turn() == "Sure thing."
    tester = Principal("drv_tester", "tester")
    monkeypatch.setenv("TURNS_PER_DAY", "1")
    assert all(run(server._run_agent(AgentRequest(text="hi", **SAC), speak=False, driver=tester)).reply == "Sure thing."
               for _ in range(3))


def test_unknown_device_age_is_not_new():
    assert run(tenancy.device_created("drv_x")) is None  # store off
    assert run(tenancy.device_created(None)) is None


# ---- canary ----------------------------------------------------------------------------------------------------

def test_canary_prints_spend():
    from scripts.route_canary import spend_line
    line = spend_line({"spend_today_usd": 1.234, "spend_cap_usd": 5.0, "spend_stage": "normal",
                       "calls_today": {"here": 12, "gemini": 0}})
    assert line == "spend today $1.23 of $5, stage normal, calls here=12 gemini=0"
    assert spend_line({"ok": True}) == "spend: not reported by this backend"


def test_tester_request_is_exempt_end_to_end(monkeypatch, api_client, agent_sandbox):
    """The role is set in auth.require_driver; the endpoint and what it calls see it (same request context)."""
    import server
    _spent(monkeypatch, 1.5)  # no_ai: drivers are refused, testers are not
    monkeypatch.setenv("TESTER_CODE", "TEST-SPEND")
    tok = api_client.post("/api/auth/access-code", json={"code": "TEST-SPEND"}).json()["access_token"]
    agent_sandbox.llm.default = {"intent": "chat", "reply": "Sure thing."}
    real = server.get_agent_provider()

    class Costly:
        source = real.source

        async def handle(self, req, driver=None):
            spend.record("openai", 0.5)  # what a model call inside the turn would record
            return await real.handle(req, driver)

    monkeypatch.setattr(server, "get_agent_provider", lambda: Costly())
    r = api_client.post("/api/agent", json={"text": "hi", **SAC}, headers={"Authorization": f"Bearer {tok}"})
    assert r.json()["reply"] == "Sure thing." and spend.today_usd() == pytest.approx(1.5)


def test_google_places_calls_are_metered(monkeypatch, fake_http):
    from services import http
    monkeypatch.setenv("DAILY_USD_CAP", "100")
    fake_http.add("places.googleapis.com", "/", json={"places": []})
    http._client = httpx.AsyncClient(transport=httpx.MockTransport(fake_http), event_hooks=http.HOOKS)

    async def go():
        await http.client().post("https://places.googleapis.com/v1/places:searchText", json={})

    run(go())
    assert spend.calls_today("google_places") == 1 and spend.today_usd() == pytest.approx(spend.PER_CALL["google_places"])
