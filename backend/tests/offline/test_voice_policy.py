"""Barge-in rules for duplex voice (voice_agent.policy)."""
import pytest

from voice_agent.policy import is_backchannel, should_interrupt

STATEMENT = "The Pilot in Dunnigan has about forty spots, and the entrance is off County Road 8."
QUESTION = "Want me to route you there?"


@pytest.mark.parametrize("heard,agent,stop", [
    ("uh-huh", STATEMENT, False),
    ("yeah okay", STATEMENT, False),
    ("", STATEMENT, False),
    ("wait", STATEMENT, False),  # one word: could be the radio
    ("no wait", STATEMENT, True),
    ("actually find one near Reno", STATEMENT, True),
    ("yeah", QUESTION, True),  # an answer, not a backchannel
    ("go", QUESTION, True),
])
def test_should_interrupt(heard, agent, stop):
    assert should_interrupt(heard, agent) is stop


def test_backchannel():
    assert is_backchannel("Mm-hmm.") and is_backchannel("ok, right") and not is_backchannel("ok take me there")
