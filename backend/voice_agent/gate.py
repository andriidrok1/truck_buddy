"""Our barge-in rule (voice_agent.policy) inside LiveKit's interruption gate.

LiveKit 1.8.10 decides whether speech over the agent may interrupt it in two places in
voice/agent_activity.py, both as `len(split_words(text, split_character=True)) < min_words`. It has no hook
for a custom rule, so install() swaps that module's split_words for one that answers the gate with our rule:
enough words when policy.should_interrupt() says so, none otherwise. Calls that pass ignore_punctuation
(transcript comparison) go to the original untouched. Pinned to livekit-agents 1.8.10; the offline test fails
if the gate's shape changes, which is the signal to re-check this file on an upgrade.
"""
from __future__ import annotations

from typing import Callable

from livekit.agents.voice import agent_activity

from voice_agent import policy

_ORIGINAL = agent_activity.split_words


def gated_split_words(agent_text: Callable[[], str]):
    def split_words(text, *args, **kwargs):
        words = _ORIGINAL(text, *args, **kwargs)
        if kwargs.get("ignore_punctuation") or args:
            return words
        if not policy.should_interrupt(text or "", agent_text()):
            return []
        # the gate compares with min_words (policy.MIN_INTERRUPT_WORDS); one word after a question must pass it
        pad = max(0, policy.MIN_INTERRUPT_WORDS - len(words))
        return list(words) + [("", 0, 0)] * pad
    return split_words


def install(agent_text: Callable[[], str]) -> None:
    """agent_text() returns what the agent is saying now (its current reply), for the after-a-question rule."""
    agent_activity.split_words = gated_split_words(agent_text)
