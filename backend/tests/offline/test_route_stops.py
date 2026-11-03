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
