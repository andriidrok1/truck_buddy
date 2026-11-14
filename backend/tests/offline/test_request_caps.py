"""Free text in requests that reaches the model has a length cap (audit 2026-11-13)."""
import pytest

from models import AgentRequest, Place

SAC = {"lat": 38.5816, "lng": -121.4944}


def test_agent_text_has_a_cap():
    AgentRequest(text="a" * 2000, **SAC)
    with pytest.raises(Exception):
        AgentRequest(text="a" * 2001, **SAC)


def test_agent_rejects_a_huge_text(api_client, agent_sandbox):
    assert api_client.post("/api/agent", json={"text": "a" * 50_000, **SAC}).status_code == 422
    assert agent_sandbox.llm.calls == []


def test_text_the_app_sends_back_is_cut_not_refused():
    from models import ChainAhead, TruckProfile
    p = Place(id="p", name="N" * 5000, category="truck_stop", lat=1, lng=2, summary="S" * 9000, tags=["t" * 99] * 99,
              address="A" * 5000)
    assert len(p.name) == 300 and len(p.summary) == 1000 and len(p.tags) == 30 and len(p.tags[0]) == 60
    assert len(p.address) == 300
    assert len(TruckProfile(favorite_chains=["Love's" * 50]).favorite_chains[0]) == 40
    assert len(ChainAhead(route="I-80", location="L" * 5000, status="R-2", miles_ahead=3).location) == 300
