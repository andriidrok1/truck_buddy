"""QA 2026-10-31, H21: "trailer tire, I can't roll on it" listed a shop-only provider as the top pick with
nothing said about it. When the truck can't move and nobody nearby is known to come out, that is said first."""
import pytest

from services.breakdown import SHOP_ONLY_NOTE, shop_only_note


def _p(shop_only, mobile=None):
    return {"name": "Saa Mobile Alignment", "distance_mi": 8.1, "shop_only": shop_only,
            "facts": {"mobile_service": mobile if mobile is not None else ("unknown" if shop_only else True), "prices": []}}


def test_note_only_when_stuck_and_nobody_comes_out():
    assert shop_only_note([_p(True)], "mobile_needed") == SHOP_ONLY_NOTE
    assert shop_only_note([_p(False, True)], "mobile_needed") == ""
    assert shop_only_note([_p(True)], "shop_ok") == ""
    assert shop_only_note([], "mobile_needed") == ""


def _shops(monkeypatch, source="google"):
    from conftest import run

    from models import AgentRequest
    from services import breakdown
    from services import providers_search as ps
    from services.dialog_state import DialogState

    def cand(id, name, mi):
        return {"id": id, "name": name, "lat": 38.58, "lng": -121.5, "phone": f"+1555000{id}", "website": None,
                "address": "1 A St, Sacramento, CA", "rating": 4.4, "rating_count": 12, "distance_mi": mi,
                "open_now": True, "listed_24_7": False, "shop_only": True, "score": 1.0, "source": source,
                "types": ["car_repair"], "reviews": [], "facts": dict(ps.EMPTY_FACTS)}

    async def research(kind, mode, lat, lng, budget=None):
        # ranked best first: the top pick is not the closest
        return {"providers": [cand("1", "Gold Rush Truck Repair", 6.4), cand("2", "Saa Tire Shop", 2.1)],
                "source": source, "stats": {"stages_ms": {}, "llm_calls": 0}}

    async def where(lat, lng):
        return "I-80, Sacramento, CA"

    async def summarize(d, mode, providers):  # what the model wrote: a shop as the answer, nothing about getting there
        return ("Okay, here's what I found. Best bet is Gold Rush Truck Repair, four point four stars. "
                "Tap one to call, or ask me anything about them.", "Gold Rush Truck Repair is the best choice.")

    monkeypatch.setattr(ps, "research", research)
    monkeypatch.setattr(breakdown, "where", where)
    monkeypatch.setattr(breakdown, "summarize", summarize)
    return run(breakdown._run({"kind": "tire", "detail": "trailer tire", "mode": "mobile_needed"},
                              AgentRequest(text="trailer tire, I can't roll on it", session_id="bd", lat=38.58, lng=-121.49),
                              DialogState(), "test"))


NO_MOBILE = ("I couldn't find anyone nearby who comes out to you. The closest shop is Saa Tire Shop, about 2.1 miles, "
             "but you'd need a tow to get there. Tap it to call, or ask me to look for a tow.")


@pytest.mark.parametrize("source", ["google", "here"])
def test_no_mobile_service_is_said_with_the_closest_shop_and_a_tow(monkeypatch, source):
    """QA 2026-11-02: "I can't roll on it" and the only results were shops, labelled "Shop only" on the card while
    the copilot named the best one as the answer. Nobody comes out: said plainly, the closest shop offered with
    a note that a tow is needed to get there."""
    res = _shops(monkeypatch, source)
    assert res.reply == NO_MOBILE
    assert res.summary == "Nobody nearby says they come out. Closest shop: Saa Tire Shop, 2.1 mi, needs a tow."
