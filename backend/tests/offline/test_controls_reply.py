"""QA 2026-10-31, H15: "I'm pulling a 40 foot trailer now" while rolling came back as "Done." The server
said "Done. Trailer is now 40 feet. Rerouting for that." and the speed fit kept the first sentence only.
A profile change is confirmed in one sentence, so the first sentence is the whole confirmation."""
from models import AgentRequest, Place
from services.agent import fit_reply
from services.controls import apply_actions

P = Place(id="x", name="X", category="truck_stop", lat=38.6, lng=-121.5)


def _req(active=True):
    return AgentRequest(text="t", session_id="s", active_place=P if active else None, lat=38.58, lng=-121.54)


def test_profile_change_is_one_sentence_that_survives_the_speed_cut():
    acts, say, end = apply_actions([{"type": "set_trailer_ft", "value": 40}], _req())
    assert say == "Done, trailer is now 40 feet, rerouting for that."
    assert fit_reply(say, 1) == say
    assert [a.type for a in acts] == ["set_trailer_ft", "reroute"] and not end


def test_two_changes_join_into_one_sentence():
    _, say, _ = apply_actions([{"type": "set_height_in", "value": 162}, {"type": "set_hazmat", "value": True}], _req(active=False))
    assert say == "Done, height is now 13 foot 6, hazmat is on, I'll keep you on hazmat-legal roads."


def test_a_rejected_value_and_a_change_still_make_one_sentence():
    _, say, _ = apply_actions([{"type": "set_trailer_ft", "value": 80}, {"type": "set_weight_lb", "value": 80000}], _req(active=False))
    assert say == "Done, the trailer has to be between 10 and 65 feet, weight is now 80,000 pounds."


def test_a_question_back_keeps_the_sentences_apart():
    _, say, _ = apply_actions([{"type": "set_weight_lb", "value": 80000}, {"type": "set_nudges"}], _req(active=False))
    assert say == "Done. Weight is now 80,000 pounds. Suggestions on or off?"


def test_i_stays_capital_in_the_joined_sentence():
    """"remember I like Pilot and I hate tolls" came back as "…favorites, i'll avoid toll roads." (H19)."""
    _, say, _ = apply_actions([{"type": "add_favorite_chain", "value": "Pilot"}, {"type": "avoid_tolls", "value": True}], _req(active=False))
    assert say == "Done, added Pilot to your favorites, I'll avoid toll roads."
