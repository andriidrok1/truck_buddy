"""fit_reply keeps free-form model replies short for the road without ever cutting a sentence in half."""
from services.speech_text import REPLY_MAX_CHARS, SENTENCE_END, fit_reply


def test_two_sentences_pass_through_unchanged():
    t = "Sure thing. The Pilot in Lodi has showers and a Subway."
    assert fit_reply(t) == t


def test_third_sentence_is_dropped_whole():
    t = "Sure thing. The Pilot in Lodi has showers. It also has a Subway and a CAT scale."
    assert fit_reply(t) == "Sure thing. The Pilot in Lodi has showers."


def test_never_cuts_mid_sentence_at_the_char_cap():
    first = "The nearest truck stop is the Flying J in Lodi, right off Highway 99 by the Walmart."
    second = "X" + "x" * (REPLY_MAX_CHARS - len(first) + 5) + "."
    out = fit_reply(f"{first} {second}")
    assert out == first  # second would cross the cap, so it goes, whole


def test_first_sentence_is_kept_even_when_longer_than_the_cap():
    long_first = "A " + "very " * 60 + "long sentence."
    assert fit_reply(long_first) == long_first
    assert len(long_first) > REPLY_MAX_CHARS


def test_abbreviations_do_not_split():
    t = "Head to St. Louis on U.S. 50. Dr. Pepper is in the fridge."
    assert fit_reply(t) == t
    assert SENTENCE_END.split(t) == ["Head to St. Louis on U.S. 50.", "Dr. Pepper is in the fridge."]


def test_whitespace_is_normalised_and_empty_is_empty():
    assert fit_reply("  Sure.   Got it.  ") == "Sure. Got it."
    assert fit_reply("") == "" and fit_reply(None) == ""  # type: ignore[arg-type]
    assert fit_reply("\n\n") == ""


def test_question_and_exclamation_count_as_sentence_ends():
    assert fit_reply("Need fuel? Got you! Two stops ahead.") == "Need fuel? Got you!"


def test_custom_limits():
    t = "One. Two. Three. Four."
    assert fit_reply(t, max_sentences=3) == "One. Two. Three."
    assert fit_reply(t, max_sentences=4, max_chars=9) == "One. Two."
