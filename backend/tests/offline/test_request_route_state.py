"""What the app sends about its screen and its route (2026-11-02): the ids of the cards on screen in screen order
(ui.option_ids) for "the second one", and the active route's HERE handle and stops (route_handle, route_via), so a
dialog that starts after a restart checks the route actually driven, stops included. All optional: an older app
sends none of them and gets what it got before."""
import pytest
from conftest import fixture_json, run
from pydantic import ValidationError

from models import AgentRequest, ChainControl, Place, UiState
from services import chains
from services.agent import list_reference, numbered, pick_place

DATA = fixture_json("here/route_sac_reno")
SAC = {"lat": 38.5816, "lng": -121.4944}
RENO = Place(id="reno", name="Reno, NV, United States", category="destination", lat=39.5296, lng=-119.8138)
FERNLEY = [39.6, -119.25]


def _p(id, name, mi):
    return Place(id=id, name=name, category="truck_stop", lat=38.58, lng=-121.5 - mi / 100, distance_mi=mi,
                 address=f"{int(mi * 100)} W Capitol Ave, West Sacramento, CA")


ROADYS, WESTSAC, FORTYNINER, SACVALLEY = _p("roadys", "Roady's Truck Stop", 4.2), _p("westsac", "West Sac Truck Stop", 1.2), \
    _p("49er", "Sacramento 49er Travel Plaza", 2.7), _p("sacvalley", "Sac Valley Truck Stop", 8.7)


def test_the_new_fields_are_optional_and_bounded():
    req = AgentRequest(text="hi", **SAC)
    assert req.ui.option_ids == [] and req.route_handle is None and req.route_via == []
    with pytest.raises(ValidationError):
        UiState(option_ids=[str(i) for i in range(21)])
    # a broken stop from the app never fails the turn: it is left out
    req = AgentRequest(text="hi", route_via=[FERNLEY, [1.0], [200.0, 5.0], ["x", "y"]], **SAC)
    assert req.route_via == [FERNLEY]


def test_numbering_follows_the_cards_on_screen():
    places = [ROADYS, WESTSAC, FORTYNINER, SACVALLEY]  # as the dialog got them
    screen = [SACVALLEY.id, FORTYNINER.id, WESTSAC.id]  # as the app shows them
    assert [p.id for p in numbered(places, ROADYS.id, screen)] == [SACVALLEY.id, FORTYNINER.id, WESTSAC.id, ROADYS.id]
    ref = list_reference("the second one", places, ROADYS.id, screen)
    assert pick_place({"place_index": ref}, places, ROADYS.id, screen).id == FORTYNINER.id
    assert pick_place({"place_index": list_reference("the last one", places, ROADYS.id, screen)}, places, ROADYS.id,
                      screen).id == WESTSAC.id
    # ids the dialog doesn't know are skipped; no ids: the order the options came in, as before
    assert [p.id for p in numbered(places, ROADYS.id, ["gone", WESTSAC.id])][:1] == [WESTSAC.id]
    assert list_reference("the second one", places, ROADYS.id, []) == list_reference("the second one", places, ROADYS.id)


def test_the_second_one_is_the_second_card_the_app_says_it_shows(agent_sandbox, fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    agent_sandbox.places_provider.places = [ROADYS, WESTSAC, FORTYNINER, SACVALLEY]
    agent_sandbox.llm.on(r"park tonight", {"intent": "find", "category": "truck_stop"})
    agent_sandbox.llm.on(r"^the second one$", {"intent": "pick", "place_index": 2})
    prov = agent_sandbox.provider()
    first = run(prov.handle(AgentRequest(text="where can I park tonight", session_id="oi", **SAC), None))
    ids = [p.id for p in first.options]
    shown = ids[1:] + ids[:1]  # the app sorted its cards its own way
    assert len(ids) == 3 and shown[1] != ids[1]
    res = run(prov.handle(AgentRequest(text="the second one", session_id="oi", active_place=first.place,
                                       ui=UiState(option_ids=shown), **SAC), None))
    assert res.status == "route_set" and res.place.id == shown[1], res.reply


def _chains_on(monkeypatch):
    donner = ChainControl(id="d1", route="I-80", location="Donner Summit", status="R-2", lat=39.3167, lng=-120.3300)

    async def controls(geometry):
        return [donner]
    monkeypatch.setattr(chains, "for_route", controls)


def test_after_a_restart_chains_are_checked_on_the_route_by_its_handle(agent_sandbox, fake_http, monkeypatch):
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    agent_sandbox.llm.on(r"chain", {"intent": "chat", "reply": "There may be chain controls."})
    _chains_on(monkeypatch)
    prov = agent_sandbox.provider()
    res = run(prov.handle(AgentRequest(text="any chain controls ahead", session_id="rh", active_place=RENO,
                                       route_handle="AHandleOfTheRoute", route_via=[FERNLEY], **SAC), None))
    assert res.reply == "Heads up, chains required on I-80 near Donner Summit.", res.reply
    calls = fake_http.calls_to("router.hereapi.com")
    assert len(calls) == 1 and calls[0].url.path.endswith("/AHandleOfTheRoute")


def test_after_a_restart_without_a_handle_the_plan_goes_through_the_apps_stops(agent_sandbox, fake_http, monkeypatch):
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    agent_sandbox.llm.on(r"chain", {"intent": "chat", "reply": "There may be chain controls."})
    agent_sandbox.llm.on(r"another way", {"intent": "alternative"})
    _chains_on(monkeypatch)
    prov = agent_sandbox.provider()
    run(prov.handle(AgentRequest(text="any chain controls ahead", session_id="rv", active_place=RENO,
                                 route_via=[FERNLEY], **SAC), None))
    run(prov.handle(AgentRequest(text="is there another way", session_id="rv2", active_place=RENO,
                                 route_via=[FERNLEY], **SAC), None))
    calls = fake_http.calls_to("router.hereapi.com")
    assert len(calls) == 2
    for c in calls:
        assert c.url.params["via"].startswith(f"{FERNLEY[0]},{FERNLEY[1]}"), c.url
