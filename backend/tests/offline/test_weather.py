"""NWS weather: sampling along the route, alert filtering/dedupe, degradation, spoken lines, the forecast intent."""
import copy
import functools
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
from conftest import fixture_json, params_of, run

from models import AgentRequest, Place, WeatherAlert
from services import agent, weather
from services.weather import parse_alerts, pick_period, resolve_when, sample_points

ROUTE = fixture_json("here/route_sac_reno")
CA_ALERTS = fixture_json("nws/alerts_area_ca")
POINTS = fixture_json("nws/points_reno")
HOURLY = fixture_json("nws/forecast_hourly_reno")
SAC = {"lat": 38.5816, "lng": -121.4944}
RENO = Place(id="here-reno", name="Reno, NV", category="destination", lat=39.5296, lng=-119.8138)
PT = ZoneInfo("America/Los_Angeles")


def setup_function():
    weather._alert_cache.clear()
    weather._points_cache.clear()
    weather._forecast_cache.clear()


def _alert(event, area="Greater Lake Tahoe Area; Donner Pass", aid=None, **extra):
    p = {"id": aid or f"urn:{event}:{area}", "event": event, "severity": "Severe", "status": "Actual",
         "messageType": "Alert", "headline": f"{event} issued", "areaDesc": area, **extra}
    return {"type": "Feature", "properties": p}


def _geometry():
    from services.routing import _here_parse
    return _here_parse(ROUTE).geometry


# ---- sampling ----

def test_sample_points_every_40km_with_ends():
    g = _geometry()
    pts = sample_points(g)
    assert pts[0][0] == 0 and pts[-1][0] == len(g) - 1
    idx = [p[0] for p in pts]
    assert idx == sorted(idx) and len(set(idx)) == len(idx)
    assert 4 <= len(pts) <= 8  # 212 km / 40 km + ends
    assert sample_points([[38.5, -121.4]]) == [(0, 38.5, -121.4)]
    assert sample_points([]) == []
    two = [[38.5, -121.4], [38.6, -121.3]]
    assert sample_points(two) == [(0, 38.5, -121.4), (1, 38.6, -121.3)]


def test_sample_points_capped():
    long = [[30.0 + i * 0.5, -100.0] for i in range(200)]  # ~55 km apart: 200 candidates
    pts = sample_points(long)
    assert len(pts) == weather.MAX_SAMPLES and pts[-1][0] == 199


# ---- alerts ----

def test_parse_alerts_keeps_truck_events_only():
    feats = [_alert("High Wind Warning"), _alert("Coastal Flood Advisory"), _alert("Small Craft Advisory"),
             _alert("Dense Fog Advisory", aid="fog"), _alert("Winter Storm Warning", status="Test"),
             _alert("Tornado Warning", messageType="Cancel"), {"properties": None}, "junk"]
    out = parse_alerts({"features": feats})
    assert [p["event"] for p in out] == ["High Wind Warning", "Dense Fog Advisory"]
    assert parse_alerts({}) == [] and parse_alerts({"features": "x"}) == []
    assert parse_alerts(CA_ALERTS) == []  # the recorded day had only coastal/marine advisories


def test_alerts_along_dedupes_across_points_and_tags_the_first_index(fake_http):
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        point = dict(request.url.params)["point"]
        calls.append(point)
        lat = float(point.split(",")[0])
        feats = [_alert("High Wind Warning")] if lat > 39.0 else []
        if lat > 39.4:
            feats.append(_alert("Winter Storm Warning", area="Sierra Nevada from Yuba County to Reno"))
        return httpx.Response(200, json={"features": feats}, request=request)

    fake_http.add("api.weather.gov", "/alerts/active", fn=respond)
    g = _geometry()
    alerts = run(weather.alerts_along(g))
    assert [a.event for a in alerts] == ["High Wind Warning", "Winter Storm Warning"]
    assert alerts[0].index < alerts[1].index and alerts[0].index > 0
    assert alerts[0].area == "Greater Lake Tahoe Area"
    assert len(calls) == len(sample_points(g))
    assert fake_http.calls[0].headers["User-Agent"].startswith("(TruckBuddy") and \
        fake_http.calls[0].headers["Accept"] == "application/geo+json"
    # cached: a second pass makes no new calls
    assert run(weather.alerts_along(g)) == alerts and len(calls) == len(sample_points(g))


def test_one_bad_point_does_not_spoil_the_rest(fake_http):
    def respond(request: httpx.Request) -> httpx.Response:
        point = dict(request.url.params)["point"]
        lat = float(point.split(",")[0])
        if 38.9 < lat < 39.1:
            return httpx.Response(429, text="slow down", request=request)
        if lat > 39.4:
            raise httpx.ReadTimeout("slow")
        if lat > 38.6:
            return httpx.Response(200, text="<html>not json", request=request)
        return httpx.Response(200, json={"features": [_alert("Dense Fog Advisory", area="Sacramento Valley")]},
                              request=request)

    fake_http.add("api.weather.gov", "/alerts/active", fn=respond)
    alerts = run(weather.alerts_along(_geometry()))
    assert [a.event for a in alerts] == ["Dense Fog Advisory"] and alerts[0].index == 0


def test_flag_off_and_empty_geometry(fake_http, monkeypatch):
    monkeypatch.setenv("WEATHER_ALERTS", "0")
    assert run(weather.alerts_along(_geometry())) == [] and fake_http.calls == []
    monkeypatch.setenv("WEATHER_ALERTS", "1")
    assert run(weather.alerts_along([])) == []


def test_speech_lines():
    a = WeatherAlert(id="1", event="High Wind Warning", severity="Severe", headline="h", area="Donner Pass", index=3)
    b = WeatherAlert(id="2", event="Winter Storm Warning", severity="Severe", headline="h", area="Sierra", index=9)
    assert weather.speech([]) == ""
    assert weather.speech([a]) == "Weather warning: High Wind Warning ahead near Donner Pass."
    assert weather.speech([a, b]) == "High Wind Warning and 1 more weather alerts along the route."


# ---- forecast ----

def test_resolve_when():
    now = datetime(2026, 10, 27, 13, 5, tzinfo=PT)
    assert resolve_when(None, now) == now and resolve_when("now", now) == now
    assert resolve_when("3pm", now) == datetime(2026, 10, 27, 15, 0, tzinfo=PT)
    assert resolve_when("at 3", now) == datetime(2026, 10, 27, 15, 0, tzinfo=PT)
    assert resolve_when("15:30", now) == datetime(2026, 10, 27, 15, 30, tzinfo=PT)
    assert resolve_when("tonight", now) == datetime(2026, 10, 27, 21, 0, tzinfo=PT)
    assert resolve_when("tomorrow morning", now) == datetime(2026, 10, 28, 8, 0, tzinfo=PT)
    assert resolve_when("tomorrow at 6 am", now) == datetime(2026, 10, 28, 6, 0, tzinfo=PT)
    assert resolve_when("8 am", now) == datetime(2026, 10, 28, 8, 0, tzinfo=PT)  # already past: tomorrow
    assert resolve_when("12 pm", now) == datetime(2026, 10, 27, 12, 0, tzinfo=PT)  # within the hour: today
    assert resolve_when("midnight", now) == datetime(2026, 10, 28, 0, 0, tzinfo=PT)
    assert resolve_when("this afternoon", now) == datetime(2026, 10, 27, 15, 0, tzinfo=PT)
    assert resolve_when("around noon", now) == datetime(2026, 10, 27, 12, 0, tzinfo=PT)


def test_pick_period_uses_the_period_timezone():
    periods = HOURLY["properties"]["periods"]
    at = datetime(2026, 10, 27, 15, 0, tzinfo=PT)
    p = pick_period(periods, at)
    assert p and p["startTime"].startswith("2026-10-27T15:00:00")
    assert pick_period(periods, datetime(2026, 10, 27, 22, 0, tzinfo=ZoneInfo("UTC")))["startTime"].startswith(
        "2026-10-27T15:00:00")  # 22:00 UTC = 15:00 PDT
    assert pick_period(periods, datetime(2026, 10, 27, 1, 0, tzinfo=PT)) is periods[0]  # before the forecast starts
    assert pick_period(periods, datetime(2026, 12, 1, 1, 0, tzinfo=PT)) is None
    assert pick_period([], at) is None


def test_forecast_speech_end_to_end(fake_http):
    fake_http.add("api.weather.gov", "/points/", json=POINTS)
    fake_http.add("api.weather.gov", "/gridpoints/REV/45,106/forecast/hourly", json=HOURLY)
    fake_http.add("api.weather.gov", "/alerts/active", json={"features": [_alert("High Wind Warning")]})
    now = datetime(2026, 10, 27, 13, 5, tzinfo=PT)
    out = run(weather.forecast_speech("Reno", RENO.lat, RENO.lng, "3pm", now=now))
    p = pick_period(HOURLY["properties"]["periods"], datetime(2026, 10, 27, 15, 0, tzinfo=PT))
    wind = p["windSpeed"].replace(" mph", " miles an hour")
    assert out == (f"Reno at 3 pm: {p['temperature']} degrees and {p['shortForecast'].lower()}, wind {wind} from the "
                   f"{p['windDirection'].lower()}. Heads up, there's a High Wind Warning in effect there.")
    assert dict(params_of(fake_http.calls_to("api.weather.gov", "/alerts/active")[0]))["point"] == "39.5296,-119.8138"
    # points + forecast cached: a second question costs only the alerts call (cached too within 10 min)
    run(weather.forecast_speech("Reno", RENO.lat, RENO.lng, "4pm", now=now))
    assert len(fake_http.calls) == 3


def test_wind_wording():
    assert weather._wind({"windSpeed": "0 mph", "windDirection": ""}) == ", calm"
    assert weather._wind({"windSpeed": "0 mph", "windDirection": "N"}) == ", calm"  # NWS gives a direction for calm too
    assert weather._wind({"windSpeed": "5 to 10 mph", "windDirection": "SW"}) == ", wind 5 to 10 miles an hour from the sw"
    assert weather._wind({"windSpeed": "12 mph", "windDirection": None, "windGust": "25 mph"}) == \
        ", wind 12 miles an hour, gusting 25"
    assert weather._wind({}) == ""


def test_when_label_names_the_day():
    now = datetime(2026, 10, 28, 21, 27, tzinfo=PT)
    assert weather._when_label(datetime(2026, 10, 28, 22, 0, tzinfo=PT), now) == "at 10 pm"
    assert weather._when_label(datetime(2026, 10, 29, 15, 0, tzinfo=PT), now) == "tomorrow at 3 pm"
    assert weather._when_label(datetime(2026, 10, 30, 15, 30, tzinfo=PT), now) == "Friday at 3:30 pm"


def test_calm_period_and_tomorrow_are_spoken_plainly(fake_http):
    calm = copy.deepcopy(HOURLY)
    for p in calm["properties"]["periods"]:
        p["windSpeed"], p["windDirection"] = "0 mph", "NW"
    fake_http.add("api.weather.gov", "/points/", json=POINTS)
    fake_http.add("api.weather.gov", "/gridpoints/REV/45,106/forecast/hourly", json=calm)
    fake_http.add("api.weather.gov", "/alerts/active", json={"features": []})
    now = datetime(2026, 10, 27, 21, 27, tzinfo=PT)  # "3pm" said at night = tomorrow afternoon
    out = run(weather.forecast_speech("Reno", RENO.lat, RENO.lng, "3pm", now=now))
    p = pick_period(calm["properties"]["periods"], datetime(2026, 10, 28, 15, 0, tzinfo=PT))
    assert out == f"Reno tomorrow at 3 pm: {p['temperature']} degrees and {p['shortForecast'].lower()}, calm."
    assert "no direction" not in out


def test_forecast_failures_are_honest(fake_http):
    fake_http.add("api.weather.gov", "/points/", status=503, text="down")
    out = run(weather.forecast_speech("Reno", RENO.lat, RENO.lng, None))
    assert out == "I couldn't get a forecast for Reno right now."
    fake_http.routes.clear()
    weather._points_cache.clear()
    fake_http.add("api.weather.gov", "/points/", json=POINTS)
    fake_http.raise_for("api.weather.gov", "/gridpoints/", httpx.ReadTimeout("slow"))
    assert run(weather.forecast_speech("Reno", RENO.lat, RENO.lng, None)).startswith("I couldn't get a forecast")


# ---- agent + endpoint ----

def test_weather_intent_in_the_dialog(agent_sandbox, fake_http, monkeypatch):
    # The fixture covers 2026-10-27 13:00 to 10-29 01:00: pin "now" so the test doesn't age out.
    monkeypatch.setattr(weather, "forecast_speech",
                        functools.partial(weather.forecast_speech, now=datetime(2026, 10, 27, 13, 5, tzinfo=PT)))
    fake_http.add("api.weather.gov", "/points/", json=POINTS)
    fake_http.add("api.weather.gov", "/gridpoints/REV/45,106/forecast/hourly", json=HOURLY)
    fake_http.add("api.weather.gov", "/alerts/active", json={"features": []})
    agent_sandbox.llm.on(r"weather in reno", {"intent": "weather", "query": "Reno, NV", "when": "3pm"})
    agent_sandbox.llm.on(r"weather there", {"intent": "weather", "query": None, "when": None})
    agent_sandbox.llm.on(r"weather in narnia", {"intent": "weather", "query": "Narnia", "when": None})
    agent_sandbox.geocode["reno"] = [RENO]
    prov = agent_sandbox.provider()
    res = run(prov.handle(AgentRequest(text="weather in Reno at 3pm", session_id="w1", **SAC), None))
    assert res.status == "chat" and res.reply.startswith("Reno at 3 pm: ") and "degrees" in res.reply
    res = run(prov.handle(AgentRequest(text="what's the weather there", session_id="w2", active_place=RENO, **SAC),
                          None))
    assert res.status == "chat" and res.reply.startswith("Reno at ")
    res = run(prov.handle(AgentRequest(text="weather in Narnia", session_id="w3", **SAC), None))
    assert res.reply == "I heard Narnia, but can't find it. Say it again or type it."


def test_new_route_speaks_the_alert_and_carries_it(agent_sandbox, fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", json=ROUTE)
    fake_http.add("api.weather.gov", "/alerts/active", json={"features": [_alert("Winter Storm Warning")]})
    agent_sandbox.llm.on(r"take me to reno", {"intent": "go", "query": "Reno, NV"})
    agent_sandbox.geocode["reno"] = [RENO]
    res = run(agent_sandbox.provider().handle(AgentRequest(text="take me to Reno", session_id="wr", **SAC), None))
    assert res.status == "route_set"
    assert res.reply.endswith(" Weather warning: Winter Storm Warning ahead near Greater Lake Tahoe Area.")
    assert len(res.route.weather) == 1 and res.route.weather[0].event == "Winter Storm Warning"


def test_route_endpoint_survives_nws_down(api_client, fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", json=ROUTE)
    fake_http.raise_for("api.weather.gov", "/alerts/active", httpx.ConnectError("no"))
    r = api_client.post("/api/route", json={**SAC, "place": RENO.model_dump(), "skip_parking": True})
    assert r.status_code == 200 and r.json()["route"]["weather"] == []
    fake_http.routes.clear()
    weather._alert_cache.clear()
    fake_http.add("router.hereapi.com", "/v8/routes", json=ROUTE)
    fake_http.add("api.weather.gov", "/alerts/active", json={"features": [_alert("High Wind Warning")]})
    r = api_client.post("/api/route", json={**SAC, "place": RENO.model_dump(), "skip_parking": True})
    w = r.json()["route"]["weather"]
    assert len(w) == 1 and set(w[0]) == {"id", "event", "severity", "headline", "area", "index", "ends"}


def test_slow_nws_is_cut_off(agent_sandbox, fake_http, monkeypatch):
    import asyncio

    async def slow(*a, **k):
        await asyncio.sleep(5)
        return []

    monkeypatch.setattr(agent, "WEATHER_BUDGET_S", 0.05)
    monkeypatch.setattr(weather, "alerts_along", slow)
    fake_http.add("router.hereapi.com", "/v8/routes", json=ROUTE)
    agent_sandbox.llm.on(r"take me to reno", {"intent": "go", "query": "Reno, NV"})
    agent_sandbox.geocode["reno"] = [RENO]
    res = run(agent_sandbox.provider().handle(AgentRequest(text="take me to Reno", session_id="slow", **SAC), None))
    assert res.status == "route_set" and "Weather" not in res.reply and res.route.weather == []


@pytest.mark.parametrize("bad", [{"features": [{"properties": {"event": 5}}]}, {"features": [None]}, [], "x"])
def test_malformed_alert_bodies(bad):
    assert parse_alerts(bad if isinstance(bad, dict) else {}) == []
    assert json.dumps(copy.deepcopy(bad)) is not None
