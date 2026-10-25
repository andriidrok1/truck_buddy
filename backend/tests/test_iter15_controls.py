from models import AgentRequest, Place, UiState
from services.controls import apply_actions

PLACE = Place(id="x", name="Redwoods", category="destination", lat=38.6, lng=-121.3)


def req(active=True, **ui):
    return AgentRequest(text="t", lat=38.5, lng=-121.4, active_place=PLACE if active else None, ui=UiState(**ui))


def types(acts):
    return [(a.type, a.value) for a in acts]


def test_start_navigation_needs_route():
    acts, say, _ = apply_actions([{"type": "start_navigation"}], req(active=False))
    assert acts == [] and "no route" in say.lower()
    acts, say, _ = apply_actions([{"type": "start_navigation"}], req())
    assert types(acts) == [("start_navigation", None)]
    acts, say, _ = apply_actions([{"type": "start_navigation"}], req(driving=True))
    assert acts == [] and "already" in say


def test_truck_changes_reroute_when_active():
    acts, say, _ = apply_actions([{"type": "set_height_in", "value": "158"}, {"type": "set_hazmat", "value": True}], req())
    assert types(acts) == [("set_height_in", 158), ("set_hazmat", True), ("reroute", None)]
    assert say.startswith("Done.") and "13 foot 2" in say
    acts, _, _ = apply_actions([{"type": "set_weight_lb", "value": 70000}], req(active=False))
    assert types(acts) == [("set_weight_lb", 70000)]


def test_out_of_range_rejected():
    acts, say, _ = apply_actions([{"type": "set_height_in", "value": 300}], req())
    assert acts == [] and "between" in say


def test_favorites_and_break():
    r = req()
    r.truck_profile.favorite_chains = ["Pilot"]
    acts, say, _ = apply_actions([{"type": "add_favorite_chain", "value": "pilot"}], r)
    assert acts == [] and "already" in say
    acts, _, _ = apply_actions([{"type": "remove_favorite_chain", "value": "PILOT"}], r)
    assert types(acts) == [("remove_favorite_chain", "Pilot")]
    acts, _, _ = apply_actions([{"type": "set_break_hours", "value": None}], r)
    assert types(acts) == [("set_break_hours", None)]


def test_options_and_stop_talking():
    acts, say, _ = apply_actions([{"type": "show_options"}], req(options_count=0))
    assert acts == []
    acts, say, end = apply_actions([{"type": "stop_talking"}], req())
    assert end and types(acts) == [("stop_talking", None)]


def test_garbage_is_ignored():
    assert apply_actions("nope", req()) == ([], "", False)
    assert apply_actions([{"type": "launch_rockets"}], req()) == ([], "", False)
