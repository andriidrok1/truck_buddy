"""Weather at route points at the ETA (services/route_weather.py)."""
import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from conftest import fixture_json, run
from services import route_weather as rw
from services import weather

NWS = "api.weather.gov"
T0 = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _fresh():
    rw.clear_caches()
    weather._points_cache.clear()
    yield
    rw.clear_caches()
    weather._points_cache.clear()


def series(*vals, uom=None, hours=1, start=T0):
    """[(value, hours?) ...] -> an NWS series starting at `start`."""
    out, t = [], start
    for v in vals:
        v, h = v if isinstance(v, tuple) else (v, hours)
        out.append({"validTime": f"{t.isoformat()}/PT{h}H", "value": v})
        t += timedelta(hours=h)
    return {"uom": uom, "values": out}


def grid(**kw):
    base = {"temperature": series(10), "windGust": series(20), "windSpeed": series(10), "visibility": series(16000),
            "snowfallAmount": series(0), "iceAccumulation": series(0), "quantitativePrecipitation": series(0),
            "skyCover": series(10), "weather": {"values": [{"validTime": f"{T0.isoformat()}/PT24H", "value": [{"weather": None}]}]}}
    base.update(kw)
    return base


def wx(word):
    return {"values": [{"validTime": f"{T0.isoformat()}/PT24H", "value": [{"coverage": "likely", "weather": word, "intensity": "moderate"}]}]}


# ---- parsing ------------------------------------------------------------------------------------

def test_valid_time_durations():
    assert rw.parse_valid_time("2026-11-11T15:00:00+00:00/PT3H") == (T0, T0 + timedelta(hours=3))
    assert rw.parse_valid_time("2026-11-11T15:00:00+00:00/P1DT10H")[1] == T0 + timedelta(days=1, hours=10)
    assert rw.parse_valid_time("2026-11-11T15:00:00+00:00/PT30M")[1] == T0 + timedelta(minutes=30)
    for bad in ("", "2026-11-11T15:00:00+00:00", "x/PT1H", "2026-11-11T15:00:00+00:00/P"):
        assert rw.parse_valid_time(bad) is None


def test_value_at_picks_the_covering_period():
    s = series((1, 3), (2, 6))
    assert rw.value_at(s, T0 + timedelta(hours=2)) == 1
    assert rw.value_at(s, T0 + timedelta(hours=3)) == 2
    assert rw.value_at(s, T0 + timedelta(hours=10)) is None
    assert rw.value_at(None, T0) is None


def test_total_between_is_pro_rata():
    s = series((6, 6))  # 6 mm over 6 h
    assert rw.total_between(s, T0 + timedelta(hours=3), T0 + timedelta(hours=9)) == pytest.approx(3.0)


def test_real_grid_reads_at_an_eta():
    g = fixture_json("nws/gridpoints_donner")["properties"]
    p = rw.read_point(g, T0 + timedelta(minutes=30), None)
    assert p["temp_f"] == 36  # 2.22 °C
    assert p["gust_mph"] == 10  # 16.7 km/h
    assert p["vis_mi"] == 10.0
    assert p["kind"] == "clear" and p["severity"] == "info" and not p["ice_risk"]


# ---- what the chip says -------------------------------------------------------------------------

def test_near_freezing_after_rain_is_ice_risk():
    g = grid(temperature=series(1, hours=24), quantitativePrecipitation=series((4, 6), start=T0 - timedelta(hours=6)))
    p = rw.read_point(g, T0 + timedelta(hours=1), None)
    assert p["ice_risk"] and p["kind"] == "ice" and p["severity"] == "warn" and p["reasons"] == ["ice"]


def test_cold_and_dry_is_not_ice():
    p = rw.read_point(grid(temperature=series(-5, hours=24)), T0 + timedelta(hours=1), None)
    assert not p["ice_risk"] and p["kind"] == "clear"


def test_forecast_ice_or_freezing_rain_is_ice_even_above_35():
    assert rw.read_point(grid(temperature=series(4), iceAccumulation=series(1)), T0, None)["ice_risk"]
    assert rw.read_point(grid(temperature=series(4), weather=wx("freezing_rain")), T0, None)["kind"] == "ice"


def test_snow():
    p = rw.read_point(grid(temperature=series(-3), snowfallAmount=series(25)), T0, None)
    assert p["kind"] == "ice"  # snow at 27 °F lands on the road: ice risk outranks the snow icon
    p = rw.read_point(grid(temperature=series(3), weather=wx("snow")), T0, None)
    assert p["kind"] == "snow" and p["severity"] == "info"


def test_gusts_matter_more_for_an_empty_trailer():
    g = grid(windGust=series(72))  # 45 mph
    assert rw.read_point(g, T0, trailer_empty=True)["reasons"] == ["gusts"]
    assert rw.read_point(g, T0, trailer_empty=True)["kind"] == "wind"
    assert rw.read_point(g, T0, trailer_empty=False)["severity"] == "info"
    assert rw.read_point(g, T0, trailer_empty=None)["severity"] == "info"
    assert rw.read_point(grid(windGust=series(97)), T0, None)["reasons"] == ["gusts"]  # 60 mph: any truck


def test_fog_and_low_visibility():
    p = rw.read_point(grid(visibility=series(800)), T0, None)  # half a mile
    assert p["kind"] == "fog" and p["reasons"] == ["low_visibility"] and p["vis_mi"] == 0.5


def test_rain_and_cloud():
    assert rw.read_point(grid(weather=wx("rain")), T0, None)["kind"] == "rain"
    assert rw.read_point(grid(skyCover=series(80)), T0, None)["kind"] == "cloud"


# ---- NWS calls ---------------------------------------------------------------------------------

def _nws(fake_http, grid_status=200):
    fake_http.add(NWS, "/points/", json=fixture_json("nws/points_donner"))
    if grid_status == 200:
        fake_http.add(NWS, "/gridpoints/", json=fixture_json("nws/gridpoints_donner"))
    else:
        fake_http.add(NWS, "/gridpoints/", status=grid_status, text="down")


def _req(n=2, **kw):
    pts = [rw.PointRequest(index=i * 10, lat=39.32 + i * 0.3, lng=-120.33, eta=T0 + timedelta(minutes=30 * i)) for i in range(n)]
    return rw.PointsRequest(points=pts, **kw)


def test_points_come_back_in_route_order_with_the_town(fake_http):
    _nws(fake_http)
    out = run(rw.points(_req(3), now=0.0))
    assert [p.index for p in out] == [0, 10, 20]
    assert out[0].place == "Truckee, CA" and out[0].temp_f == 36


def test_grid_is_cached(fake_http):
    _nws(fake_http)
    run(rw.points(_req(1), now=0.0))
    run(rw.points(_req(1), now=60.0))
    assert len(fake_http.calls_to(NWS, "/gridpoints/")) == 1


def test_a_failed_grid_drops_the_point_not_the_answer(fake_http):
    _nws(fake_http, grid_status=503)
    assert run(rw.points(_req(2), now=0.0)) == []


def test_budget_returns_what_finished(fake_http, monkeypatch):
    _nws(fake_http)

    async def slow(p, empty, now):
        if p.index:
            await asyncio.sleep(5)
        return rw.RoutePoint(index=p.index, lat=p.lat, lng=p.lng, eta=p.eta)

    monkeypatch.setattr(rw, "one_point", slow)
    out = run(rw.points(_req(2), budget_s=0.2, now=0.0))
    assert [p.index for p in out] == [0]


def test_too_many_points_refused():
    with pytest.raises(Exception):
        rw.PointsRequest(points=[rw.PointRequest(index=i, lat=39, lng=-120, eta=T0) for i in range(rw.MAX_POINTS + 1)])


def test_endpoint(api_client, fake_http):
    _nws(fake_http)
    body = {"points": [{"index": 0, "lat": 39.32, "lng": -120.33, "eta": T0.isoformat()}], "trailer_empty": True}
    r = api_client.post("/api/weather/points", json=body)
    assert r.status_code == 200
    p = r.json()["points"][0]
    assert p["place"] == "Truckee, CA" and p["kind"] == "clear" and p["severity"] == "info"


def test_endpoint_needs_a_token(api_client):
    r = api_client.post("/api/weather/points", json={"points": []}, headers={"Authorization": ""})
    assert r.status_code in (401, 403)


_ = httpx
