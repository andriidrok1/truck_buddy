"""The classifier prompt is cache-stable: one static system prompt for everyone, the driver's data in the
user message, a fixed cache key, and the prefix is long enough for OpenAI to cache it."""
import re

import pytest
from conftest import run

from models import AgentRequest, TruckProfile
from services import agent, controls, approach, breakdown
from services.agent import PROMPT_CACHE_KEY, SYSTEM_STATIC, prompt_messages

SAC = {"lat": 38.5816, "lng": -121.4944}


def test_system_is_identical_across_drivers_and_turns():
    a = TruckProfile(height_in=162, trailer_ft=53, weight_lb=80000, hazmat=True, driver_name="Mike",
                     favorite_chains=["Pilot"], avoid_tolls=True)
    b = TruckProfile(height_in=150, trailer_ft=28, weight_lb=40000)
    sys_a, user_a = prompt_messages("find fuel", "ctx one", a)
    sys_b, user_b = prompt_messages("take me to Reno", "ctx two", b)
    assert sys_a == sys_b == SYSTEM_STATIC
    assert "{" not in SYSTEM_STATIC.replace('{"intent"', "").replace("{", "", 1) or "{truck}" not in SYSTEM_STATIC
    assert "{truck}" not in SYSTEM_STATIC and "{prefs}" not in SYSTEM_STATIC
    assert user_a.startswith("Truck: ") and "Driver preferences: " in user_a.split("\n")[0]
    assert "Mike" in user_a and "Pilot" in user_a and "[ctx one]\nDriver said: find fuel" in user_a
    assert "Mike" not in sys_a


def test_sub_prompts_are_in_the_static_system():
    for chunk in (controls.PROMPT, approach.PROMPT, breakdown.PROMPT):
        assert chunk.strip() in SYSTEM_STATIC
    assert "hands-free voice copilot" in SYSTEM_STATIC


def test_classify_uses_the_cache_key_and_static_system(agent_sandbox):
    agent_sandbox.llm.on(r"fuel", {"intent": "find", "category": "truck_stop"})
    truck = TruckProfile(driver_name="Mike")
    run(agent.classify("I need fuel", "ctx", truck))
    system, user, model = agent_sandbox.llm.calls[-1]
    assert system == SYSTEM_STATIC and user.startswith("Truck: ") and model == "gpt-5.4-mini"
    assert agent_sandbox.llm.cache_keys[-1] == PROMPT_CACHE_KEY == "agent-v4"


def test_static_prefix_is_long_enough_to_cache():
    tiktoken = pytest.importorskip("tiktoken")
    enc = tiktoken.get_encoding("o200k_base")
    n = len(enc.encode(SYSTEM_STATIC))
    assert n >= 1100, f"static system prompt is {n} tokens; OpenAI caches prefixes of 1,024+"


def test_intent_enum_still_parseable_for_evals():
    m = re.search(r'\{"intent": ([^,]+), "category"', SYSTEM_STATIC)
    assert m and "find" in m.group(1) and "unclear" in m.group(1)


def test_end_to_end_turn_still_works(agent_sandbox):
    agent_sandbox.llm.default = {"intent": "chat", "reply": "Hello there."}
    res = run(agent_sandbox.provider().handle(AgentRequest(text="hey", session_id="pc", **SAC), None))
    assert res.status == "chat" and res.reply == "Hello there."
