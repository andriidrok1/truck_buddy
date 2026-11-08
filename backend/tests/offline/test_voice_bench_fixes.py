"""Three wrong answers the synthetic voice run found on prod (2026-11-08): "find a Love's" routed to the nearest
truck stop of any brand, "find a CAT scale" said it can't (the category is gated by CATEGORIES_EXTRA, a config
fix), and "where am I" read the coordinates out loud."""
import json
import re

from conftest import fixture_json, run

from models import AgentRequest, Place
from services import places

DATA = fixture_json("here/route_sac_reno")
SAC = {"lat": 38.5816, "lng": -121.4944}
HARBOR = Place(id="h", name="Harbor Travel Plaza", category="truck_stop", lat=38.58, lng=-121.53, distance_mi=2.0)
LOVES = Place(id="l", name="Love's Travel Stop Lodi", category="truck_stop", lat=38.13, lng=-121.27, distance_mi=32.0)


def _prov(agent_sandbox, fake_http, places_):
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    agent_sandbox.places_provider.places = places_
    agent_sandbox.llm.rules.insert(0, (re.compile(r"love|pilot|truck stop", re.I), {"intent": "find", "category": "truck_stop"}))
    return agent_sandbox.provider()


def test_a_named_chain_wins_over_the_nearest_stop(agent_sandbox, fake_http):
    prov = _prov(agent_sandbox, fake_http, [HARBOR, LOVES])
    res = run(prov.handle(AgentRequest(text="find a Love's", session_id="c1", **SAC), None))
    assert res.status == "route_set" and res.place.id == "l"
    assert [p.id for p in res.options] == [] or all("Love" in p.name for p in [res.place, *res.options])


def test_no_such_chain_says_so_then_the_nearest_stop(agent_sandbox, fake_http):
    prov = _prov(agent_sandbox, fake_http, [HARBOR])
    res = run(prov.handle(AgentRequest(text="find a Pilot", session_id="c2", **SAC), None))
    assert res.status == "route_set" and res.place.id == "h"
    assert res.reply.startswith("I don't see a Pilot nearby. ")


def test_no_chain_named_keeps_the_nearest(agent_sandbox, fake_http):
    prov = _prov(agent_sandbox, fake_http, [HARBOR, LOVES])
    res = run(prov.handle(AgentRequest(text="where's the nearest truck stop", session_id="c3", **SAC), None))
    assert res.place.id == "h" and not res.reply.startswith("I don't see")


def test_own_places_name_search_reaches_past_the_plain_radius(tmp_path):
    rows = [{"id": f"s{i}", "name": f"Stop {i}", "category": "truck_stop", "lat": 38.58 + i * 0.01, "lng": -121.49,
             "tags": [], "address": None} for i in range(12)]
    rows.append({"id": "lodi", "name": "Love's Travel Stop Lodi", "category": "truck_stop", "lat": 38.13, "lng": -121.27,
                 "tags": [], "address": None})
    rows.append({"id": "far", "name": "Love's Travel Stop Tulare", "category": "truck_stop", "lat": 36.2, "lng": -119.3,
                 "tags": [], "address": None})
    (tmp_path / "truck_stop.json").write_text(json.dumps(rows))
    own = places.OwnPlacesProvider(tmp_path)
    plain = run(own.search("truck_stop", **SAC))
    assert "lodi" not in {p.id for p in plain.places}
    named = run(own.search_named("truck_stop", lambda n: "Love's" in n, **SAC))
    assert [p.id for p in named] == ["lodi"]  # Tulare is past places.NAMED_MAX_KM


def _where(agent_sandbox, fake_http, items):
    fake_http.add("revgeocode.search.hereapi.com", "/v1/revgeocode", json={"items": items})
    agent_sandbox.llm.rules.insert(0, (re.compile(r"where am i|where are we", re.I),
                                       {"intent": "chat", "reply": "You're near Sacramento, around 38.58160, -121.49440."}))
    return agent_sandbox.provider()


def test_where_am_i_says_road_and_town(agent_sandbox, fake_http):
    prov = _where(agent_sandbox, fake_http, [{"address": {"street": "I-80", "city": "West Sacramento", "state": "California"}}])
    res = run(prov.handle(AgentRequest(text="Where am I?", session_id="w1", **SAC), None))
    assert res.reply == "You're on I-80 in West Sacramento, California."


def test_where_are_we_without_a_street(agent_sandbox, fake_http):
    prov = _where(agent_sandbox, fake_http, [{"address": {"city": "Lodi", "state": "California"}}])
    res = run(prov.handle(AgentRequest(text="where are we", session_id="w2", **SAC), None))
    assert res.reply == "You're in Lodi, California."


def test_where_am_i_never_reads_coordinates_when_here_fails(agent_sandbox, fake_http):
    prov = _where(agent_sandbox, fake_http, [])
    res = run(prov.handle(AgentRequest(text="where am I", session_id="w3", **SAC), None))
    assert res.reply == "I can't tell where we are right now." and not re.search(r"\d+\.\d{3}", res.reply)


def test_where_am_i_going_is_not_a_location_question():
    from services.agent import WHERE_AM_I
    assert WHERE_AM_I.search("where am I")
    assert WHERE_AM_I.search("где я")
    assert not WHERE_AM_I.search("where am I going")
    assert not WHERE_AM_I.search("where am I parking tonight")
