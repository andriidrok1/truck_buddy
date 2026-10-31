"""QA 2026-10-30, scenario H4, played back with the classifier answers seen live: with a route active,
"not that one" / "something else" came back as `alternative` (another road to the same place) and "the
second one" as `go` with the phrase as the query, which HERE geocoded to The Dalles, OR, 598 miles out.
The guard in front of the intent switch turns both into picks; real route questions still get alternatives."""
from conftest import fixture_json, run

from models import AgentRequest, Place
from services import agent
from services.agent import list_reference, reject_or_reference_guard

DATA = fixture_json("here/route_sac_reno")
SAC = {"lat": 38.5816, "lng": -121.4944}


def _p(id, name, mi):
    return Place(id=id, name=name, category="truck_stop", lat=38.58, lng=-121.5 - mi / 100, distance_mi=mi,
                 address=f"{int(mi * 100)} W Capitol Ave, West Sacramento, CA")


ROADYS, WESTSAC, FORTYNINER, SACVALLEY = _p("roadys", "Roady's Truck Stop", 4.2), _p("westsac", "West Sac Truck Stop", 1.2), \
    _p("49er", "Sacramento 49er Travel Plaza", 2.7), _p("sacvalley", "Sac Valley Truck Stop", 8.7)
OREGON = Place(id="here-dalles", name="W 21st Pl, The Dalles, OR 97058, United States", category="destination",
               lat=45.6, lng=-121.18)


def _sandbox(agent_sandbox, fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    agent_sandbox.places_provider.places = [ROADYS, WESTSAC, FORTYNINER, SACVALLEY]
    llm = agent_sandbox.llm
    llm.on(r"park tonight", {"intent": "find", "category": "truck_stop"})
    # exactly what gpt-5.4-mini answered on the emulator
    llm.on(r"^not that one$", {"intent": "alternative", "place_index": 1})
    llm.on(r"^something else$", {"intent": "alternative"})
    llm.on(r"^the second one$", {"intent": "go", "query": "the second one", "place_index": 2})
    llm.on(r"another way|faster way", {"intent": "alternative"})
    llm.on(r"^take me to that$", {"intent": "go", "query": "that"})
    agent_sandbox.geocode["second"] = [OREGON]  # HERE really did this
    return agent_sandbox.provider()


def _req(text, active=None, sid="h4"):
    return AgentRequest(text=text, session_id=sid, active_place=active, **SAC)


def test_rejections_change_the_place_not_the_road(agent_sandbox, fake_http):
    prov = _sandbox(agent_sandbox, fake_http)
    first = run(prov.handle(_req("where can I park tonight"), None))
    assert first.status == "route_set" and first.place.id == WESTSAC.id  # the closest one

    second = run(prov.handle(_req("not that one", active=first.place), None))
    assert second.status == "route_set" and second.place.id != first.place.id, second.reply
    assert not second.reply.startswith("Here's another way"), second.reply
    assert first.place.id in agent._SESSIONS["h4"]["rejected"]

    third = run(prov.handle(_req("something else", active=second.place), None))
    assert third.status == "route_set" and third.place.id not in (first.place.id, second.place.id), third.reply
    assert not third.reply.startswith("Here's another way")
    assert {first.place.id, second.place.id} <= agent._SESSIONS["h4"]["rejected"]


def test_the_second_one_is_a_pick_never_a_geocode(agent_sandbox, fake_http):
    prov = _sandbox(agent_sandbox, fake_http)
    first = run(prov.handle(_req("where can I park tonight", sid="h4b"), None))
    shown = [p for p in agent._SESSIONS["h4b"]["places"] if p.id != first.place.id]
    res = run(prov.handle(_req("the second one", active=first.place, sid="h4b"), None))
    assert res.status == "route_set" and res.place.id == shown[1].id, res.reply
    assert "W 21st Pl" not in res.reply and "598" not in res.reply
    assert all("geocode" not in str(c) for c in fake_http.calls)


def test_real_route_questions_still_get_an_alternative(agent_sandbox, fake_http):
    prov = _sandbox(agent_sandbox, fake_http)
    first = run(prov.handle(_req("where can I park tonight", sid="h4c"), None))
    res = run(prov.handle(_req("is there another way", active=first.place, sid="h4c"), None))
    assert res.status == "route_set" and res.place.id == first.place.id and res.reply.startswith("Here's another way")
    res = run(prov.handle(_req("is there a faster way", active=res.place, sid="h4c"), None))
    assert res.place.id == first.place.id


def test_go_with_a_pronoun_is_not_geocoded(agent_sandbox, fake_http):
    prov = _sandbox(agent_sandbox, fake_http)
    agent_sandbox.geocode["that"] = [OREGON]
    res = run(prov.handle(_req("take me to that", sid="h4d"), None))
    assert res.status == "not_understood"
    first = run(prov.handle(_req("where can I park tonight", sid="h4e"), None))
    res = run(prov.handle(_req("take me to that", active=first.place, sid="h4e"), None))
    assert res.status == "route_set" and res.place.id != OREGON.id  # a pick of the next place, not Oregon


def test_list_reference_counts_the_places_the_driver_sees():
    places = [ROADYS, WESTSAC, FORTYNINER, SACVALLEY]
    cur = ROADYS.id
    assert list_reference("the second one", places, cur) == 3  # 2nd of the shown = 49er = index 3 overall
    assert list_reference("the first one", places, cur) == 2
    assert list_reference("number two", places, cur) == 3
    assert list_reference("the last one", places, cur) == 4
    assert list_reference("the closest one", places, cur) == 2  # West Sac, 1.2 mi
    assert list_reference("the fifth one", places, cur) is None  # only three shown
    assert list_reference("Reno, NV", places, cur) is None and list_reference("2828 El Centro Rd", places, cur) is None
    assert list_reference("the second one", [], cur) is None


def test_nothing_else_is_goodbye_not_a_pick():
    places = [ROADYS, WESTSAC]
    for said in ("nothing else", "No, nothing else.", "that's all", "I'm good", "all set", "no thanks"):
        assert reject_or_reference_guard("pick", {"intent": "pick"}, said, places, ROADYS)[0] == "bye", said
    assert reject_or_reference_guard("pick", {"intent": "pick"}, "something else", places, ROADYS)[0] == "pick"


def test_guard_leaves_other_intents_alone():
    places = [ROADYS, WESTSAC]
    assert reject_or_reference_guard("find", {"intent": "find", "category": "hotel"}, "find me a hotel", places, ROADYS) == \
        ("find", {"intent": "find", "category": "hotel"})
    assert reject_or_reference_guard("alternative", {"intent": "alternative"}, "not that road", places, ROADYS)[0] == "alternative"
    assert reject_or_reference_guard("alternative", {"intent": "alternative"}, "not that one", [], ROADYS)[0] == "alternative"
    assert reject_or_reference_guard("go", {"intent": "go", "query": "Reno, NV"}, "take me to Reno", places, ROADYS)[0] == "go"
    three = [ROADYS, WESTSAC, FORTYNINER]
    assert reject_or_reference_guard("go", {"intent": "go", "query": "the second one"}, "the second one", three, ROADYS) == \
        ("pick", {"intent": "pick", "query": "the second one", "place_index": 3})  # 2nd of the two shown
    # only one other place shown: "the second one" points past the list -> a plain "next place" pick
    assert reject_or_reference_guard("go", {"intent": "go", "query": "the second one"}, "the second one", places, ROADYS) == \
        ("pick", {"intent": "pick", "query": "the second one", "place_index": None})
