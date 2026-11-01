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


def test_a_pick_on_my_route_over_an_unpriced_list_is_a_stop_on_the_way(agent_sandbox, fake_http):
    """QA 2026-10-31, H9: with the route card and three other stops on screen the model answered pick
    (place_index=2) to "cheapest diesel on my route". A stop on the way is what was said; with no prices
    on the list the index means nothing, so the stop finder picks along the route."""
    prov = _prov(agent_sandbox, fake_http)
    agent_sandbox.llm.on(r"park", {"intent": "find", "category": "truck_stop"})
    agent_sandbox.llm.on(r"cheapest diesel on my route", {"intent": "pick", "place_index": 2})
    agent_sandbox.places_provider.places = [PARK, ROADYS]
    first = run(prov.handle(AgentRequest(text="where can I park", session_id="otw-pick", **SAC), None))
    res = run(prov.handle(AgentRequest(text="cheapest diesel on my route", session_id="otw-pick",
                                       active_place=first.place, **SAC), None))
    other = ROADYS if first.place.id == PARK.id else PARK
    assert res.status == "route_set" and res.place.id == first.place.id, res.reply  # destination kept
    assert [o.id for o in res.options] == [other.id] and res.reply.startswith(f"Added {other.name}")
    assert res.reply.endswith("I don't have a diesel price for it.")
    assert first.place.id not in agent._SESSIONS["otw-pick"].get("rejected", set())


def test_a_named_pick_on_the_way_stops_at_that_place(agent_sandbox, fake_http):
    prov = _prov(agent_sandbox, fake_http)
    agent_sandbox.llm.on(r"park", {"intent": "find", "category": "truck_stop"})
    agent_sandbox.llm.on(r"stop at", {"intent": "pick", "place_index": 2})
    agent_sandbox.places_provider.places = [PARK, ROADYS]
    first = run(prov.handle(AgentRequest(text="where can I park", session_id="otw-named", **SAC), None))
    shown = agent._SESSIONS["otw-named"]["places"]
    res = run(prov.handle(AgentRequest(text=f"stop at {shown[1].name} on the way", session_id="otw-named",
                                       active_place=first.place, **SAC), None))
    assert res.status == "route_set" and res.place.id == first.place.id
    assert [o.id for o in res.options] == [shown[1].id], res.reply
    assert all("geocode" not in str(c) for c in fake_http.calls)


def test_the_same_stop_twice_is_said_not_added(agent_sandbox, fake_http):
    prov = _prov(agent_sandbox, fake_http)
    agent_sandbox.llm.on(r"diesel", {"intent": "add_stop", "category": "truck_stop"})
    req = AgentRequest(text="add diesel on the way", session_id="dup", active_place=PARK, **SAC)
    first = run(prov.handle(req, None))
    assert [o.id for o in first.options] == [ROADYS.id]
    again = run(prov.handle(req, None))
    assert again.status == "chat" and again.reply == "Roady's Truck Stop is already one of your stops."
    assert [s.id for s in agent._SESSIONS["dup"]["via"]["stops"]] == [ROADYS.id]


def test_a_named_stop_from_the_list_is_not_geocoded(agent_sandbox, fake_http):
    """The model echoed "Pilot" as the query with no index (on the emulator "49er" went to HERE and came
    back as a street in Placerville). A listed place named by the driver is that place."""
    from services.agent import named_place
    prov = _prov(agent_sandbox, fake_http)
    pilot = Place(id="pilot", name="Pilot Travel Center #412", category="truck_stop", lat=38.62, lng=-121.52,
                  distance_mi=2.7, address="4601 W Capitol Ave, West Sacramento, CA")
    agent_sandbox.llm.on(r"park", {"intent": "find", "category": "truck_stop"})
    agent_sandbox.llm.on(r"stop at", {"intent": "add_stop", "category": "truck_stop", "query": "Pilot"})
    agent_sandbox.places_provider.places = [PARK, ROADYS, pilot]
    first = run(prov.handle(AgentRequest(text="where can I park", session_id="named-q", **SAC), None))
    assert first.place.id == pilot.id  # the closest; the named stop below is one of the others
    agent_sandbox.llm.on(r"swing by", {"intent": "add_stop", "category": "truck_stop", "query": "Roady's"})
    res = run(prov.handle(AgentRequest(text="swing by Roady's on the way", session_id="named-q",
                                       active_place=first.place, **SAC), None))
    assert res.status == "route_set" and [o.id for o in res.options] == [ROADYS.id], res.reply
    assert all("geocode" not in str(c) for c in fake_http.calls)
    # the town in the name is not enough to name the place; a word of the name is
    sac49 = Place(id="49er", name="Sacramento 49er Travel Plaza", category="truck_stop", lat=38.62, lng=-121.52,
                  address="2828 El Centro Rd, Sacramento, CA")
    assert named_place("Sacramento", [sac49]) is None
    assert named_place("the 49er", [sac49]) is sac49 and named_place("49er travel plaza", [sac49]) is sac49
    assert named_place("Roady's", [sac49, ROADYS]) is ROADYS and named_place("Love's", [sac49, ROADYS]) is None
    assert named_place("Roady's Truck Stops", [sac49, ROADYS]) is ROADYS  # the model's plural (emulator)


def test_go_to_a_named_listed_place_routes_there(agent_sandbox, fake_http):
    prov = _prov(agent_sandbox, fake_http)
    agent_sandbox.llm.on(r"park", {"intent": "find", "category": "truck_stop"})
    agent_sandbox.llm.on(r"take me to", {"intent": "go", "query": "Roady's"})
    agent_sandbox.places_provider.places = [PARK, ROADYS]
    first = run(prov.handle(AgentRequest(text="where can I park", session_id="go-named", **SAC), None))
    other = ROADYS if first.place.id != ROADYS.id else PARK
    agent_sandbox.llm.on(r"take me to", {"intent": "go", "query": other.name})
    res = run(prov.handle(AgentRequest(text=f"take me to {other.name}", session_id="go-named",
                                       active_place=first.place, **SAC), None))
    assert res.status == "route_set" and res.place.id == other.id, res.reply
    assert all("geocode" not in str(c) for c in fake_http.calls)


def test_the_place_named_in_the_text_beats_the_models_index(agent_sandbox, fake_http):
    """Second "stop at the 49er on the way" with the 49er already a stop: the model answered pick with the
    index of the next place (Roady's) on the emulator. The word the driver said wins."""
    from services.agent import named_in_text
    prov = _prov(agent_sandbox, fake_http)
    flying = Place(id="flyingj", name="Flying J Travel Center", category="truck_stop", lat=38.62, lng=-121.52,
                   distance_mi=9.0, address="3001 Hwy 99, Sacramento, CA")
    agent_sandbox.llm.on(r"park", {"intent": "find", "category": "truck_stop"})
    agent_sandbox.places_provider.places = [PARK, ROADYS, flying]
    first = run(prov.handle(AgentRequest(text="where can I park", session_id="said", **SAC), None))
    assert first.place.id == PARK.id
    shown = agent._SESSIONS["said"]["places"]
    wrong = next(i for i, p in enumerate(shown, 1) if p.id == ROADYS.id)
    agent_sandbox.llm.on(r"flying", {"intent": "pick", "place_index": wrong})
    res = run(prov.handle(AgentRequest(text="stop at the Flying J on the way", session_id="said",
                                       active_place=first.place, **SAC), None))
    assert res.status == "route_set" and [o.id for o in res.options] == [flying.id], res.reply
    again = run(prov.handle(AgentRequest(text="stop at the Flying J on the way", session_id="said",
                                         active_place=first.place, **SAC), None))
    assert again.status == "chat" and again.reply == "Flying J Travel Center is already one of your stops."
    # generic words and the town never name a place
    assert named_in_text("add a truck stop on the way", [flying, ROADYS]) is None
    assert named_in_text("stop in Sacramento", [flying]) is None
    assert named_in_text("swing by Roady's first", [flying, ROADYS]) is ROADYS


def test_a_stop_at_the_destination_itself_is_said_not_added(agent_sandbox, fake_http):
    """Going to the 49er, "stop at the 49er on the way": the model echoed the destination's name and address
    as the query and its geocode came back as a new place, which was then added as a stop (emulator)."""
    prov = _prov(agent_sandbox, fake_http)
    agent_sandbox.llm.on(r"stop at", {"intent": "add_stop", "category": "truck_stop",
                                      "query": "49er Travel Plaza, 2828 El Centro Rd, Sacramento"})
    agent_sandbox.geocode["El Centro"] = [Place(id="here-1", name="2828 El Centro Rd, Sacramento, CA", category="destination",
                                                lat=PARK.lat + 0.0003, lng=PARK.lng)]
    res = run(prov.handle(AgentRequest(text="stop at the 49er on the way", session_id="dest", active_place=PARK, **SAC), None))
    assert res.status == "chat" and res.reply == "That's where we're headed, 49er Travel Plaza.", res.reply
    assert "via" not in agent._SESSIONS["dest"]
