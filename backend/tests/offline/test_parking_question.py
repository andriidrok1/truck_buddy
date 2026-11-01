"""QA 2026-10-31, H6: "how do I get in" and "where do I park" got the same three sentences (fit verdict,
entrance, where to park). The answer now follows the question; a warning verdict is always said."""
from models import ParkingCheck
from services import approach
from services.approach import asked, speech


def _check(verdict="likely", relative=True):
    return ParkingCheck(verdict=verdict, label="x", reason="x", source="satellite", relative=relative,
                        entry_tip="On your left, off West Capitol Ave", park_tip="Park along the east side near existing trailers")


def test_the_question_picks_the_part():
    assert asked("how do I get in") == "entry" and asked("which side is the entrance") == "entry"
    assert asked("where do I park") == "park" and asked("where should I park the rig") == "park"
    assert asked("is there parking") == "all" and asked("how do I get in and where do I park") == "all"


def test_entrance_question_gets_the_entrance_only_when_the_fit_is_fine():
    assert speech(_check(), "entry") == "The truck entrance is on your left, off West Capitol Ave."
    assert speech(_check(), "park") == "Park along the east side near existing trailers."
    assert speech(_check(), "all") == ("From the satellite, it looks like your rig will fit. The truck entrance is on your "
                                       "left, off West Capitol Ave. Park along the east side near existing trailers.")


def test_a_tight_fit_is_said_whatever_the_question():
    assert speech(_check("unlikely"), "entry").startswith("Heads up, from the satellite it looks tight for a rig. The truck entrance")
    assert speech(_check("unlikely"), "park").startswith("Heads up, from the satellite it looks tight for a rig. Park along")


def test_without_a_tip_the_full_answer_is_given():
    c = ParkingCheck(verdict="likely", label="x", reason="x", source="satellite", entry_tip=None, relative=False, park_tip=None)
    assert speech(c, "entry") == speech(c, "park") == "From the satellite, it looks like your rig will fit."
