"""Weather against the route: the alert timed by the ETA, alerts over before arrival dropped, the alternative
that misses the weather offered and served first by "take the other way", and an honest "no way around"."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from conftest import fixture_json, run

from models import AgentRequest, Place, WeatherAlert
from services import agent, weather
from services.routing import _here_parse

ROUTE = fixture_json("here/route_sac_reno")  # I-80 2 h 52 min; alternatives via CA-89 (4 h 38) and I-580 (4 h 56)
SAC = {"lat": 38.5816, "lng": -121.4944}
RENO = Place(id="here-reno", name="Reno, NV", category="destination", lat=39.5296, lng=-119.8138)
PT = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 11, 3, 13, 0, tzinfo=PT)


def setup_function():
    weather._alert_cache.clear()


def teardown_function():
    weather._alert_cache.clear()  # the reroute test's alert must not reach the next module on this worker


def _alert(event, index, severity="Severe", ends=None, onset=None, area="Sierra Nevada"):
    return WeatherAlert(id=f"{event}@{index}", event=event, severity=severity, headline=event, area=area,
                        index=index, ends=ends, onset=onset)


def _route():
    return _here_parse(ROUTE)


def _by_geometry(main, ca89, i580):
    """A fake alerts_along keyed by which route's geometry it gets (the three differ in length)."""
    sizes = {1268: main, 1243: ca89, 1200: i580}

    async def fake(geometry, now=None):
        return list(sizes[len(geometry)])

    return fake


# ---- timing ----

def test_eta_at_scales_with_the_distance_covered():
    r = _route()
    assert weather.eta_at(r, 0, NOW) == NOW
    end = weather.eta_at(r, len(r.geometry) - 1, NOW)
    assert abs((end - NOW).total_seconds() - r.duration_s) < 1
    mid = weather.eta_at(r, len(r.geometry) // 2, NOW)
    assert NOW < mid < end


def test_warning_is_timed_by_the_eta_and_says_when_it_ends():
    r = _route()
    last = len(r.geometry) - 1
    ends = (NOW + timedelta(hours=5)).isoformat()
    r.weather = [_alert("High Wind Warning", last, ends=ends, area="Reno Area")]
    # 2 h 52 min from 1 pm = 3:52 pm, rounded down to the half hour.
    assert weather.speech(r.weather, r, NOW) == \
        "Weather warning: High Wind Warning near Reno Area, you'd hit it around 3:30 pm, in effect till 6 pm."
    r.weather = [_alert("High Wind Warning", last, area="Reno Area"), _alert("Dense Fog Advisory", last)]
    assert weather.speech(r.weather, r, NOW) == \
        "High Wind Warning near Reno Area around 3:30 pm and 1 more weather alert along the route."
    # In it already (index 0): no clock, the old line.
    r.weather = [_alert("High Wind Warning", 0, area="Sacramento Valley")]
    assert weather.speech(r.weather, r, NOW) == "Weather warning: High Wind Warning ahead near Sacramento Valley."
    # Ends tomorrow: the day is said.
    ends = (NOW + timedelta(hours=20)).isoformat()
    r.weather = [_alert("Winter Storm Warning", last, ends=ends, area="Donner Pass")]
    assert weather.speech(r.weather, r, NOW).endswith(", in effect till tomorrow 9 am.")


def test_alerts_over_before_arrival_are_dropped():
    r = _route()
    last = len(r.geometry) - 1
    gone = _alert("Wind Advisory", last, ends=(NOW + timedelta(hours=1)).isoformat())  # over by 2 pm, truck at ~3:50
    still = _alert("High Wind Warning", last, ends=(NOW + timedelta(hours=4)).isoformat())
    open_ended = _alert("Dense Fog Advisory", last)
    assert weather.on_arrival([gone, still, open_ended], r, NOW) == [still, open_ended]


# ---- the choice between routes ----

def test_better_route_is_the_lightest_then_the_fastest():
    r = _route()
    ca89, i580 = r.alternatives
    r.weather = [_alert("High Wind Warning", 300)]
    ca89.weather, i580.weather = [], []
    assert weather.better_route(r) is ca89  # both clear, CA-89 is faster
    ca89.weather = [_alert("Wind Advisory", 300, severity="Moderate")]
    assert weather.better_route(r) is i580  # the clear one wins over the faster one
    i580.weather = [_alert("Winter Storm Warning", 300, severity="Severe")]
    assert weather.better_route(r) is ca89  # lighter weather beats the same weather
    ca89.weather = [_alert("High Wind Warning", 300)]
    assert weather.better_route(r) is None  # nothing beats what the driver has
    r.weather = []
    assert weather.better_route(r) is None


def test_detour_wording():
    r = _route()
    ca89, i580 = r.alternatives
    r.weather = [_alert("High Wind Warning", 300, ends=(NOW + timedelta(hours=6)).isoformat())]
    ca89.weather, i580.weather = [], []
    assert weather.detour_speech(r, NOW) == \
        "The way via I-80 and CA-89 misses it, about 1 h 46 min longer. Say 'take the other way' if you want it."
    ca89.weather = [_alert("Wind Advisory", 300, severity="Moderate")]
    i580.weather = [_alert("Wind Advisory", 300, severity="Moderate")]
    assert weather.detour_speech(r, NOW) == \
        "The way via I-80 and CA-89 only runs into a Wind Advisory, about 1 h 46 min longer. Say 'take the other way' if you want it."
    ca89.weather = i580.weather = [_alert("High Wind Warning", 300)]
    assert weather.detour_speech(r, NOW) == "No truck route around it; it should clear by 7 pm."
    r.weather = [_alert("High Wind Warning", 300)]
    assert weather.detour_speech(r, NOW) == "No truck route around it."
    same = ca89.model_copy(update={"duration_min": r.duration_min + 2, "weather": [], "labels": []})
    r.alternatives = [same]
    assert weather.detour_speech(r, NOW) == "The other way misses it and takes about the same time. Say 'take the other way' if you want it."


# ---- in the dialog ----

def test_new_route_offers_the_way_around_and_the_other_way_takes_it(agent_sandbox, fake_http, monkeypatch):
    fake_http.add("router.hereapi.com", "/v8/routes", json=ROUTE)
    hit = _alert("High Wind Warning", 1267, ends=(datetime.now(PT) + timedelta(hours=8)).isoformat(), area="Reno Area")
    monkeypatch.setattr(weather, "alerts_along", _by_geometry([hit], [], [_alert("Wind Advisory", 1199, severity="Moderate")]))
    agent_sandbox.llm.on(r"take me to reno", {"intent": "go", "query": "Reno, NV"})
    agent_sandbox.llm.on(r"take the other way", {"intent": "alternative"})
    agent_sandbox.geocode["reno"] = [RENO]
    prov = agent_sandbox.provider()
    res = run(prov.handle(AgentRequest(text="take me to Reno", session_id="wd", **SAC), None))
    assert res.status == "route_set" and res.route.labels == ["I-80"]
    assert "Weather warning: High Wind Warning near Reno Area, you'd hit it around " in res.reply
    assert res.reply.endswith("The way via I-80 and CA-89 misses it, about 1 h 46 min longer. "
                              "Say 'take the other way' if you want it.")
    assert [len(a.weather) for a in res.route.alternatives] == [0, 1]  # each alternative carries its own
    res = run(prov.handle(AgentRequest(text="take the other way", session_id="wd", active_place=RENO, **SAC), None))
    assert res.status == "route_set" and res.route.labels == ["I-80", "CA-89"] and res.route.weather == []
    assert res.reply == "Here's another way via I-80 and CA-89, 4 h 38 min, 204 miles, about 1 h 46 min longer, " \
                        "and it misses the High Wind Warning."
    # The next "another way" is the one that still has weather, and says so.
    res = run(prov.handle(AgentRequest(text="take the other way", session_id="wd", active_place=RENO, **SAC), None))
    assert res.route.labels == ["I-80", "I-580"] and res.reply.endswith(", but it runs into a Wind Advisory.")


def test_no_way_around_is_said_once_and_alternatives_keep_their_order(agent_sandbox, fake_http, monkeypatch):
    fake_http.add("router.hereapi.com", "/v8/routes", json=ROUTE)
    storm = _alert("Winter Storm Warning", 600, area="Donner Pass")
    monkeypatch.setattr(weather, "alerts_along", _by_geometry([storm], [storm], [storm]))
    agent_sandbox.llm.on(r"take me to reno", {"intent": "go", "query": "Reno, NV"})
    agent_sandbox.llm.on(r"another way", {"intent": "alternative"})
    agent_sandbox.geocode["reno"] = [RENO]
    prov = agent_sandbox.provider()
    res = run(prov.handle(AgentRequest(text="take me to Reno", session_id="wn", **SAC), None))
    assert res.reply.endswith(" No truck route around it.")
    res = run(prov.handle(AgentRequest(text="another way", session_id="wn", active_place=RENO, **SAC), None))
    assert res.route.labels == ["I-80", "CA-89"] and "misses" not in res.reply and "runs into" not in res.reply


def test_slow_weather_leaves_every_route_clean(agent_sandbox, fake_http, monkeypatch):
    import asyncio

    async def slow(*a, **k):
        await asyncio.sleep(5)

    monkeypatch.setattr(agent, "WEATHER_BUDGET_S", 0.05)
    monkeypatch.setattr(weather, "for_route", slow)
    fake_http.add("router.hereapi.com", "/v8/routes", json=ROUTE)
    agent_sandbox.llm.on(r"take me to reno", {"intent": "go", "query": "Reno, NV"})
    agent_sandbox.geocode["reno"] = [RENO]
    res = run(agent_sandbox.provider().handle(AgentRequest(text="take me to Reno", session_id="ws", **SAC), None))
    assert res.status == "route_set" and "Weather" not in res.reply
    assert res.route.weather == [] and all(a.weather == [] for a in res.route.alternatives)


def test_reroute_carries_weather(api_client, fake_http, monkeypatch):
    fake_http.add("router.hereapi.com", "/v8/routes", json=ROUTE)
    fake_http.add("api.weather.gov", "/alerts/active", json={"features": [{
        "type": "Feature", "properties": {"id": "x", "event": "High Wind Warning", "severity": "Severe",
                                          "status": "Actual", "messageType": "Alert", "headline": "h",
                                          "areaDesc": "Reno Area"}}]})
    body = {**SAC, "place": RENO.model_dump(), "handle": "h1", "heading": 90}
    r = api_client.post("/api/reroute", json=body)
    assert r.status_code == 200
    w = r.json()["route"]["weather"]
    assert len(w) == 1 and w[0]["event"] == "High Wind Warning"
