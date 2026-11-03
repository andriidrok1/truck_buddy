"""Eval 2026-11-02: "the one in Reno" had nothing to go on, the model saw places by name only."""
from models import AgentRequest, Place
from services.agent import LlmAgentProvider, _session, _town


def test_town_from_an_address():
    assert _town("3645 W Capitol Ave, West Sacramento, CA 95691, USA") == "West Sacramento, CA"
    assert _town("Reno, NV, USA") == "Reno, NV"
    assert _town("100 Pilot Rd, Fernley, NV") == "Fernley, NV"
    assert _town("Auburn, CA") == "Auburn, CA" and _town(None) == "" and _town("somewhere") == ""


def test_the_model_sees_each_places_town():
    sess = _session("town-test")
    sess["places"] = [Place(id="fj", name="Flying J Travel Center", category="truck_stop", lat=39.5, lng=-119.8,
                            address="Reno, NV, USA"),
                      Place(id="reno-ts", name="Reno Truck Stop", category="truck_stop", lat=39.5, lng=-119.8,
                            address="Reno, NV, USA")]
    ctx = LlmAgentProvider()._context(AgentRequest(text="t", lat=38.6, lng=-121.5, session_id="town-test"), sess)
    assert "Flying J Travel Center in Reno, NV (truck_stop)" in ctx
    assert "Reno Truck Stop (truck_stop)" in ctx  # the town already in the name is not said twice
