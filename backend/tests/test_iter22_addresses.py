# Iteration 22: spoken addresses -> digits, street-only follow-ups get the town, read-back on no match
import pytest

from services.agent import city_of, heard_not_found, with_city
from services.geocode import words_to_digits
from models import Place


@pytest.mark.parametrize("spoken,want", [
    ("twenty eight twenty eight El Centro Road, Sacramento", "2828 El Centro Road, Sacramento"),
    ("two eight two eight El Centro Rd", "2828 El Centro Rd"),
    ("twenty-eight twenty-eight El Centro", "2828 El Centro"),
    ("Interstate eighty", "Interstate 80"),
    ("twelve hundred Main St, Dallas", "1200 Main St, Dallas"),
    ("one hundred and ten Main St", "110 Main St"),
    ("three thousand four hundred five Oak Dr", "3405 Oak Dr"),
    ("seven oh one Elm Street", "701 Elm Street"),
    ("fifty fifth street", "55th street"),
    ("Second Street", "2nd Street"),
    ("one Market Street, San Francisco", "1 Market Street, San Francisco"),
    ("the one on Main", "the one on Main"),
    ("Reno, NV", "Reno, NV"),
    ("2828 El Centro Rd", "2828 El Centro Rd"),
])
def test_words_to_digits(spoken, want):
    assert words_to_digits(spoken) == want


def _p(name):
    return Place(id="x", name=name, category="destination", lat=0, lng=0)


def test_street_only_gets_previous_city():
    assert with_city("2828 El Centro Rd", "Sacramento, CA") == "2828 El Centro Rd, Sacramento, CA"
    assert with_city("2828 El Centro Rd, Sacramento", "Reno, NV") == "2828 El Centro Rd, Sacramento"
    assert with_city("Reno", "Sacramento") == "Reno"
    assert with_city("2828 El Centro Rd", None) == "2828 El Centro Rd"


def test_city_of():
    assert city_of("Sacramento, CA", _p("Sacramento, CA, United States")) == "Sacramento, CA"
    assert city_of("2828 El Centro Rd, Sacramento, CA 95833", _p("2828 El Centro Rd")) == "Sacramento"
    assert city_of("Sacramento railway station", _p("Gail Way, Sacramento")) is None


def test_read_back():
    assert heard_not_found("2828 El Centro Road, Sacramento") == \
        "I heard 2828 El Centro Road, Sacramento, but can't find it. Say it again or type it."


@pytest.mark.parametrize("text,drop", [
    ("For more information visit www.FEMA.gov", True),
    ("Thanks for watching!", True),
    ("Truck driver talking to a copilot.", True),
    ("Take me to 2828 El Centro Rd, Sacramento", False),
    ("where do I park", False),
])
def test_whisper_hallucinations_dropped(text, drop):
    from services.voice import HALLUCINATION, PROMPT_ECHO
    assert bool(HALLUCINATION.search(text) or PROMPT_ECHO.search(text)) == drop
