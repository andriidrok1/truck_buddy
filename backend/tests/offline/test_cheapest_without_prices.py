"""QA 2026-10-31, scenario H8: with a route to Roady's and three other stops on the list, "where's the
cheapest diesel" came back as a pick (the eval wants that) and the destination moved to the 49er, although
no place on the list had a diesel price. Without prices there is nothing to pick on: the current stop
stays, or, with no current stop, the closest one is routed with the usual "no price" caveat."""
import re

from conftest import fixture_json, run

from models import AgentRequest, Place
from services import agent
from services.agent import NO_PRICE, NO_PRICES_KEEP

DATA = fixture_json("here/route_sac_reno")
SAC = {"lat": 38.5816, "lng": -121.4944}


def _p(id, name, mi, price=None):
    return Place(id=id, name=name, category="truck_stop", lat=38.58, lng=-121.5 - mi / 100, distance_mi=mi,
                 address=f"{int(mi * 100)} W Capitol Ave, West Sacramento, CA", diesel_price=price,
                 diesel_kind="truck" if price else None)


ROADYS, WESTSAC, FORTYNINER = _p("roadys", "Roady's Truck Stop", 4.2), _p("westsac", "West Sac Truck Stop", 1.2), \
    _p("49er", "Sacramento 49er Travel Plaza", 2.7)


def _sandbox(agent_sandbox, fake_http, places):
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    agent_sandbox.places_provider.places = places
    agent_sandbox.llm.on(r"park tonight|truck stop", {"intent": "find", "category": "truck_stop"})
    agent_sandbox.llm.on(r"cheapest diesel", {"intent": "pick", "place_index": 2})  # what gpt-5.4-mini did
    return agent_sandbox.provider()


def _req(text, sid, active=None):
    return AgentRequest(text=text, session_id=sid, active_place=active, **SAC)


def test_no_prices_keeps_the_current_stop(agent_sandbox, fake_http):
    prov = _sandbox(agent_sandbox, fake_http, [ROADYS, WESTSAC, FORTYNINER])
    first = run(prov.handle(_req("where can I park tonight", "h8"), None))
    assert first.status == "route_set" and first.place.id == WESTSAC.id
    res = run(prov.handle(_req("where's the cheapest diesel", "h8", active=first.place), None))
    assert res.status == "chat" and res.place is None, res
    assert res.reply == NO_PRICES_KEEP.format(name=WESTSAC.name)
    assert WESTSAC.id not in agent._SESSIONS["h8"].get("rejected", set())  # not a rejection


def test_no_prices_and_no_current_stop_routes_the_closest_with_the_caveat(agent_sandbox, fake_http):
    prov = _sandbox(agent_sandbox, fake_http, [ROADYS, WESTSAC, FORTYNINER])
    run(prov.handle(_req("find a truck stop", "h8b"), None))
    res = run(prov.handle(_req("where's the cheapest diesel", "h8b"), None))  # route dropped in the app
    assert res.status == "route_set" and res.place.id == WESTSAC.id, res.reply
    assert res.reply.endswith(NO_PRICE.strip())


def test_a_price_on_the_list_lets_the_pick_through(agent_sandbox, fake_http):
    priced = [ROADYS, WESTSAC, _p("49er", "Sacramento 49er Travel Plaza", 2.7, price=4.89)]
    prov = _sandbox(agent_sandbox, fake_http, priced)
    first = run(prov.handle(_req("where can I park tonight", "h8c"), None))
    shown = agent._SESSIONS["h8c"]["places"]
    res = run(prov.handle(_req("where's the cheapest diesel", "h8c", active=first.place), None))
    picked = agent.numbered(shown, first.place.id)[1]  # place_index 2 of the list the model saw
    assert res.status == "route_set" and res.place.id == picked.id, res.reply  # the model's pick, untouched


def test_no_prices_no_route_and_the_router_down_is_said(agent_sandbox, fake_http):
    """Review 2026-11-02, bug 18: the same pick with no active route and HERE down came out as a 500, the
    closest-place route ran outside the turn's error handling. The driver hears "couldn't build a route"."""
    fake_http.add("router.hereapi.com", "/v8/routes", status=500, text="boom")
    prov = _sandbox(agent_sandbox, fake_http, [ROADYS, WESTSAC])
    agent._SESSIONS["h8-down"] = agent.DialogState(places=[ROADYS, WESTSAC])
    res = run(prov.handle(_req("where's the cheapest diesel", "h8-down"), None))
    assert res.status == "error" and res.reply == agent.ROUTE_DOWN


EMPTY_OFFER = "I can check the cheapest one in the list if you want."  # what the model said (QA 2026-11-02)
OFFER = ("None of these list a diesel price, so I can't tell which is cheapest. The closest is West Sac Truck Stop, "
         "1.2 miles. Want to go there?")


def test_cheapest_as_small_talk_names_the_closest_and_offers_it(agent_sandbox, fake_http):
    """QA 2026-11-02: "where's the cheapest diesel" with no price on the list came back as chat, "I can check the
    cheapest one in the list if you want": an offer of nothing, nobody has a price. The copilot says so, names
    the closest and offers to go there; "yes" routes to it."""
    prov = _sandbox(agent_sandbox, fake_http, [ROADYS, WESTSAC, FORTYNINER])
    agent_sandbox.llm.rules.insert(0, (re.compile(r"cheapest diesel", re.I),
                                       {"intent": "chat", "reply": EMPTY_OFFER}))
    run(prov.handle(_req("find a truck stop", "h8d"), None))
    fast = _req("where's the cheapest diesel", "h8d", active=ROADYS).model_copy(update={"speed_mps": 30.0})
    res = run(prov.handle(fast, None))
    assert res.status == "confirm" and res.reply == OFFER  # whole at highway speed: the question is the point
    yes = run(prov.handle(_req("yes", "h8d", active=ROADYS), None))
    assert yes.status == "route_set" and yes.place.id == WESTSAC.id, yes.reply

    run(prov.handle(_req("find a truck stop", "h8e"), None))
    res = run(prov.handle(_req("where's the cheapest diesel", "h8e"), None))  # no route: the same offer
    assert res.reply == OFFER
    no = run(prov.handle(_req("no", "h8e"), None))
    assert no.status == "chat" and no.route is None
