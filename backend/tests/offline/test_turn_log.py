"""The per-turn log line, token counters in timings, and scripts/turn_stats.py."""
import logging
import re
import sys
from pathlib import Path

from conftest import fixture_json, run

from models import AgentRequest, AgentResponse, Place
from services import agent, timing

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import turn_stats  # noqa: E402

SAC = {"lat": 38.5816, "lng": -121.4944}
RENO = Place(id="here-reno", name="Reno, NV", category="destination", lat=39.5296, lng=-119.8138)
LOG = Path(__file__).resolve().parents[1] / "fixtures" / "logs" / "turns.log"


def test_counters_are_reported_but_not_summed():
    import server
    timing.begin()
    timing.add("llm_round_1", 1200)
    timing.add("tts", 500)
    timing.add("n_prompt_tokens", 2000)
    timing.add("n_cached_tokens", 1500)
    timing.note("intent", "find")
    res = server._finish(AgentResponse(status="chat", transcript="x", reply="y", source="t"), {"stt": 800})
    assert res.timings["total"] == 2500 and res.timings["first_audio"] == 2500
    assert res.timings["n_prompt_tokens"] == 2000 and res.timings["n_cached_tokens"] == 1500
    assert res.intent == "find"


def test_turn_line_through_the_api(api_client, agent_sandbox, fake_http, caplog):
    fake_http.add("router.hereapi.com", "/v8/routes", json=fixture_json("here/route_sac_reno"))
    fake_http.add("api.weather.gov", "/alerts/active", json={"features": []})
    agent_sandbox.llm.on(r"take me to reno", {"intent": "go", "query": "Reno, NV"})
    agent_sandbox.llm.usage = {"prompt_tokens": 2100, "completion_tokens": 30, "cached_tokens": 1536}
    agent_sandbox.geocode["reno"] = [RENO]
    with caplog.at_level(logging.INFO, logger="truckbuddy"):
        r = api_client.post("/api/agent", json={"text": "take me to Reno", "stt_ms": 820, **SAC})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["intent"] == "go" and body["timings"]["stt"] == 820
    assert body["timings"]["n_cached_tokens"] == 1536 and body["timings"]["n_prompt_tokens"] == 2100
    assert body["timings"]["first_audio"] >= 820
    line = next(rec.getMessage() for rec in caplog.records if rec.getMessage().startswith("turn "))
    assert "status=route_set" in line and "intent=go" in line and "stt=820" in line and "n_cached_tokens=1536" in line
    parsed = turn_stats.parse_line(line)
    assert parsed["status"] == "route_set" and parsed["intent"] == "go" and parsed["n_cached_tokens"] == 1536


def test_llm_timeout_is_noted(agent_sandbox, monkeypatch):
    import asyncio

    async def slow(*a, **k):
        await asyncio.sleep(1)
        return {}

    monkeypatch.setattr(agent, "CLASSIFY_TIMEOUT_S", 0.01)
    monkeypatch.setattr(agent, "classify", slow)
    timing.begin()
    res = run(agent_sandbox.provider().handle(AgentRequest(text="find fuel", session_id="t", **SAC), None))
    assert res.status == "error" and res.reply == agent.LLM_DOWN
    assert timing.notes() == {"error": "llm_timeout"}


def test_turn_stats_on_the_fixture_log(capsys):
    turns = turn_stats.parse(LOG.read_text().splitlines())
    assert len(turns) == 7  # the old "voice timings" line is ignored
    s = turn_stats.summarize(turns)
    assert s["stages"]["llm_round_1"]["p50"] == 1400 and s["stages"]["llm_round_1"]["p95"] == 9000
    assert s["status"]["route_set"] == 4 and s["errors"] == {"llm_timeout": 1}
    assert s["intent"]["go"] == 2
    assert abs(s["cached_ratio"] - (1500 * 5) / (2100 + 2000 + 2300 + 2300 + 1900 + 2200)) < 1e-6
    # re-asks: the second "go" 7 s after the first, and the not_understood after the timeout = 2 of 6 pairs
    assert abs(s["reask_rate"] - 2 / 6) < 1e-6
    out = turn_stats.render(s)
    assert "re-ask rate: 33.3%" in out and "llm_round_1" in out and "status: route_set=4" in out
    assert turn_stats.main([str(turn_stats.__file__), str(LOG)]) == 0
    assert "turns: 7" in capsys.readouterr().out


def test_turn_stats_edge_cases():
    assert turn_stats.parse_line("INFO - nothing here") is None
    t = turn_stats.parse_line("turn status=chat intent=chat total=10")
    assert t == {"status": "chat", "intent": "chat", "total": 10}
    assert turn_stats.pct([], 0.5) is None and turn_stats.pct([5], 0.95) == 5
    assert turn_stats.reask_rate([t]) is None
    assert re.match(r"turns: 1", turn_stats.render(turn_stats.summarize([t])))
