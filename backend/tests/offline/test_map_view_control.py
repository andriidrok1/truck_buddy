"""The map's look by voice (Figma "Weather v2"): "show the weather" switches the map to the radar view."""
import pytest

from conftest import run
from models import AgentRequest
from services.agent.phrases import control_phrase, view_phrase
from services.controls import apply_actions


def _req(text):
    return AgentRequest(text=text, session_id="mv", lat=38.58, lng=-121.49)


@pytest.mark.parametrize("text,view", [
    ("show the weather", "weather"),
    ("show me the radar", "weather"),
    ("weather map", "weather"),
    ("turn on the radar", "weather"),
    ("покажи погоду", "weather"),
    ("satellite view", "satellite"),
    ("switch to satellite", "satellite"),
    ("show the weather please", "weather"),
    ("back to the normal map", "map"),
    ("спутник", "satellite"),
    ("normal map", "map"),
    ("hide the radar", "map"),
    ("обычная карта", "map"),
])
def test_phrases(text, view):
    assert view_phrase(text) == {"type": "map_view", "value": view}


@pytest.mark.parametrize("text", [
    "what's the weather", "weather in Reno", "show me the weather in Reno", "show me the weather in reno", "is it snowing",
    "show me the weather tomorrow", "show the weather for tomorrow", "turn on the weather alerts",
    "Satellite Beach", "take me to Satellite Beach", "satellite parking check", "No satellite check yet.",
    "Checking the satellite.", "покажи погоду в Рино",
])
def test_forecast_questions_and_places_are_not_map_controls(text):
    assert view_phrase(text) is None
    assert control_phrase(text) is None  # the model's reply never carries a map switch


def test_a_chat_reply_mentioning_satellite_is_said_not_obeyed(agent_sandbox):
    agent_sandbox.llm.on(r"how does the parking check work", {"intent": "chat", "reply": "Checking the satellite."})
    res = run(agent_sandbox.provider().handle(_req("how does the parking check work"), None))
    assert res.actions == [] and res.reply.startswith("Checking the satellite")


def test_a_place_called_satellite_is_still_a_place():
    assert view_phrase("Satellite Beach, FL") is None


def test_apply_validates_the_value():
    acts, say, end = apply_actions([{"type": "map_view", "value": "weather"}], _req("x"))
    assert [(a.type, a.value) for a in acts] == [("map_view", "weather")]
    assert say == "Here's the weather radar." and not end
    acts, say, _ = apply_actions([{"type": "map_view", "value": "hybrid"}], _req("x"))
    assert acts == [] and say == ""


def test_weather_intent_with_a_map_phrase_becomes_the_control(agent_sandbox):
    agent_sandbox.llm.on(r"show the weather", {"intent": "weather", "near": None})
    res = run(agent_sandbox.provider().handle(_req("show the weather"), None))
    assert [(a.type, a.value) for a in res.actions] == [("map_view", "weather")]
    assert res.reply == "Here's the weather radar."


def test_model_control_action(agent_sandbox):
    agent_sandbox.llm.on(r"put the satellite on", {"intent": "control", "actions": [{"type": "map_view", "value": "satellite"}]})
    res = run(agent_sandbox.provider().handle(_req("put the satellite on please"), None))
    assert [(a.type, a.value) for a in res.actions] == [("map_view", "satellite")]


def test_empty_trailer_control():
    acts, say, _ = apply_actions([{"type": "set_trailer_empty", "value": True}], _req("I'm empty"))
    assert [(a.type, a.value) for a in acts] == [("set_trailer_empty", True)]
    assert say == "Got it, running empty. I'll watch the crosswinds for you."
    acts, say, _ = apply_actions([{"type": "set_trailer_empty", "value": None}], _req("x"))
    assert acts == [] and say == "Empty or loaded?"
