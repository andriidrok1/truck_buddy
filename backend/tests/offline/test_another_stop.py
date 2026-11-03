"""QA 2026-11-02 (UX): "add another truck stop on my route" with Love's already a stop answered "Love's Travel Stop
is already one of your stops" and searched nothing. The search for a stop of a kind leaves out the stops already
on the route; "already one of your stops" is only for the driver naming that very place again."""
import re

from conftest import run

from models import AgentRequest
from services import agent
from test_via import FOURTH, LOVES, RENO, SAC, _sandbox, _vias


def _req(text, sid):
    return AgentRequest(text=text, session_id=sid, active_place=RENO, **SAC)


def _with_loves(agent_sandbox, fake_http, sid, rule):
    prov = _sandbox(agent_sandbox, fake_http)
    agent_sandbox.llm.rules.insert(0, rule)
    agent_sandbox.places_provider.places = [LOVES, FOURTH]
    run(prov.handle(AgentRequest(text="take me to Reno", session_id=sid, **SAC), None))
    assert run(prov.handle(_req("swing by Love's", sid), None)).status == "route_set"
    return prov


def test_another_stop_of_the_kind_is_a_new_one(agent_sandbox, fake_http):
    # the model reads it three ways: the category alone, "truck stop" as the query, or the listed stop's number
    for sid, said in (("a1", {"intent": "add_stop", "category": "truck_stop"}),
                      ("a2", {"intent": "add_stop", "category": "truck_stop", "query": "truck stop"}),
                      ("a3", {"intent": "add_stop", "category": "truck_stop", "place_index": 1})):
        prov = _with_loves(agent_sandbox, fake_http, sid, (re.compile(r"another truck stop", re.I), said))
        res = run(prov.handle(_req("add another truck stop on my route", sid), None))
        assert res.status == "route_set", (sid, res.reply)
        assert res.reply.startswith("Added Flying J,"), (sid, res.reply)
        assert [o.id for o in res.options] == [LOVES.id, FOURTH.id], sid
        assert len(_vias(fake_http.calls_to("router.hereapi.com")[-1])) == 2


def test_no_other_one_ahead_is_said(agent_sandbox, fake_http):
    prov = _with_loves(agent_sandbox, fake_http, "a4",
                       (re.compile(r"another truck stop", re.I), {"intent": "add_stop", "category": "truck_stop"}))
    agent_sandbox.places_provider.places = [LOVES]
    res = run(prov.handle(_req("add another truck stop on my route", "a4"), None))
    assert res.status == "chat" and res.reply == "I couldn't find another truck stop ahead on your route."


def test_naming_the_stop_again_is_still_a_duplicate(agent_sandbox, fake_http):
    prov = _with_loves(agent_sandbox, fake_http, "a5",
                       (re.compile(r"another truck stop", re.I), {"intent": "add_stop", "category": "truck_stop"}))
    res = run(prov.handle(_req("swing by Love's", "a5"), None))
    assert res.status == "chat" and res.reply == agent.VIA_DUPLICATE.format(name="Love's Travel Stop")


def test_add_it_again_changes_nothing(agent_sandbox, fake_http):
    """Review 2026-11-02: "yeah add that one" just after Love's was added (the model's index on Love's) added Flying J,
    a stop the driver never asked for. Only "another", "a different one", "one more" look for a new one."""
    for sid, said in (("a6", "yeah add that one"), ("a7", "add it")):
        prov = _with_loves(agent_sandbox, fake_http, sid,
                           (re.compile(r"^(?:yeah add|add it)", re.I), {"intent": "add_stop", "place_index": 1}))
        before = len(fake_http.calls_to("router.hereapi.com"))
        res = run(prov.handle(_req(said, sid), None))
        assert res.status == "chat" and res.reply == agent.VIA_DUPLICATE.format(name="Love's Travel Stop"), res.reply
        assert len(fake_http.calls_to("router.hereapi.com")) == before


def test_one_more_is_another(agent_sandbox, fake_http):
    prov = _with_loves(agent_sandbox, fake_http, "a8",
                       (re.compile(r"one more", re.I), {"intent": "add_stop", "category": "truck_stop", "place_index": 1}))
    res = run(prov.handle(_req("add one more on the way", "a8"), None))
    assert res.status == "route_set" and res.reply.startswith("Added Flying J,"), res.reply
