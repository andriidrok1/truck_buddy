"""QA 2026-11-02 (UX): the entry tip read "On your left, off West Capitol Ave, the wide driveway off the road": the
landmark phrase from the satellite note ended in its own "off the road" right after the route's "off <street>".
A landmark clause that only says the road again (bare "off the road" / "off the frontage road", or the same
street) is left out when the phrase is put together; what is left of the landmark stays."""
import pytest

from models import Approach, ParkingCheck, ParkingSpot
from services import approach

E = (38.5800, -121.5300)


def _ap(street):
    # heading east 100 m south-west of E: E is on the left
    return Approach(bearing=90, compass="west", street=street, lat=E[0] - 0.0004, lng=E[1] - 0.0012)


def _check():
    entry = ParkingSpot(kind="entry", x=0.5, y=0.4, lat=E[0], lng=E[1])
    return ParkingCheck(verdict="likely", label="", reason="", source="satellite", image_path="/s",
                        entry_tip="Use the wide driveway off the road", spots=[entry])


@pytest.mark.parametrize("street, marks, tip", [
    ("West Capitol Ave", "the wide driveway off the road", "On your left, off West Capitol Ave, the wide driveway"),
    ("West Capitol Ave", "off the frontage road", "On your left, off West Capitol Ave"),
    ("West Capitol Ave", "the second driveway, off the frontage road", "On your left, off West Capitol Ave, the second driveway"),
    ("W Capitol Ave", "off West Capitol Avenue, past the fuel canopy", "On your left, off W Capitol Ave, past the fuel canopy"),
    ("West Capitol Ave", "the second driveway from Capitol Ave", "On your left, off West Capitol Ave, the second driveway"),
    ("West Capitol Ave", "just after the landscaped island", "On your left, off West Capitol Ave, just after the landscaped island"),
    ("West Capitol Ave", "past the road sign", "On your left, off West Capitol Ave, past the road sign"),
    (None, "off the frontage road", "On your left, off the frontage road"),  # no street named: the road is the landmark
])
def test_the_landmark_does_not_repeat_the_street(street, marks, tip):
    assert approach._phrase(_check(), _ap(street), marks).entry_tip == tip
