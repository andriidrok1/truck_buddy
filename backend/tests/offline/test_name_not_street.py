"""QA 2026-10-31, H9/H23: in a fresh dialog "take me to the 49er" has no list to match, HERE geocodes "49er"
to a bare street (49ER Trl, Placerville, 40 miles the wrong way) and the route went there. A short name that
geocodes to a street, or to nothing, is looked up among the truck stops nearby first."""
from conftest import fixture_json, run

from models import AgentRequest, Place
from services.agent import name_like
from services.geocode import is_street

DATA = fixture_json("here/route_sac_reno")
SAC = {"lat": 38.5816, "lng": -121.4944}
NINER = Place(id="sac49", name="Sacramento 49er Travel Plaza", category="truck_stop", lat=38.62, lng=-121.52,
              distance_mi=5.3, address="2828 El Centro Rd, Sacramento, CA")
ROADYS = Place(id="roadys", name="Roady's Truck Stop", category="truck_stop", lat=38.58, lng=-121.55, distance_mi=4.6,
               address="2816 W Capitol Ave, West Sacramento, CA")
STREET = Place(id="here-street-abc", name="49ER Trl, Placerville, CA 95667, United States", category="destination",
               lat=38.73, lng=-120.80, address="49ER Trl, Placerville, CA 95667, United States")


def _prov(agent_sandbox, fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    agent_sandbox.places_provider.places = [ROADYS, NINER]
    agent_sandbox.llm.on(r"take me to the 49er", {"intent": "go", "query": "49er"})
    agent_sandbox.llm.on(r"take me to reno", {"intent": "go", "query": "Reno, NV"})
    agent_sandbox.llm.on(r"stop at the 49er", {"intent": "add_stop", "category": "truck_stop", "query": "49er"})
    agent_sandbox.geocode["49er"] = [STREET]
    return agent_sandbox.provider()


def test_a_name_that_geocodes_to_a_street_is_the_truck_stop_nearby(agent_sandbox, fake_http):
    prov = _prov(agent_sandbox, fake_http)
    res = run(prov.handle(AgentRequest(text="take me to the 49er", session_id="fresh", **SAC), None))
    assert res.status == "route_set" and res.place.id == NINER.id, res.reply
    assert "49ER Trl" not in res.reply


def test_a_street_result_stands_when_no_truck_stop_has_that_name(agent_sandbox, fake_http):
    prov = _prov(agent_sandbox, fake_http)
    agent_sandbox.llm.on(r"take me to elm", {"intent": "go", "query": "Elm"})
    agent_sandbox.geocode["elm"] = [Place(id="here-street-elm", name="Elm St, Sacramento, CA", category="destination",
                                          lat=38.59, lng=-121.49, address="Elm St, Sacramento, CA")]
    res = run(prov.handle(AgentRequest(text="take me to Elm", session_id="fresh2", **SAC), None))
    assert res.status == "route_set" and res.place.id == "here-street-elm"


def test_a_town_is_never_swapped_for_a_truck_stop(agent_sandbox, fake_http):
    prov = _prov(agent_sandbox, fake_http)
    reno = Place(id="here-town-reno", name="Reno, NV, United States", category="destination", lat=39.53, lng=-119.81)
    agent_sandbox.geocode["reno"] = [reno]
    res = run(prov.handle(AgentRequest(text="take me to Reno", session_id="fresh3", **SAC), None))
    assert res.status == "route_set" and res.place.id == reno.id


def test_a_named_stop_on_the_way_in_a_fresh_dialog(agent_sandbox, fake_http):
    prov = _prov(agent_sandbox, fake_http)
    res = run(prov.handle(AgentRequest(text="stop at the 49er on the way", session_id="fresh4", active_place=ROADYS, **SAC), None))
    assert res.status == "route_set" and [o.id for o in res.options] == [NINER.id], res.reply


def test_name_like_and_is_street():
    for q in ("the 49er", "Roady's", "Pilot", "Flying J", "Love's Travel Stop"):
        assert name_like(q), q
    for q in ("2828 El Centro Rd", "W Capitol Ave", "Reno, NV", "exit 85", "a very long query with many words"):
        assert not name_like(q), q
    assert is_street(STREET) and not is_street(NINER)
