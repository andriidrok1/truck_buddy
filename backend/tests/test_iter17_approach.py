"""Satellite approach logic: context lines for all session places, approach bearing, parking intent."""
import asyncio

from models import AgentRequest, Maneuver, ParkingCheck, ParkingSpot, Place, RouteInfo
from services import approach, parking
from services.agent import LlmAgentProvider, _session


def P(i, lat, lng, cat="truck_stop"):
    return Place(id=f"p{i}", name=f"Stop {i}", category=cat, lat=lat, lng=lng)


def seed(p, verdict, entry=None, park=None, spots=()):
    c = ParkingCheck(verdict=verdict, label=verdict, reason="r", source="satellite", image_path="/satellite?x",
                     entry_tip=entry, park_tip=park, spots=list(spots))
    parking._verdict_cache[parking._key(p.lat, p.lng)] = c
    return c


def test_context_lists_every_session_verdict():
    a, b, c = P(1, 10.1, 20.1), P(2, 10.2, 20.2), P(3, 10.3, 20.3)
    seed(a, "likely", "Use the east driveway", "Back row")
    seed(b, "unlikely")
    sess = _session("ctx-test")
    sess["places"] = [a, b, c]
    req = AgentRequest(text="t", lat=10, lng=20, session_id="ctx-test", active_place=a)
    ctx = LlmAgentProvider()._context(req, sess)
    assert "1=Stop 1 (truck_stop) 0.0 mi: likely; Entry: Use the east driveway; Park: Back row" in ctx
    assert "2=Stop 2 (truck_stop) 0.0 mi: unlikely" in ctx
    assert "3=Stop 3 (truck_stop) 0.0 mi: parking not checked yet" in ctx
    assert "Satellite parking check there: likely" in ctx  # current route


def test_destination_gets_same_parking_lines():
    d = P(4, 11.0, 21.0, "destination")
    req = AgentRequest(text="t", lat=10, lng=20, active_place=d)
    assert "Parking there has not been checked yet." in LlmAgentProvider()._context(req, _session(None))
    seed(d, "likely", "Enter from the south gate")
    assert "Entry: Enter from the south gate." in LlmAgentProvider()._context(req, _session(None))


def test_approach_from_route():
    # Heading due north for ~1 km, last maneuver onto Main St.
    geo = [[38.0 + i * 0.001, -121.0] for i in range(10)]
    r = RouteInfo(geometry=geo, distance_m=1000, duration_s=60, distance_mi=0.6, duration_min=1, summary="", source="t",
                  maneuvers=[Maneuver(action="turn", direction="left", instruction="Turn left onto Main St", lat=0, lng=0, index=2),
                             Maneuver(action="arrive", instruction="Arrive at Main St", lat=0, lng=0, index=9)])
    ap = approach.from_route(r)
    assert ap.bearing in (0, 359, 1) and ap.compass == "south" and ap.street == "Main St"


def test_target_is_entry_spot():
    p = P(5, 12.0, 22.0)
    assert approach.target(p) == (12.0, 22.0)
    seed(p, "likely", "x", spots=[ParkingSpot(kind="entry", x=0.5, y=0.4, lat=12.0005, lng=22.0001)])
    assert approach.target(p) == (12.0005, 22.0001)


def test_speech_relative():
    c = ParkingCheck(verdict="likely", label="", reason="", source="satellite",
                     entry_tip="On your right off Main Street, past the canopy", park_tip="Park in the back row", relative=True)
    s = approach.speech(c)
    assert "The truck entrance is on your right off Main Street" in s and s.endswith("back row.")


def test_parking_intent_cached_vs_pending():
    agent = LlmAgentProvider()
    p, q = P(6, 13.0, 23.0), P(7, 13.5, 23.5)
    seed(p, "likely", "Use the north driveway")
    sess = {"history": [], "places": [p, q]}
    req = AgentRequest(text="how do I get in", lat=13, lng=23)
    r = agent._parking(req, {"place_index": 1}, sess, "how do I get in")
    assert "To get in: Use the north driveway" in r.reply and not r.actions

    async def run():
        parking._fail_cache[parking._key(q.lat, q.lng)] = (1e18, ParkingCheck(  # no real vision call in tests
            verdict="unclear", label="", reason="", source="none"))
        return agent._parking(req, {"place_index": 2}, sess, "how do I get in")
    r = asyncio.run(run())
    assert r.reply == approach.LOOKING and r.actions[0].type == "parking_followup" and r.actions[0].value == "p7"
