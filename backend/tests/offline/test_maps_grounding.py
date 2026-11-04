"""What drivers say about a place via Gemini Grounding with Google Maps (services.maps_grounding)."""
import json

from conftest import run

from models import AgentRequest, Place
from services import maps_grounding

SAC = {"lat": 38.5816, "lng": -121.4944}
PILOT = Place(id="atp-pilot_flying_j-1", name="Pilot Travel Center", category="truck_stop", lat=38.86, lng=-121.95,
              address="30035 County Road 8, Dunnigan, CA 95937")
TEXT = "Drivers say the lot fills up by 6 pm, but the showers are clean and the staff is friendly."
ANSWER = {"steps": [
    {"type": "thought", "content": [{"type": "text", "text": "thinking"}]},
    {"type": "model_output", "content": [{"type": "text", "text": TEXT, "annotations": [
        {"type": "place_citation", "name": "Pilot Travel Center", "url": "https://maps.google.com/?cid=1"},
        {"type": "place_citation", "name": "Pilot Travel Center", "url": "https://maps.google.com/?cid=1"},
        {"type": "url_citation", "url": "https://example.com"}]}]},
]}


def test_parse_takes_the_model_output_and_place_citations_once():
    g = maps_grounding.parse(ANSWER)
    assert g.text == TEXT
    assert g.sources == [{"name": "Pilot Travel Center", "url": "https://maps.google.com/?cid=1"}]
    assert maps_grounding.parse({"steps": []}) is None and maps_grounding.parse({}) is None


def test_off_without_a_key(monkeypatch, fake_http):
    assert run(maps_grounding.drivers_say(PILOT)) is None
    assert fake_http.calls == []


def test_request_and_answer(monkeypatch, fake_http):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    fake_http.add("generativelanguage.googleapis.com", "/v1beta/interactions", json=ANSWER)
    g = run(maps_grounding.drivers_say(PILOT))
    assert g.text == TEXT and len(g.sources) == 1
    call = fake_http.calls[0]
    body = json.loads(call.content)
    assert call.headers["x-goog-api-key"] == "test-gemini" and body["model"] == maps_grounding.DEFAULT_MODEL
    assert body["tools"] == [{"type": "google_maps", "latitude": 38.86, "longitude": -121.95}]
    assert "Pilot Travel Center, 30035 County Road 8" in body["input"]


def test_no_maps_source_or_a_failure_is_no_answer(monkeypatch, fake_http):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    ungrounded = {"steps": [{"type": "model_output", "content": [{"type": "text", "text": "I think it is fine."}]}]}
    fake_http.add("generativelanguage.googleapis.com", "/v1beta/interactions", json=ungrounded, times=1)
    assert run(maps_grounding.drivers_say(PILOT)) is None  # the model's own words, not a grounded result
    fake_http.add("generativelanguage.googleapis.com", "/v1beta/interactions", status=503, json={})
    assert run(maps_grounding.drivers_say(PILOT)) is None


def test_reviews_intent_speaks_the_grounded_text_and_returns_its_links(agent_sandbox, monkeypatch, fake_http):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    fake_http.add("generativelanguage.googleapis.com", "/v1beta/interactions", json=ANSWER)
    agent_sandbox.llm.on(r"drivers say", {"intent": "reviews", "place_index": None})
    prov = agent_sandbox.provider()
    res = run(prov.handle(AgentRequest(text="what do drivers say about it?", session_id="mg", active_place=PILOT,
                                       **SAC), None))
    assert res.status == "chat" and res.reply == TEXT  # exactly as returned: nothing added, nothing cut
    assert [s.model_dump() for s in res.maps_sources] == [{"name": "Pilot Travel Center",
                                                          "url": "https://maps.google.com/?cid=1"}]
    # Gemini down: the old answer (no rating on our own places -> "no reviews yet")
    fake_http.routes.clear()
    fake_http.add("generativelanguage.googleapis.com", "/v1beta/interactions", status=500, json={})
    res = run(prov.handle(AgentRequest(text="what do drivers say about it?", session_id="mg2", active_place=PILOT,
                                       **SAC), None))
    assert res.reply == "No driver reviews there yet." and res.maps_sources == []
