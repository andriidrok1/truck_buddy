"""'Take me to X': a geocoded place is named with where it is, and a far or differently named match is asked
about before the route is built (stand 2026-11-01: Payson, AZ became a street in Phoenix and a business in Cave Creek)."""
from conftest import fixture_json, run

from models import AgentRequest, Place
from services import agent, timing
from services.agent import dest_check, spoken_where

DATA = fixture_json("here/route_sac_reno")
FH = {"lat": 33.6117, "lng": -111.7174}  # Fountain Hills, AZ
SAC = {"lat": 38.5816, "lng": -121.4944}

TOWN = Place(id="here-town-payson", name="Payson, AZ, United States", category="destination", lat=34.2309, lng=-111.3251,
             address="United States", distance_mi=48.0)
STREET = Place(id="here-street-payson", name="Payson, Phoenix, AZ 85086, United States", category="destination",
               lat=33.84, lng=-112.11, distance_mi=40.0)
BUSINESS = Place(id="here-cc", name="Cave Creek, Az", category="destination", lat=33.62, lng=-111.90,
                 address="15333 N Pima Rd, Scottsdale, AZ 85260-2637, United States", distance_mi=62.0)
HAMLET = Place(id="here-town-pilot", name="Pilot, Wells, NV, United States", category="destination", lat=40.82, lng=-114.24,
               address="United States", distance_mi=300.0)
RENO = Place(id="here-reno", name="Reno, NV", category="destination", lat=39.5296, lng=-119.8138)
HOUSE = Place(id="here-h", name="2828 El Centro Rd, Sacramento, CA 95833, United States", category="destination",
              lat=38.6, lng=-121.5, distance_mi=3.0)
PILOT_BIZ = Place(id="here-p", name="Pilot Travel Center #123", category="destination", lat=38.65, lng=-121.38,
                  address="1001 Truck Stop Way, Sacramento, CA 95814, United States", distance_mi=6.0)


def test_spoken_where_forms():
    assert spoken_where(TOWN) == "Payson, Arizona"
    assert spoken_where(STREET) == "Payson in Phoenix, Arizona"
    assert spoken_where(BUSINESS) == "Cave Creek on North Pima Road in Scottsdale, Arizona"
    assert spoken_where(PILOT_BIZ) == "Pilot Travel Center on Truck Stop Way in Sacramento, California"
    assert spoken_where(RENO) == "Reno"
    assert spoken_where(HOUSE) == "2828 El Centro Road in Sacramento, California"
    assert spoken_where(HAMLET) == "Pilot, Nevada"


def test_dest_check_rules():
    assert dest_check("Payson, Arizona", STREET) == "town_to_other"
    assert dest_check("Payson, AZ", BUSINESS) == "town_to_other"
    assert dest_check("Pilot", HAMLET) == "chain_to_town"
    assert dest_check("Payson", BUSINESS) == "far_other_name"
    # plainly what was said: no question
    assert dest_check("Reno", RENO) is None and dest_check("Reno, NV", RENO) is None
    assert dest_check("Payson", STREET) is None  # near, same letters: spoken in the reply instead
    assert dest_check("Payson, AZ", TOWN) is None  # the town itself, 48 miles out
    assert dest_check("Pilot", PILOT_BIZ) is None


def _sandbox(agent_sandbox, fake_http, found):
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    for phrase in ("Reno", "Payson, Arizona", "Payson, AZ", "Pilot", "Payson"):
        agent_sandbox.llm.on(rf"take me to {phrase}$", {"intent": "go", "query": phrase})
    agent_sandbox.geocode.update(found)
    return agent_sandbox.provider()


def _req(text, sid, at=FH, **kw):
    return AgentRequest(text=text, session_id=sid, **at, **kw)


def test_a_matching_town_is_routed_without_a_question(agent_sandbox, fake_http):
    prov = _sandbox(agent_sandbox, fake_http, {"reno": [RENO]})
    res = run(prov.handle(_req("take me to Reno", "dc0", SAC), None))
    assert res.status == "route_set" and res.reply.startswith("Alright. Route to Reno is set")
    assert "pending" not in agent._SESSIONS["dc0"] and len(fake_http.calls_to("router.hereapi.com")) == 1


def test_a_town_with_state_that_became_a_street_is_asked_first(agent_sandbox, fake_http, monkeypatch):
    prov = _sandbox(agent_sandbox, fake_http, {"payson": [STREET]})
    calls = []
    real = agent.geocode

    async def counting(q, lat, lng):
        calls.append(q)
        return await real(q, lat, lng)
    monkeypatch.setattr(agent, "geocode", counting)
    timing.begin()
    res = run(prov.handle(_req("take me to Payson, Arizona", "dc1"), None))
    assert res.status == "confirm" and res.place is None and res.route is None
    assert res.reply == "I found Payson in Phoenix, Arizona, about 40 miles away. Is that the one?"
    assert fake_http.calls_to("router.hereapi.com") == [] and "city" not in agent._SESSIONS["dc1"]
    assert timing.notes()["dest_check"] == "asked"
    timing.begin()
    res = run(prov.handle(_req("yes", "dc1"), None))
    assert res.status == "route_set" and res.reply.startswith("Alright. Route to Payson in Phoenix, Arizona is set")
    assert len(calls) == 1 and len(fake_http.calls_to("router.hereapi.com")) == 1
    assert agent._SESSIONS["dc1"].get("city") == "Payson, Arizona"
    assert timing.notes()["dest_check"] == "accepted"


def test_a_far_business_with_another_name_is_asked_and_no_declines(agent_sandbox, fake_http):
    prov = _sandbox(agent_sandbox, fake_http, {"payson": [BUSINESS]})
    res = run(prov.handle(_req("take me to Payson, AZ", "dc2"), None))
    assert res.status == "confirm"
    assert res.reply == "I found Cave Creek on North Pima Road in Scottsdale, Arizona, about 62 miles away. Is that the one?"
    timing.begin()
    res = run(prov.handle(_req("no", "dc2"), None))
    assert res.status == "chat" and res.reply == agent.DEST_NO and "pending" not in agent._SESSIONS["dc2"]
    assert timing.notes()["dest_check"] == "declined" and "city" not in agent._SESSIONS["dc2"]
    res = run(prov.handle(_req("take me to Payson, AZ", "dc2"), None))
    assert res.status == "chat" and res.reply == "That's the only match I have for Payson, AZ. Try the town with its state, or type the address."
    assert fake_http.calls_to("router.hereapi.com") == []


def test_a_chain_name_that_became_a_hamlet_is_asked_first(agent_sandbox, fake_http):
    prov = _sandbox(agent_sandbox, fake_http, {"pilot": [HAMLET]})
    res = run(prov.handle(_req("take me to Pilot", "dc3", SAC), None))
    assert res.status == "confirm" and res.reply == "I found Pilot, Nevada, about 300 miles away. Is that the one?"


def test_a_near_street_is_routed_with_its_place_spoken(agent_sandbox, fake_http):
    prov = _sandbox(agent_sandbox, fake_http, {"payson": [STREET]})
    res = run(prov.handle(_req("take me to Payson", "dc4"), None))
    assert res.status == "route_set" and "Payson in Phoenix, Arizona" in res.reply


def test_after_an_stt_question_the_destination_is_spoken_not_asked(agent_sandbox, fake_http, monkeypatch):
    prov = _sandbox(agent_sandbox, fake_http, {"payson": [STREET]})
    monkeypatch.setattr(agent, "stt_confirm_below", lambda: 0.9)
    res = run(prov.handle(_req("take me to Payson, Arizona", "dc5", stt_confidence=0.3), None))
    assert res.status == "confirm" and res.reply.startswith("Did you say")
    timing.begin()
    res = run(prov.handle(_req("yes", "dc5"), None))
    assert res.status == "route_set" and "Payson in Phoenix, Arizona" in res.reply
    assert timing.notes()["dest_check"] == "spoken"
