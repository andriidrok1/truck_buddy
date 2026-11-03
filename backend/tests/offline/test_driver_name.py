"""QA 2026-11-02 (UX): "Mike" was in three of four small-talk replies, the profile line asks the model for "now
and then". The rule lives in code: the name at most once in NAME_EVERY replies of a dialog, never in a short
reply, and any extra direct address the model wrote is cut out."""
import re

import pytest
from conftest import run

from models import AgentRequest, Place, TruckProfile
from services.agent import NAME_EVERY, name_policy, without_name

SAC = {"lat": 38.5816, "lng": -121.4944}


@pytest.mark.parametrize("said, cut", [
    ("Mike, you've got about four hours left today.", "You've got about four hours left today."),
    ("Sure thing, Mike. I'll keep an eye on it.", "Sure thing. I'll keep an eye on it."),
    ("Okay, Mike.", "Okay."),
    ("Thanks Mike!", "Thanks!"),
    ("Hey Mike, the Pilot is two miles ahead.", "The Pilot is two miles ahead."),
    ("All good. Mike, want me to find fuel?", "All good. Want me to find fuel?"),
    ("Call Mike's Mobile Service, they come out.", "Call Mike's Mobile Service, they come out."),  # not an address
])
def test_direct_address_is_cut(said, cut):
    assert without_name(said, "Mike") == cut


# Review 2026-11-02: the name was cut wherever it was the last word before a comma or a full stop, and drivers have
# names that are towns and words. Only a vocative goes: "Name, ..." / "Hey Name, ..." opening a sentence, or
# ", Name." closing one.
@pytest.mark.parametrize("name, said", [
    ("Austin", "Okay, heading to Austin, 240 miles, about four hours."),
    ("Austin", "The closest truck stop is in Austin."),
    ("Dallas", "Pilot Travel Center, Dallas, has diesel at $3.89."),
    ("Jackson", "Your stop is Love's in Jackson. New total 3 h, 200 miles."),
    ("Will", "Sounds good, I will."),
    ("Will", "Love's has showers. If you want, I can add it and I will."),
    ("Chase", "Weather looks clear, nothing to chase."),
    ("Austin", "Austin, TX is about 240 miles out."),
    ("Mike", "Drive safe, Mike, and holler if you need anything."),  # mid-sentence: not cut
])
def test_a_name_that_is_not_an_address_stays(name, said):
    assert without_name(said, name) == said
    assert name_policy(said, name, False) == (said, False)


def test_a_name_that_is_a_place_of_the_dialog_stays():
    jackson = Place(id="j", name="Jackson, MS", category="destination", lat=32.3, lng=-90.18)
    said = "Okay, Jackson. About three hours from here, all on I-20."
    assert without_name(said, "Jackson") == "Okay. About three hours from here, all on I-20."
    assert name_policy(said, "Jackson", False, [jackson]) == (said, False)


def _dialog(agent_sandbox, replies, driver="Mike Johnson"):
    for i, r in enumerate(replies):
        agent_sandbox.llm.on(rf"^line {i}$", {"intent": "chat", "reply": r})
    prov = agent_sandbox.provider()
    truck = TruckProfile(driver_name=driver)
    return [run(prov.handle(AgentRequest(text=f"line {i}", session_id="name", truck_profile=truck, **SAC), None)).reply
            for i in range(len(replies))]


def test_a_name_that_is_not_an_address_does_not_use_the_turn(agent_sandbox):
    said = _dialog(agent_sandbox, [
        "Okay, heading to Austin, 240 miles, about four hours of driving.",
        "Austin, the weather looks clear all the way there tonight.",
    ], driver="Austin Reed")
    assert said[0] == "Okay, heading to Austin, 240 miles, about four hours of driving."
    assert said[1] == "Austin, the weather looks clear all the way there tonight."


def test_the_name_once_in_a_while_and_never_in_short_replies(agent_sandbox):
    said = _dialog(agent_sandbox, [
        "Sure thing, Mike.",  # short: never
        "Mike, the weather looks clear all the way to Reno tonight.",  # long and the first: kept
        "No problem, Mike, I've got the rest of the trip covered for you.",  # too soon
        "Mike, you're doing great, about two hours left to Reno now.",  # too soon
        "Alright Mike, holler if you need fuel or a place to park later on.",  # too soon (3 since)
        "Mike, the Pilot up ahead has showers and a good diner, by the way.",  # 4 since: kept
    ])
    assert NAME_EVERY == 4
    named = [i for i, r in enumerate(said) if re.search(r"\bMike\b", r)]
    assert named == [1, 5], said
    assert said[0] == "Sure thing."
    assert said[2] == "No problem, I've got the rest of the trip covered for you."
    assert said[4] == "Alright, holler if you need fuel or a place to park later on."


def test_twice_in_one_reply_keeps_the_first(agent_sandbox):
    said = _dialog(agent_sandbox, ["Mike, you've got about four hours left today. Drive safe, Mike."])
    assert said == ["Mike, you've got about four hours left today. Drive safe."]
