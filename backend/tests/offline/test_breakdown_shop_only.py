"""QA 2026-10-31, H21: "trailer tire, I can't roll on it" listed a shop-only provider as the top pick with
nothing said about it. When the truck can't move and nobody nearby is known to come out, that is said first."""
from services.breakdown import SHOP_ONLY_NOTE, shop_only_note


def _p(shop_only, mobile=None):
    return {"name": "Saa Mobile Alignment", "distance_mi": 8.1, "shop_only": shop_only,
            "facts": {"mobile_service": mobile if mobile is not None else ("unknown" if shop_only else True), "prices": []}}


def test_note_only_when_stuck_and_nobody_comes_out():
    assert shop_only_note([_p(True)], "mobile_needed") == SHOP_ONLY_NOTE
    assert shop_only_note([_p(False, True)], "mobile_needed") == ""
    assert shop_only_note([_p(True)], "shop_ok") == ""
    assert shop_only_note([], "mobile_needed") == ""
