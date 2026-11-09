"""Company driver or owner-operator: asked once in onboarding, kept in the truck profile (2026-11-08)."""
import pytest
from conftest import run

from models import TruckProfile
from services import onboarding


@pytest.mark.parametrize("said,want", [
    ("It's my own truck", "owner"), ("owner operator", "owner"), ("I'm leased on with my truck", "owner"),
    ("I drive for a company", "company"), ("company driver, Swift", "company"), ("I work for a carrier", "company"),
    ("свой трак", "owner"), ("работаю на компанию", "company"),
])
def test_common_answers_need_no_model(said, want):
    assert onboarding.operator_of(said) == {"operator": want}
    assert run(onboarding.extract("work", said)) == {"operator": want}


def test_unclear_or_both_goes_to_the_model():
    assert onboarding.operator_of("hmm not sure") == {}
    assert onboarding.operator_of("I drive for a company, not my own truck") == {}


def test_clean_keeps_only_the_two_values():
    assert onboarding._clean({"operator": "owner"}) == {"operator": "owner"}
    assert onboarding._clean({"operator": "boss"}) == {}


def test_the_agent_knows_it():
    assert "owner-operator" in TruckProfile(operator="owner").prefs_label
    assert "company driver" in TruckProfile(operator="company").prefs_label
    assert TruckProfile().operator is None
