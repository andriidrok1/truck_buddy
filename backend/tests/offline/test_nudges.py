"""Proactive nudges: break due with a stop ahead, parking before the shift ends; accepted with a plain "yes"."""
from conftest import fixture_json, run

from models import AgentRequest, NudgeRequest, Place, TruckProfile
from services import agent, hos, nudges
from services.nudges import break_due_min, point_ahead

SAC = {"lat": 38.5816, "lng": -121.4944}
RENO = Place(id="here-reno", name="Reno, NV", category="destination", lat=39.5296, lng=-119.8138)
# Route points ahead (every ~5 miles up to 60), roughly along I-80 east of Sacramento.
AHEAD = [[38.60 + i * 0.04, -121.45 + i * 0.09, 5.0 * i] for i in range(1, 13)]
PILOT = Place(id="g-pilot", name="Pilot Travel Center, Auburn", category="truck_stop", lat=AHEAD[6][0] + 0.005,
              lng=AHEAD[6][1], parking_available=22, parking_capacity=60)
REST = Place(id="g-rest", name="Gold Run Rest Area", category="rest_area", lat=AHEAD[7][0], lng=AHEAD[7][1] + 0.01)


def test_break_due_min():
    assert break_due_min(hos.Hos(drive_min=260, shift_min=540, cycle_min=2500, break_min=35), None, None) == 35
    assert break_due_min(hos.Hos(drive_min=50, shift_min=540, cycle_min=2500), None, None) == 50
    assert break_due_min(None, 230, 4) == 10  # 4-hour reminder, 3 h 50 min driven
    assert break_due_min(None, 250, 4) == 230  # just had one
    assert break_due_min(None, 100, None) is None and break_due_min(None, None, 4) is None


def test_point_ahead():
    assert point_ahead(AHEAD, 35.0)[2] == 35.0
    assert point_ahead(AHEAD, 500.0)[2] == 60.0
    assert point_ahead([], 10.0) is None and point_ahead([[1, 2]], 10.0) is None


def _req(**kw) -> NudgeRequest:
    base = dict(driving=True, active_place=RENO, ahead=AHEAD, session_id="n1", **SAC)
    base.update(kw)
    return NudgeRequest(**base)


def test_break_nudge_once_per_cycle(agent_sandbox, monkeypatch):
    agent_sandbox.places_provider.places = [PILOT, REST]

    async def fake_hos(key):
        return hos.Hos(drive_min=200, shift_min=500, cycle_min=2000, break_min=45)

    monkeypatch.setattr(hos, "available_time", fake_hos)
    sess = {"history": [], "places": []}
    out = run(nudges.compute(_req(), sess, "key"))
    assert len(out) == 1 and out[0].kind == "break" and out[0].offer == "add_stop"
    assert out[0].text == ("Your break is due in 45 minutes. There's a Pilot Travel Center in 35 miles, "
                           "want me to add it?")
    assert sess["pending"]["d"] == {"intent": "add_stop", "place": PILOT.model_dump()}
    assert run(nudges.compute(_req(), sess, "key")) == []  # not again this cycle

    async def after_break(key):
        return hos.Hos(drive_min=480, shift_min=400, cycle_min=2000, break_min=470)

    monkeypatch.setattr(hos, "available_time", after_break)
    sess.pop("hos_cache", None)
    assert run(nudges.compute(_req(), sess, "key")) == [] and "nudge_break_done" not in sess


def test_break_without_motive_uses_the_profile_reminder(agent_sandbox):
    agent_sandbox.places_provider.places = [REST]
    sess = {"history": [], "places": []}
    req = _req(truck_profile=TruckProfile(break_hours=4), drive_elapsed_min=225)
    out = run(nudges.compute(req, sess, None))
    assert out and out[0].text == "Your break is due in 15 minutes. There's a Gold Run Rest Area in 41 miles, " \
        "want me to add it?"
    req = _req(truck_profile=TruckProfile(break_hours=4), drive_elapsed_min=238, session_id="n2")
    out = run(nudges.compute(req, {"history": [], "places": []}, None))
    assert out and out[0].text.startswith("Your break is due now.")
    req = _req(truck_profile=TruckProfile(break_hours=4), drive_elapsed_min=240, session_id="n2b")
    assert run(nudges.compute(req, {"history": [], "places": []}, None)) == []  # just had one: due in 4 h


def test_parking_nudge_by_shift_and_by_clock(agent_sandbox, monkeypatch):
    agent_sandbox.places_provider.places = [PILOT]

    async def late_shift(key):
        return hos.Hos(drive_min=300, shift_min=80, cycle_min=2000, break_min=200)

    monkeypatch.setattr(hos, "available_time", late_shift)
    sess = {"history": [], "places": []}
    out = run(nudges.compute(_req(), sess, "key"))
    assert [n.kind for n in out] == ["parking"]
    assert out[0].text == ("About 80 minutes on the shift. The Pilot Travel Center in 35 miles has 22 spots open right "
                           "now, should I route you there?")
    assert sess["pending"]["d"]["intent"] == "go"
    assert run(nudges.compute(_req(), sess, "key")) == []
    sess2 = {"history": [], "places": []}
    out = run(nudges.compute(_req(session_id="n3", local_hour=19, drive_elapsed_min=500), sess2, None))
    assert out and out[0].text.startswith("It's getting late. The Pilot Travel Center in 35 miles has 22 spots")
    noon = _req(session_id="n4", local_hour=12, drive_elapsed_min=500)
    assert run(nudges.compute(noon, {"history": [], "places": []}, None)) == []


def test_nothing_without_driving_places_or_flags(agent_sandbox, monkeypatch):
    agent_sandbox.places_provider.places = []
    sess = {"history": [], "places": []}
    assert run(nudges.compute(_req(driving=False), sess, None)) == []
    assert run(nudges.compute(_req(truck_profile=TruckProfile(break_hours=1), drive_elapsed_min=55), sess, None)) == []
    agent_sandbox.places_provider.places = [PILOT]
    monkeypatch.setenv("NUDGE_BREAK", "0")
    assert run(nudges.compute(_req(truck_profile=TruckProfile(break_hours=1), drive_elapsed_min=55), sess, None)) == []
    assert "pending" not in sess


def test_yes_after_a_break_nudge_adds_the_stop(agent_sandbox, fake_http, monkeypatch):
    fake_http.add("router.hereapi.com", "/v8/routes", json=fixture_json("here/route_sac_reno"))
    fake_http.add("api.weather.gov", "/alerts/active", json={"features": []})
    fake_http.add("cwwp2.dot.ca.gov", "/data/", json={"data": []})
    agent_sandbox.places_provider.places = [PILOT]

    async def fake_hos(key):
        return hos.Hos(drive_min=40, shift_min=500, cycle_min=2000)

    monkeypatch.setattr(hos, "available_time", fake_hos)
    prov = agent_sandbox.provider()
    sess = agent._session("n5")
    out = run(nudges.compute(_req(session_id="n5"), sess, "key"))
    assert out and out[0].kind == "break"
    res = run(prov.handle(AgentRequest(text="yes", session_id="n5", active_place=RENO, **SAC), None))
    assert res.status == "route_set" and res.reply.startswith("Added Pilot Travel Center.")
    assert res.route.via == [[PILOT.lat, PILOT.lng]] and len(agent_sandbox.llm.calls) == 0  # no LLM call


def test_yes_after_a_parking_nudge_routes_there(agent_sandbox, fake_http, monkeypatch):
    fake_http.add("router.hereapi.com", "/v8/routes", json=fixture_json("here/route_sac_reno"))
    fake_http.add("api.weather.gov", "/alerts/active", json={"features": []})
    fake_http.add("cwwp2.dot.ca.gov", "/data/", json={"data": []})
    agent_sandbox.places_provider.places = [PILOT]
    prov = agent_sandbox.provider()
    sess = agent._session("n6")
    out = run(nudges.compute(_req(session_id="n6", local_hour=20, drive_elapsed_min=600), sess, None))
    assert out and out[0].kind == "parking"
    res = run(prov.handle(AgentRequest(text="sure", session_id="n6", active_place=RENO, **SAC), None))
    assert res.status == "route_set" and res.place.id == PILOT.id


def test_nudges_endpoint(api_client, agent_sandbox, monkeypatch):
    agent_sandbox.places_provider.places = [PILOT]

    async def fake_hos(key):
        assert key == "motive-key"
        return hos.Hos(drive_min=30, shift_min=500, cycle_min=2000)

    monkeypatch.setattr(hos, "available_time", fake_hos)
    body = {**SAC, "driving": True, "active_place": RENO.model_dump(), "ahead": AHEAD, "session_id": "e1"}
    r = api_client.post("/api/nudges", json=body, headers={"X-Motive-Key": "motive-key"})
    assert r.status_code == 200, r.text
    nudge = r.json()["nudges"][0]
    assert nudge["kind"] == "break" and nudge["offer"] == "add_stop" and nudge["place"]["id"] == PILOT.id
    assert "motive" not in r.text.lower()
    r = api_client.post("/api/nudges", json=body, headers={"X-Motive-Key": "motive-key"})
    assert r.json()["nudges"] == []
    assert api_client.post("/api/nudges", json={**body, "lat": 48.9, "lng": 2.3}).status_code == 422
