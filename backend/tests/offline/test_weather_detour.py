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


def _alert(event, index, severity="Severe", ends=None, onset=None, area="Sierra Nevada", index_end=None):
    return WeatherAlert(id=f"{event}@{index}", event=event, severity=severity, headline=event, area=area,
                        index=index, ends=ends, onset=onset, index_end=index_end)


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


def test_in_it_already_still_says_when_it_ends():
    r = _route()
    r.weather = [_alert("Dense Fog Advisory", 0, ends=(NOW + timedelta(hours=3)).isoformat(), area="Sacramento Valley")]
    assert weather.speech(r.weather, r, NOW) == "Weather warning: Dense Fog Advisory ahead near Sacramento Valley, in effect till 4 pm."
    assert weather.speech(r.weather) == "Weather warning: Dense Fog Advisory ahead near Sacramento Valley."


def test_alerts_over_before_arrival_are_dropped():
    r = _route()
    last = len(r.geometry) - 1
    gone = _alert("Wind Advisory", last, ends=(NOW + timedelta(hours=1)).isoformat())  # over by 2 pm, truck at ~3:50
    still = _alert("High Wind Warning", last, ends=(NOW + timedelta(hours=4)).isoformat())
    open_ended = _alert("Dense Fog Advisory", last)
    assert weather.on_arrival([gone, still, open_ended], r, NOW) == [still, open_ended]
    # Starts tomorrow evening: NWS lists it as active now, the truck is long through. Starting within three
    # hours of the ETA it stays (a stop can shift the ETA that much).
    later = _alert("Winter Storm Warning", last, onset=(NOW + timedelta(hours=20)).isoformat(),
                   ends=(NOW + timedelta(hours=40)).isoformat())
    soon = _alert("Winter Storm Warning", last, onset=(NOW + timedelta(hours=5)).isoformat(),
                  ends=(NOW + timedelta(hours=40)).isoformat())
    assert weather.on_arrival([later, soon], r, NOW) == [soon]
    assert weather.starting_later([gone, later, soon], r, NOW) == [later]


def test_a_storm_that_starts_while_the_truck_is_still_inside_is_weather_now():
    """Review 2026-11-01: timing by the entry vertex only dropped a storm starting before the truck left the
    zone. Entered at index 300 (~45 min in), left at the last vertex (2 h 52 min): onset at +2 h is "now"."""
    r = _route()
    last = len(r.geometry) - 1
    storm = _alert("Winter Storm Warning", 300, onset=(NOW + timedelta(hours=2)).isoformat(),
                   ends=(NOW + timedelta(hours=30)).isoformat(), index_end=last)
    assert weather.on_arrival([storm], r, NOW) == [storm] and weather.starting_later([storm], r, NOW) == []
    # Unknown exit (point path, None): the route end is assumed, the conservative side.
    storm.index_end = None
    assert weather.on_arrival([storm], r, NOW) == [storm]
    # Left the zone early (index_end 320, a few minutes in): a storm 5 h later is "later".
    storm.index_end = 320
    storm.onset = (NOW + timedelta(hours=5)).isoformat()
    assert weather.starting_later([storm], r, NOW) == [storm]
    r.weather_later = [storm]
    assert weather.later_speech(r, NOW).endswith("you're through there by 1:30 pm.")  # the exit (index 320), not the entry


def test_expires_is_not_the_end_of_the_hazard():
    p = {"id": "x", "event": "Flood Warning", "severity": "Severe", "headline": "h", "areaDesc": "Maricopa, AZ",
         "expires": (NOW + timedelta(hours=1)).isoformat(), "onset": NOW.isoformat()}
    a = weather._to_alert(p, 100, 400)
    assert a.ends is None and a.index_end == 400
    r = _route()
    assert weather.on_arrival([a], r, NOW) == [a]  # never "over" on the strength of an expiry


def test_clears_by_is_the_end_of_the_last_alert_and_one_county_counts_once():
    r = _route()
    ca89, i580 = r.alternatives
    ca89.weather_known = i580.weather_known = True
    soon = _alert("Flood Warning", 300, ends=(NOW + timedelta(minutes=15)).isoformat(), area="Maricopa, AZ")
    late = WeatherAlert(id="late", event="Flood Warning", severity="Severe", headline="h", area="Maricopa, AZ",
                        index=320, ends=(NOW + timedelta(hours=14)).isoformat())
    r.weather = [soon, late]
    ca89.weather = i580.weather = [soon, late]
    assert weather.detour_speech(r, NOW) == "No truck route around it; it should clear by tomorrow 3 am."
    # Two warnings for the same county: "Weather warning: Flood Warning near Maricopa, AZ, ...", not "and 1 more".
    assert "more weather alert" not in weather.speech(r.weather, r, NOW)
    assert weather.speech(r.weather, r, NOW).endswith(", in effect till tomorrow 3 am.")


def test_the_end_time_is_said_once(agent_sandbox, fake_http, monkeypatch):
    """Stand 2026-11-01, Tucson-Phoenix: "in effect till 3:45 pm. No truck route around it; it should clear by
    3:45 pm." The warning line already said when it ends; the detour line must not."""
    fake_http.add("router.hereapi.com", "/v8/routes", json=ROUTE)
    ends = (datetime.now(PT) + timedelta(hours=8)).isoformat()  # still on when the slowest alternative gets there
    flood = _alert("Flash Flood Warning", 1267, ends=ends, area="Pima, AZ")
    monkeypatch.setattr(weather, "alerts_along", _by_geometry([flood], [flood], [flood]))
    agent_sandbox.llm.on(r"take me to reno", {"intent": "go", "query": "Reno, NV"})
    agent_sandbox.geocode["reno"] = [RENO]
    res = run(agent_sandbox.provider().handle(AgentRequest(text="take me to Reno", session_id="wt", **SAC), None))
    assert ", in effect till " in res.reply and res.reply.endswith("No truck route around it.")
    assert "should clear by" not in res.reply
    # Several alerts (no single end in the warning line): the clearing time is still said once, in the detour line.
    fog = _alert("Dense Fog Advisory", 1200, ends=ends, area="Pinal, AZ")
    monkeypatch.setattr(weather, "alerts_along", _by_geometry([flood, fog], [flood, fog], [flood, fog]))
    res = run(agent_sandbox.provider().handle(AgentRequest(text="take me to Reno", session_id="wt2", **SAC), None))
    assert "more weather alert" in res.reply and "should clear by" in res.reply and "in effect till" not in res.reply


def test_a_storm_after_the_truck_is_through_gets_a_heads_up():
    r = _route()
    last = len(r.geometry) - 1
    r.weather_later = [_alert("Winter Storm Warning", last, onset=(NOW + timedelta(hours=17)).isoformat(),
                              ends=(NOW + timedelta(hours=40)).isoformat(), area="Donner Pass")]
    # Arrival 3:52 pm, said as 3:30; the storm starts 6 am tomorrow.
    assert weather.later_speech(r, NOW) == \
        "Heads up, a Winter Storm Warning near Donner Pass starts tomorrow 6 am; at this pace you're through there by 3:30 pm."
    r.weather_later = []
    assert weather.later_speech(r, NOW) == ""


def test_the_dialog_says_the_later_storm_when_nothing_is_on_the_way_now(agent_sandbox, fake_http, monkeypatch):
    fake_http.add("router.hereapi.com", "/v8/routes", json=ROUTE)
    later = _alert("Winter Storm Warning", 1267, onset=(datetime.now(PT) + timedelta(hours=20)).isoformat(),
                   ends=(datetime.now(PT) + timedelta(hours=40)).isoformat(), area="Reno Area")
    monkeypatch.setattr(weather, "alerts_along", _by_geometry([later], [], []))
    agent_sandbox.llm.on(r"take me to reno", {"intent": "go", "query": "Reno, NV"})
    agent_sandbox.geocode["reno"] = [RENO]
    res = run(agent_sandbox.provider().handle(AgentRequest(text="take me to Reno", session_id="wl", **SAC), None))
    assert res.route.weather == [] and res.route.weather_known and len(res.route.weather_later) == 1
    assert "Heads up, a Winter Storm Warning near Reno Area starts tomorrow " in res.reply
    assert "you're through there by" in res.reply and "around it" not in res.reply


# ---- the choice between routes ----

def test_better_route_is_the_lightest_then_the_fastest():
    r = _route()
    ca89, i580 = r.alternatives
    r.weather = [_alert("High Wind Warning", 300)]
    assert weather.better_route(r) is None  # nothing looked up yet: an empty list is not "clear"
    ca89.weather_known = i580.weather_known = True
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


def test_a_closed_road_is_never_the_way_around():
    from models import Incident
    r = _route()
    ca89, i580 = r.alternatives
    r.weather = [_alert("High Wind Warning", 300)]
    ca89.weather, i580.weather = [], []
    ca89.weather_known = i580.weather_known = True
    ca89.incidents = [Incident(type="roadClosure", description="CA-89 closed at Sierraville", closed=True, index=500)]
    assert weather.better_route(r) is i580
    i580.incidents = list(ca89.incidents)
    assert weather.better_route(r) is None and weather.detour_speech(r, NOW) == "No truck route around it."


def test_unknown_alternatives_claim_nothing():
    r = _route()
    r.weather = [_alert("High Wind Warning", 300)]
    assert weather.detour_speech(r, NOW, alts_known=False) == ""


def test_detour_wording():
    r = _route()
    ca89, i580 = r.alternatives
    r.weather = [_alert("High Wind Warning", 300, ends=(NOW + timedelta(hours=6)).isoformat())]
    ca89.weather, i580.weather = [], []
    ca89.weather_known = i580.weather_known = True
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
    r.alternatives = []
    assert weather.detour_speech(r, NOW) == "I don't have another truck route to try."  # nothing to compare: no "around it"
    r.alternatives = [ca89, i580]
    same = ca89.model_copy(update={"duration_min": r.duration_min + 2, "weather": [], "labels": [], "weather_known": True})
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
    assert res.route.weather_known and all(a.weather_known for a in res.route.alternatives)
    res = run(prov.handle(AgentRequest(text="take the other way", session_id="wd", active_place=RENO, **SAC), None))
    assert res.status == "route_set" and res.route.labels == ["I-80", "CA-89"] and res.route.weather == []
    assert res.reply == "Here's another way via I-80 and CA-89, 4 h 38 min, 204 miles, about 1 h 46 min longer, " \
                        "and it misses the High Wind Warning."
    # The next "another way" is the one that still has weather, and says so.
    res = run(prov.handle(AgentRequest(text="take the other way", session_id="wd", active_place=RENO, **SAC), None))
    assert res.route.labels == ["I-80", "I-580"] and res.reply.endswith(", but it runs into a Wind Advisory.")
    # Back to the original (its weather and weather_known were carried onto the cached copy): the High Wind
    # Warning is said again, since the I-580 road did not have it.
    res = run(prov.handle(AgentRequest(text="take the other way", session_id="wd", active_place=RENO, **SAC), None))
    assert res.route.labels == ["I-80"] and res.reply.endswith(", but it runs into a High Wind Warning.")


def test_the_other_way_with_weather_is_said_even_when_the_first_road_was_clear(agent_sandbox, fake_http, monkeypatch):
    """Review 2026-11-01: a clear main road and a stormy alternative said nothing about the storm."""
    fake_http.add("router.hereapi.com", "/v8/routes", json=ROUTE)
    wind = _alert("High Wind Warning", 600, area="Sierra Nevada")
    monkeypatch.setattr(weather, "alerts_along", _by_geometry([], [wind], []))
    agent_sandbox.llm.on(r"take me to reno", {"intent": "go", "query": "Reno, NV"})
    agent_sandbox.llm.on(r"take the other way", {"intent": "alternative"})
    agent_sandbox.geocode["reno"] = [RENO]
    prov = agent_sandbox.provider()
    res = run(prov.handle(AgentRequest(text="take me to Reno", session_id="wc", **SAC), None))
    assert "Weather" not in res.reply
    res = run(prov.handle(AgentRequest(text="take the other way", session_id="wc", active_place=RENO, **SAC), None))
    assert res.route.labels == ["I-80", "CA-89"] and res.reply.endswith(", but it runs into a High Wind Warning.")


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


def test_slow_alternatives_keep_the_warning_and_drop_the_detour_line(agent_sandbox, fake_http, monkeypatch):
    """A long route with alternatives: the route's own alerts fit the budget, the alternatives don't. Before,
    one wait_for over everything lost the warning too."""
    import asyncio
    hit = _alert("High Wind Warning", 1267, area="Reno Area")

    async def fake(geometry, now=None):
        if len(geometry) == 1268:
            return [hit]
        await asyncio.sleep(5)
        return []

    monkeypatch.setattr(weather, "alerts_along", fake)
    monkeypatch.setattr(agent, "DETOUR_BUDGET_S", 0.05)
    fake_http.add("router.hereapi.com", "/v8/routes", json=ROUTE)
    agent_sandbox.llm.on(r"take me to reno", {"intent": "go", "query": "Reno, NV"})
    agent_sandbox.llm.on(r"another way", {"intent": "alternative"})
    agent_sandbox.geocode["reno"] = [RENO]
    prov = agent_sandbox.provider()
    res = run(prov.handle(AgentRequest(text="take me to Reno", session_id="wk", **SAC), None))
    assert "Weather warning: High Wind Warning near Reno Area, you'd hit it around " in res.reply
    assert "around it" not in res.reply and "misses" not in res.reply and "other way" not in res.reply
    assert all(a.weather == [] and not a.weather_known for a in res.route.alternatives) and res.route.weather_known
    # "Another way" keeps HERE's order and makes no weather claim about a road it doesn't know.
    res = run(prov.handle(AgentRequest(text="another way", session_id="wk", active_place=RENO, **SAC), None))
    assert res.route.labels == ["I-80", "CA-89"] and "misses" not in res.reply and "runs into" not in res.reply


def test_slow_weather_leaves_every_route_clean(agent_sandbox, fake_http, monkeypatch):
    import asyncio

    async def slow(*a, **k):
        await asyncio.sleep(5)
        return []

    monkeypatch.setattr(agent, "WEATHER_BUDGET_S", 0.05)
    monkeypatch.setattr(agent, "DETOUR_BUDGET_S", 0.05)
    monkeypatch.setattr(weather, "alerts_along", slow)
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
                                          "areaDesc": "Reno Area"},
        "geometry": {"type": "Polygon", "coordinates": [[[-121, 37], [-118, 37], [-118, 41], [-121, 41], [-121, 37]]]}}]})
    body = {**SAC, "place": RENO.model_dump(), "handle": "h1", "heading": 90}
    r = api_client.post("/api/reroute", json=body)
    assert r.status_code == 200
    w = r.json()["route"]["weather"]
    assert len(w) == 1 and w[0]["event"] == "High Wind Warning"
