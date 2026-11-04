"""The LiveKit interruption gate answers with our barge-in rule (voice_agent.gate)."""
import inspect

import pytest

lk = pytest.importorskip("livekit.agents.voice.agent_activity")

from voice_agent import gate, policy  # noqa: E402


def test_livekit_gate_still_looks_like_we_assume():
    src = inspect.getsource(lk)
    assert src.count('len(split_words(text, split_character=True)) < interruption_options["min_words"]') == 1
    assert "len(split_words(info.new_transcript, split_character=True))" in src


@pytest.mark.parametrize("heard,agent,passes", [
    ("uh-huh okay", "The Pilot has forty spots.", False),
    ("no wait find one near Reno", "The Pilot has forty spots.", True),
    ("yeah", "Want me to route you there?", True),
])
def test_gate(heard, agent, passes):
    split = gate.gated_split_words(lambda: agent)
    assert (len(split(heard, split_character=True)) >= policy.MIN_INTERRUPT_WORDS) is passes


def test_transcript_comparison_untouched():
    split = gate.gated_split_words(lambda: "x")
    assert split("uh-huh", ignore_punctuation=True, split_character=True) == gate._ORIGINAL(
        "uh-huh", ignore_punctuation=True, split_character=True)
