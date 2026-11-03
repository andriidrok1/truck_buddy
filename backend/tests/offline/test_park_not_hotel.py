"""The model sometimes files "where can I park tonight" under hotel (stand QA 2026-11-01). Parking words without
a bed word are a truck stop; a driver who asks for a room still gets the hotel."""
from conftest import run

from models import AgentRequest

SAC = {"lat": 38.5816, "lng": -121.4944}


def test_park_tonight_filed_as_hotel_becomes_a_truck_stop(agent_sandbox, fake_http):
    agent_sandbox.llm.on(r"park tonight", {"intent": "find", "category": "hotel"})
    agent_sandbox.llm.on(r"a room", {"intent": "find", "category": "hotel"})
    agent_sandbox.llm.on(r"shut down for the night", {"intent": "find", "category": "hotel"})
    prov = agent_sandbox.provider()
    res = run(prov.handle(AgentRequest(text="where can I park tonight", session_id="p1", **SAC), None))
    assert res.category == "truck_stop" and res.status in ("route_set", "no_results")
    res = run(prov.handle(AgentRequest(text="where can I shut down for the night", session_id="p2", **SAC), None))
    assert res.category == "truck_stop"
    res = run(prov.handle(AgentRequest(text="find me a hotel with a room and truck parking", session_id="p3", **SAC), None))
    assert res.category == "hotel"
