"""Duplex voice: a reply the driver cut off is kept in the dialog history only as far as it was heard."""
from conftest import run

from services import session_store
from services.agent import LlmAgentProvider


def test_cut_off_reply_is_trimmed_to_what_was_heard(monkeypatch):
    monkeypatch.setenv("SESSION_STORE", "memory")
    store = session_store.get_store()
    state = run(store.load("drv_t:dlg-cut"))
    state["history"].extend(["Driver: where can I park", "Copilot: The Pilot in Dunnigan has forty spots and showers."])
    run(store.save("drv_t:dlg-cut", state))
    assert run(LlmAgentProvider().heard_only("drv_t:dlg-cut", "The Pilot in   Dunnigan")) is True
    hist = run(store.load("drv_t:dlg-cut"))["history"]
    assert hist == ["Driver: where can I park", "Copilot (cut off by the driver): The Pilot in Dunnigan"]


def test_nothing_heard_and_no_line(monkeypatch):
    monkeypatch.setenv("SESSION_STORE", "memory")
    store = session_store.get_store()
    state = run(store.load("drv_t:dlg-empty"))
    state["history"].append("Copilot: Hi Mike.")
    run(store.save("drv_t:dlg-empty", state))
    assert run(LlmAgentProvider().heard_only("drv_t:dlg-empty", "")) is True
    assert run(store.load("drv_t:dlg-empty"))["history"] == ["Copilot: (cut off before saying anything)"]
    assert run(LlmAgentProvider().heard_only("drv_t:dlg-none", "x")) is False
