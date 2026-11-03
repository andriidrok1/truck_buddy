"""The trip lives on the phone (plan 2026-11-02, track 3): every copilot turn carries the stops still ahead as
places (route_stops), and those are the truth for the active route's stops. A dialog that starts after an app
restart knows nothing of them on its own; before this it answered "there are no stops on this route" with two
stops on the map, and could neither drop one by name nor add one between them. An older app sends no
route_stops and keeps the dialog's own copy, as before."""
import re

import pytest
from conftest import run
from pydantic import ValidationError

from models import AgentRequest
from services import agent
from test_via import LOVES, PILOT, RENO, SAC, TA, _sandbox, _vias


def _fresh(text, sid, stops):
    return AgentRequest(text=text, session_id=sid, active_place=RENO, route_stops=stops, **SAC)


def _drop_rules(agent_sandbox):
    agent_sandbox.llm.rules.insert(0, (re.compile(r"skip the pilot", re.I),
                                       {"intent": "clear_stops", "query": "Pilot"}))


def test_route_stops_is_optional_and_bounded():
    assert AgentRequest(text="hi", **SAC).route_stops == []
    with pytest.raises(ValidationError):
        AgentRequest(text="hi", route_stops=[LOVES] * 11, **SAC)
    # a broken stop from the app is left out, never a failed turn
    req = AgentRequest(text="hi", route_stops=[LOVES.model_dump(), {"id": "x"}, "junk"], **SAC)
    assert [s.id for s in req.route_stops] == [LOVES.id]


def test_a_fresh_dialog_drops_one_stop_by_name(agent_sandbox, fake_http):
    prov = _sandbox(agent_sandbox, fake_http)
    _drop_rules(agent_sandbox)
    res = run(prov.handle(_fresh("skip the Pilot", "rs1", [LOVES, PILOT]), None))
    assert res.status == "route_set", res.reply
    assert res.reply.startswith("Okay, no stop at Pilot Travel Center. New total"), res.reply
    assert [o.id for o in res.options] == [LOVES.id]
    assert _vias(fake_http.calls_to("router.hereapi.com")[-1]) == [f"{LOVES.lat},{LOVES.lng}!stopDuration=1800"]


def test_dropping_the_only_stop_goes_straight_there(agent_sandbox, fake_http):
    prov = _sandbox(agent_sandbox, fake_http)
    _drop_rules(agent_sandbox)
    res = run(prov.handle(_fresh("skip the Pilot", "rs2", [PILOT]), None))
    assert res.status == "route_set" and res.reply.startswith("Okay, no stop at Pilot Travel Center. Straight there")
    assert _vias(fake_http.calls_to("router.hereapi.com")[-1]) == []


def test_a_stop_by_name_is_dropped_even_when_the_model_reads_a_pick(agent_sandbox, fake_http):
    prov = _sandbox(agent_sandbox, fake_http)
    agent_sandbox.llm.rules.insert(0, (re.compile(r"drop the pilot", re.I),
                                       {"intent": "pick", "place_index": None}))
    res = run(prov.handle(_fresh("drop the Pilot stop", "rs3", [LOVES, PILOT]), None))
    assert res.status == "route_set" and [o.id for o in res.options] == [LOVES.id], res.reply


def test_naming_a_place_that_is_not_a_stop_keeps_the_stops(agent_sandbox, fake_http):
    prov = _sandbox(agent_sandbox, fake_http)
    _drop_rules(agent_sandbox)
    res = run(prov.handle(_fresh("skip the Pilot", "rs4", [LOVES]), None))
    assert res.status == "chat" and res.reply == agent.VIA_NOT_A_STOP.format(name="Pilot"), res.reply
    assert fake_http.calls_to("router.hereapi.com") == []


def test_a_fresh_dialog_adds_a_stop_among_the_apps_in_route_order(agent_sandbox, fake_http):
    prov = _sandbox(agent_sandbox, fake_http)
    res = run(prov.handle(_fresh("swing by Love's", "rs5", [PILOT, TA]), None))
    assert res.status == "route_set", res.reply
    assert [o.id for o in res.options] == [LOVES.id, PILOT.id, TA.id]
    assert [v.split("!")[0] for v in _vias(fake_http.calls_to("router.hereapi.com")[-1])] == \
        [f"{s.lat},{s.lng}" for s in (LOVES, PILOT, TA)]


def test_a_fresh_dialog_knows_a_stop_is_already_on_the_route(agent_sandbox, fake_http):
    prov = _sandbox(agent_sandbox, fake_http)
    res = run(prov.handle(_fresh("swing by Love's", "rs6", [LOVES]), None))
    assert res.status == "chat" and res.reply == agent.VIA_DUPLICATE.format(name="Love's Travel Stop")


def test_a_fresh_dialog_clears_the_apps_stops(agent_sandbox, fake_http):
    prov = _sandbox(agent_sandbox, fake_http)
    res = run(prov.handle(_fresh("clear the stops", "rs7", [LOVES, PILOT]), None))
    assert res.status == "route_set" and res.reply.startswith("Stops cleared."), res.reply
    assert _vias(fake_http.calls_to("router.hereapi.com")[-1]) == []


def test_the_model_is_told_the_apps_stops(agent_sandbox, fake_http):
    prov = _sandbox(agent_sandbox, fake_http)
    agent_sandbox.llm.on(r"one of my stops", {"intent": "chat", "reply": "Yes, it is."})
    run(prov.handle(_fresh("is the Pilot one of my stops", "rs8", [LOVES, PILOT]), None))
    assert "Stops on the way: Love's Travel Stop, Pilot Travel Center." in agent_sandbox.llm.calls[-1][1]


def test_the_apps_stops_beat_the_dialogs_copy(agent_sandbox, fake_http):
    # the dialog added Love's; the app has since dropped it (the driver passed it, or ended the stop on screen)
    prov = _sandbox(agent_sandbox, fake_http)
    run(prov.handle(AgentRequest(text="take me to Reno", session_id="rs9", **SAC), None))
    assert run(prov.handle(AgentRequest(text="swing by Love's", session_id="rs9", active_place=RENO, **SAC),
                           None)).status == "route_set"
    res = run(prov.handle(_fresh("clear the stops", "rs9", []), None))
    assert res.status == "chat" and res.reply == agent.VIA_NONE
    res = run(prov.handle(_fresh("stop at the Pilot in Fernley", "rs9", []), None))
    assert [o.id for o in res.options] == [PILOT.id], res.reply


def test_an_old_app_keeps_the_dialogs_stops(agent_sandbox, fake_http):
    prov = _sandbox(agent_sandbox, fake_http)
    run(prov.handle(AgentRequest(text="take me to Reno", session_id="rs10", **SAC), None))
    run(prov.handle(AgentRequest(text="swing by Love's", session_id="rs10", active_place=RENO, **SAC), None))
    res = run(prov.handle(AgentRequest(text="stop at the Pilot in Fernley", session_id="rs10", active_place=RENO,
                                       **SAC), None))
    assert [o.id for o in res.options] == [LOVES.id, PILOT.id], res.reply


def test_the_destinations_town_does_not_name_a_stop(agent_sandbox, fake_http):
    prov = _sandbox(agent_sandbox, fake_http)
    petro = PILOT.model_copy(update={"id": "g-petro-reno", "name": "Petro Stopping Center, Reno", "address": None})
    res = run(prov.handle(_fresh("no stops, straight to Reno", "rs11", [LOVES, petro]), None))
    assert res.status == "route_set" and res.reply.startswith("Stops cleared."), res.reply


# Review 2026-11-02: drop/take off/cancel anywhere in the sentence plus any word of a stop's name dropped that stop,
# whatever the model made of it. Only an order about the stop itself leaves it out.
@pytest.mark.parametrize("text", [
    "can I drop my trailer at the Pilot",
    "what time should I take off from the Pilot",
    "drop the load in Fernley",
    "take off from the Pilot at 6",
    "skip breakfast, the Pilot has food",
])
def test_talk_with_a_drop_word_keeps_the_stops(agent_sandbox, fake_http, text):
    prov = _sandbox(agent_sandbox, fake_http)
    agent_sandbox.llm.rules.insert(0, (re.compile(re.escape(text), re.I), {"intent": "chat", "reply": "Sure."}))
    res = run(prov.handle(_fresh(text, "rs12", [LOVES, PILOT]), None))
    assert res.status == "chat" and res.reply == "Sure.", res.reply
    assert fake_http.calls_to("router.hereapi.com") == []


@pytest.mark.parametrize("text", [
    "skip the Pilot",
    "please drop the stop at the Pilot",
    "cancel the Fernley stop",
    "take the Pilot off",
    "let's take off the Pilot",
    "okay, can you remove the Pilot",
])
def test_an_order_about_the_stop_drops_it_whatever_the_model_read(agent_sandbox, fake_http, text):
    prov = _sandbox(agent_sandbox, fake_http)
    agent_sandbox.llm.rules.insert(0, (re.compile(re.escape(text), re.I), {"intent": "pick", "place_index": None}))
    res = run(prov.handle(_fresh(text, "rs13", [LOVES, PILOT]), None))
    assert res.status == "route_set" and [o.id for o in res.options] == [LOVES.id], res.reply


# Review 2026-11-02: the model's query for clear_stops is often not a stop's name ("truck stop", "my stops", "the
# last stop"), and every one of those got "X isn't one of your stops".
REST = LOVES.model_copy(update={"id": "g-rest-donner", "name": "Donner Summit Rest Area", "category": "rest_area",
                                "address": "I-80, CA", "distance_mi": 60.0})


def _skip(agent_sandbox, fake_http, query, stops, sid, text=None):
    prov = _sandbox(agent_sandbox, fake_http)
    agent_sandbox.llm.rules.insert(0, (re.compile(r"^skip|^no stops", re.I), {"intent": "clear_stops", "query": query}))
    return run(prov.handle(_fresh(text or "skip the " + query, sid, stops), None))


@pytest.mark.parametrize("query", ["my stops", "both stops", "the stops", "all", "straight there"])
def test_all_of_the_stops(agent_sandbox, fake_http, query):
    res = _skip(agent_sandbox, fake_http, query, [LOVES, PILOT], "rs20")
    assert res.status == "route_set" and res.reply.startswith("Stops cleared."), res.reply


def test_no_stops_straight_to_the_destination_with_its_name_as_the_query(agent_sandbox, fake_http):
    res = _skip(agent_sandbox, fake_http, "Reno", [LOVES, PILOT], "rs21", text="no stops, straight to Reno")
    assert res.status == "route_set" and res.reply.startswith("Stops cleared."), res.reply


def test_the_destination_is_not_a_stop(agent_sandbox, fake_http):
    res = _skip(agent_sandbox, fake_http, "Reno", [LOVES, PILOT], "rs22", text="skip Reno")
    assert res.status == "chat" and res.reply == agent.VIA_DEST_NOT_A_STOP.format(name="Reno"), res.reply
    assert fake_http.calls_to("router.hereapi.com") == []


@pytest.mark.parametrize("query, dropped", [("the last stop", LOVES), ("the first stop", PILOT)])
def test_a_stop_by_its_order(agent_sandbox, fake_http, query, dropped):
    stops = [PILOT, LOVES]
    res = _skip(agent_sandbox, fake_http, query, stops, "rs23")
    assert res.status == "route_set" and [o.id for o in res.options] == [s.id for s in stops if s is not dropped], res.reply


@pytest.mark.parametrize("query", ["truck stop", "fuel stop"])
def test_a_stop_by_its_kind(agent_sandbox, fake_http, query):
    res = _skip(agent_sandbox, fake_http, query, [LOVES, REST], "rs24")
    assert res.status == "route_set" and [o.id for o in res.options] == [REST.id], res.reply


def test_two_stops_of_the_kind_ask_which(agent_sandbox, fake_http):
    res = _skip(agent_sandbox, fake_http, "truck stop", [LOVES, PILOT], "rs25")
    assert res.status == "chat" and res.reply == "Which one, Love's Travel Stop or Pilot Travel Center?", res.reply
    assert fake_http.calls_to("router.hereapi.com") == []


def test_that_stop_is_the_one_just_added(agent_sandbox, fake_http):
    prov = _sandbox(agent_sandbox, fake_http)
    run(prov.handle(AgentRequest(text="take me to Reno", session_id="rs26", **SAC), None))
    run(prov.handle(AgentRequest(text="stop at the Pilot in Fernley", session_id="rs26", active_place=RENO, **SAC), None))
    added = run(prov.handle(AgentRequest(text="swing by Love's", session_id="rs26", active_place=RENO, **SAC), None))
    assert {o.id for o in added.options} == {LOVES.id, PILOT.id}
    agent_sandbox.llm.rules.insert(0, (re.compile(r"^skip", re.I), {"intent": "clear_stops", "query": "that stop"}))
    res = run(prov.handle(AgentRequest(text="skip that stop", session_id="rs26", active_place=RENO, **SAC), None))
    assert res.status == "route_set" and [o.id for o in res.options] == [PILOT.id], res.reply


def test_a_name_that_matches_nothing_is_still_said(agent_sandbox, fake_http):
    res = _skip(agent_sandbox, fake_http, "Narnia", [LOVES, PILOT], "rs27")
    assert res.status == "chat" and res.reply == agent.VIA_NOT_A_STOP.format(name="Narnia"), res.reply


@pytest.mark.parametrize("text, kept", [("skip the truck stop", [REST]), ("skip the last stop", [LOVES]),
                                        ("drop the rest area", [LOVES])])
def test_the_drivers_words_decide_when_the_model_sent_no_query(agent_sandbox, fake_http, text, kept):
    # A clear_stops with no query used to clear every stop: "skip the truck stop" dropped the rest area too.
    prov = _sandbox(agent_sandbox, fake_http)
    agent_sandbox.llm.rules.insert(0, (re.compile(r"^(skip|drop)", re.I), {"intent": "clear_stops"}))
    res = run(prov.handle(_fresh(text, "rs30", [LOVES, REST]), None))
    assert res.status == "route_set" and [o.id for o in res.options] == [s.id for s in kept], res.reply


def test_no_query_and_all_the_stops_still_clears_them(agent_sandbox, fake_http):
    prov = _sandbox(agent_sandbox, fake_http)
    agent_sandbox.llm.rules.insert(0, (re.compile(r"^no stops", re.I), {"intent": "clear_stops"}))
    res = run(prov.handle(_fresh("no stops, straight there", "rs31", [LOVES, REST]), None))
    assert res.status == "route_set" and res.reply.startswith("Stops cleared."), res.reply


@pytest.mark.parametrize("text", ["skip it", "skip that", "skip that one"])
def test_skip_it_without_a_query_is_not_a_place_named_it(agent_sandbox, fake_http, text):
    prov = _sandbox(agent_sandbox, fake_http)
    agent_sandbox.llm.rules.insert(0, (re.compile(r"^skip", re.I), {"intent": "clear_stops"}))
    res = run(prov.handle(_fresh(text, "rs32", [LOVES]), None))
    assert res.status == "route_set" and res.options in ([], None), res.reply
