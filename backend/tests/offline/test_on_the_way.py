"""From the 2026-10-28 driver QA: "cheapest diesel on my route" with a route set is a stop on the way (the
parking stays the destination), a price question without a price says so, and the spoken landmark is the
street the driver arrives on, the same one the entry tip on the card names."""
import json

from conftest import fixture_json, run

from models import AgentRequest, Approach, Place
from services import agent
from services.agent import arrival_street, price_caveat
from services.routing import _here_parse

DATA = fixture_json("here/route_sac_reno")
SAC = {"lat": 38.5816, "lng": -121.4944}
PARK = Place(id="g-49er", name="49er Travel Plaza", category="truck_stop", lat=38.60, lng=-121.55,
             address="2828 El Centro Rd, Sacramento, CA", distance_mi=5.6)
ROADYS = Place(id="g-roadys", name="Roady's Truck Stop", category="truck_stop", lat=38.57, lng=-121.56,
               address="1 W Capitol Ave, West Sacramento, CA", distance_mi=6.4)


def _prov(agent_sandbox, fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    agent_sandbox.llm.on(r"diesel|fuel", {"intent": "find", "category": "truck_stop"})
    agent_sandbox.places_provider.places = [ROADYS]
    return agent_sandbox.provider()


def test_fuel_on_my_route_keeps_the_destination(agent_sandbox, fake_http):
    prov = _prov(agent_sandbox, fake_http)
    req = AgentRequest(text="cheapest diesel on my route", session_id="otw", active_place=PARK, **SAC)
    res = run(prov.handle(req, None))
    assert res.status == "route_set" and res.place.id == PARK.id  # still going to the parking
    assert res.route.via == [[ROADYS.lat, ROADYS.lng]] and [o.id for o in res.options] == [ROADYS.id]
    assert res.reply == ("Added Roady's Truck Stop, about 6.4 miles ahead. New total 2 h 52 min, 132 miles. "
                         "I don't have a diesel price for it.")
    assert PARK.id not in agent._SESSIONS["otw"].get("rejected", set())  # not a "not that one"


def test_stop_query_that_is_not_a_place_falls_back_to_the_category(agent_sandbox, fake_http):
    """The model echoed the phrase as the query (seen on the emulator): the category search is what was meant."""
    prov = _prov(agent_sandbox, fake_http)
    agent_sandbox.llm.on(r"diesel", {"intent": "add_stop", "category": "truck_stop", "query": "cheapest diesel on my route"})
    req = AgentRequest(text="cheapest diesel on my route", session_id="echo", active_place=PARK, **SAC)
    res = run(prov.handle(req, None))
    assert res.status == "route_set" and res.place.id == PARK.id and res.route.via == [[ROADYS.lat, ROADYS.lng]]
    assert res.reply.startswith("Added Roady's Truck Stop, about 6.4 miles ahead.")
    # a real name that doesn't geocode is still "can't find it", with or without a category
    agent_sandbox.llm.on(r"narnia", {"intent": "add_stop", "category": None, "query": "Narnia"})
    res = run(prov.handle(AgentRequest(text="stop at Narnia", session_id="echo2", active_place=PARK, **SAC), None))
    assert res.status == "chat" and res.reply == "I heard Narnia, but can't find it. Say it again or type it."


def test_plain_find_with_a_route_still_picks_a_new_place(agent_sandbox, fake_http):
    prov = _prov(agent_sandbox, fake_http)
    res = run(prov.handle(AgentRequest(text="find me diesel", session_id="plain", active_place=PARK, **SAC), None))
    assert res.status == "route_set" and res.place.id == ROADYS.id
    assert "diesel price" not in res.reply  # no price question, no caveat


def test_price_question_without_a_price_says_so(agent_sandbox, fake_http):
    prov = _prov(agent_sandbox, fake_http)
    res = run(prov.handle(AgentRequest(text="where's the cheapest diesel", session_id="price", **SAC), None))
    assert res.status == "route_set" and res.reply.endswith(" I don't have a diesel price for it.")
    priced = ROADYS.model_copy(update={"diesel_price": 4.19, "diesel_kind": "truck"})
    assert price_caveat("cheapest diesel", priced) == "" and price_caveat("cheapest diesel", None) == ""
    assert price_caveat("how much is diesel there", ROADYS) == agent.NO_PRICE
    assert price_caveat("find me diesel", ROADYS) == ""
    assert price_caveat("cheapest", ROADYS.model_copy(update={"category": "rest_area"})) == ""


def test_on_the_way_phrases():
    on = agent.ON_THE_WAY.search
    assert on("cheapest diesel on my route") and on("fuel along the way") and on("a rest area before I get there")
    assert on("diesel en route") and on("truck stop on the way")
    assert not on("find me diesel") and not on("route me to Reno") and not on("take the highway")


def test_spoken_landmark_is_the_arrival_street():
    route = _here_parse(DATA)
    route.approach = Approach(bearing=167, compass="north", street="N Virginia St (US-395-BR)")
    assert arrival_street(ROADYS, route) == "N Virginia St"
    route.approach = Approach(bearing=167, compass="north", street=None)
    assert arrival_street(ROADYS, route) == "W Capitol Ave"
    route.approach = None
    assert arrival_street(ROADYS, route) == "W Capitol Ave"
    assert arrival_street(ROADYS.model_copy(update={"address": None}), route) == ""
