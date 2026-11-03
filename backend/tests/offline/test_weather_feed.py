"""The feed path for route alerts: one all-US NWS feed (cached, stale-while-revalidate), zone polygons fetched
once, every route vertex tested; the point path as the fallback when the feed or a zone is unavailable."""
import asyncio

import httpx
from conftest import fixture_json, run

from services import weather
from services.weather import first_index_inside, point_in_rings

ROUTE = fixture_json("here/route_sac_reno")
ZONE = "https://api.weather.gov/zones/forecast/CAZ069"
ZONE2 = "https://api.weather.gov/zones/forecast/NVZ002"


def _geometry():
    from services.routing import _here_parse
    return _here_parse(ROUTE).geometry


def _box(lat0, lat1, lng0=-123.0, lng1=-118.0):
    return {"type": "Polygon", "coordinates": [[[lng0, lat0], [lng1, lat0], [lng1, lat1], [lng0, lat1], [lng0, lat0]]]}


def _feature(event, zones=None, geometry=None, **props):
    p = {"id": f"urn:{event}", "event": event, "severity": "Severe", "status": "Actual", "messageType": "Alert",
         "headline": event, "areaDesc": "Greater Lake Tahoe Area", "affectedZones": zones or [], **props}
    return {"type": "Feature", "properties": p, "geometry": geometry}


def _is_feed(request: httpx.Request) -> bool:
    return request.url.path == "/alerts/active" and "point" not in dict(request.url.params)


# ---- geometry ----

def test_point_in_rings_with_a_hole():
    outer = [(-121.0, 38.0), (-119.0, 38.0), (-119.0, 40.0), (-121.0, 40.0), (-121.0, 38.0)]
    hole = [(-120.5, 38.5), (-119.5, 38.5), (-119.5, 39.5), (-120.5, 39.5), (-120.5, 38.5)]
    assert point_in_rings(-120.8, 38.2, [outer]) and not point_in_rings(-122.0, 39.0, [outer])
    assert not point_in_rings(-120.0, 39.0, [outer, hole])  # in the hole
    assert point_in_rings(-120.8, 39.0, [outer, hole])
    assert not point_in_rings(-120.0, 39.0, [])


def test_first_index_inside_is_the_first_vertex_in_the_polygon():
    g = _geometry()
    rings = weather._rings(_box(39.4, 41.0))
    idx = first_index_inside(g, rings)
    assert idx > 0 and g[idx][0] >= 39.4 and all(p[0] < 39.4 for p in g[:idx])
    assert first_index_inside(g, weather._rings(_box(45.0, 46.0))) == -1
    assert first_index_inside(g, []) == -1
    multi = {"type": "MultiPolygon", "coordinates": [_box(45.0, 46.0)["coordinates"], _box(39.4, 41.0)["coordinates"]]}
    assert first_index_inside(g, weather._rings(multi)) == idx
    assert weather._rings({"type": "Point", "coordinates": [1, 2]}) == [] and weather._rings(None) == []


# ---- the feed and the zones ----

def test_feed_and_zones_place_the_alerts_without_point_calls(fake_http):
    feed = {"features": [_feature("Winter Storm Warning", zones=[ZONE]),
                         _feature("High Wind Warning", geometry=_box(39.0, 39.3), areaDesc="Foothills"),
                         _feature("Rip Current Statement", zones=[ZONE2]),  # not a truck event: no zone fetch
                         _feature("Winter Storm Warning", zones=[ZONE], messageType="Cancel", id="urn:cancel")]}
    fake_http.add("api.weather.gov", "/alerts/active", json=feed)
    fake_http.add("api.weather.gov", "/zones/forecast/CAZ069", json={"geometry": _box(39.4, 41.0)})
    fake_http.add("api.weather.gov", "/zones/forecast/NVZ002", status=500)
    g = _geometry()
    alerts = run(weather.alerts_along(g))
    assert [a.event for a in alerts] == ["High Wind Warning", "Winter Storm Warning"]
    assert 0 < alerts[0].index < alerts[1].index and alerts[1].area == "Greater Lake Tahoe Area"
    paths = [c.url.path for c in fake_http.calls_to("api.weather.gov")]
    assert paths == ["/alerts/active", "/zones/forecast/CAZ069"]  # one feed, one zone, no point lookups
    assert dict(fake_http.calls[0].url.params) == {"status": "actual", "message_type": "alert,update"}
    # Another route, same feed and zone: no new calls at all.
    south = [p for p in g if p[0] < 38.9]  # the stretch below both polygons, however the line is thinned
    assert run(weather.alerts_along(south)) == [] and len(fake_http.calls_to("api.weather.gov")) == 2


def test_a_missing_zone_keeps_what_the_feed_placed_and_adds_the_point_path(fake_http):
    """One dead zone file somewhere must not throw away the alerts the feed could place (review 2026-11-01)."""
    def respond(request: httpx.Request) -> httpx.Response:
        if _is_feed(request):
            return httpx.Response(200, json={"features": [
                _feature("Winter Storm Warning", zones=[ZONE]),  # zone unavailable
                _feature("High Wind Warning", geometry=_box(39.0, 39.3), areaDesc="Foothills")]}, request=request)
        return httpx.Response(200, json={"features": []}, request=request)  # the point path finds nothing more

    fake_http.add("api.weather.gov", "/alerts/active", fn=respond)
    fake_http.raise_for("api.weather.gov", "/zones/forecast/CAZ069", httpx.ReadTimeout("slow"))
    alerts = run(weather.alerts_along(_geometry()))
    assert [a.event for a in alerts] == ["High Wind Warning"]  # placed by the feed, kept
    assert any("point" in dict(c.url.params) for c in fake_http.calls_to("api.weather.gov", "/alerts/active"))
    # After ZONE_GIVE_UP failures the zone is written off as empty and stops forcing the point path.
    for _ in range(weather.ZONE_GIVE_UP):
        run(weather.alerts_along(_geometry()))
    assert weather._zone_cache[ZONE] == []
    before = len(fake_http.calls)
    run(weather.alerts_along(_geometry()[:100]))
    assert len(fake_http.calls) == before  # no point lookups any more


def test_a_missing_zone_polygon_falls_back_to_point_lookups(fake_http):
    def respond(request: httpx.Request) -> httpx.Response:
        if _is_feed(request):
            return httpx.Response(200, json={"features": [_feature("Winter Storm Warning", zones=[ZONE])]}, request=request)
        lat = float(dict(request.url.params)["point"].split(",")[0])
        feats = [_feature("Winter Storm Warning", zones=[ZONE])] if lat > 39.4 else []
        return httpx.Response(200, json={"features": feats}, request=request)

    fake_http.add("api.weather.gov", "/alerts/active", fn=respond)
    fake_http.raise_for("api.weather.gov", "/zones/forecast/CAZ069", httpx.ReadTimeout("slow"))
    alerts = run(weather.alerts_along(_geometry()))
    assert [a.event for a in alerts] == ["Winter Storm Warning"] and alerts[0].index > 0
    assert any("point" in dict(c.url.params) for c in fake_http.calls_to("api.weather.gov", "/alerts/active"))
    assert weather._zone_cache[ZONE] is None and weather._zone_fail[ZONE] == 1  # retried on the next lookup


def test_feed_down_falls_back_to_point_lookups(fake_http):
    def respond(request: httpx.Request) -> httpx.Response:
        if _is_feed(request):
            return httpx.Response(503, text="down", request=request)
        return httpx.Response(200, json={"features": [_feature("Dense Fog Advisory", severity="Moderate")]}, request=request)

    fake_http.add("api.weather.gov", "/alerts/active", fn=respond)
    alerts = run(weather.alerts_along(_geometry()))
    assert [a.event for a in alerts] == ["Dense Fog Advisory"] and alerts[0].index == 0


def test_stale_feed_is_served_while_it_refreshes(fake_http, monkeypatch):
    fake_http.add("api.weather.gov", "/alerts/active", json={"features": [_feature("High Wind Warning", geometry=_box(37.0, 41.0))]})
    g = _geometry()
    assert [a.event for a in run(weather.alerts_along(g, now=1000.0))] == ["High Wind Warning"]
    fake_http.routes.clear()
    fake_http.add("api.weather.gov", "/alerts/active", json={"features": []})
    # Within the TTL: the cached feed, no call.
    assert [a.event for a in run(weather.alerts_along(g, now=1000.0 + weather.FEED_TTL_S - 1))] == ["High Wind Warning"]
    assert len(fake_http.calls) == 1
    # Past the TTL: the stale feed now, a refresh in the background.
    assert [a.event for a in run(weather.alerts_along(g, now=1000.0 + weather.FEED_TTL_S + 1))] == ["High Wind Warning"]
    run(asyncio.sleep(0.05))
    assert len(fake_http.calls) == 2 and weather._feed_cache["alerts"] == []
    # Too stale to serve: fetched in line.
    weather._feed_cache["at"] = 0.0
    assert run(weather.alerts_along(g, now=weather.FEED_STALE_MAX_S + 5.0)) == [] and len(fake_http.calls) == 3


def test_warm_fetches_the_feed_and_the_zones(fake_http):
    fake_http.add("api.weather.gov", "/alerts/active", json={"features": [_feature("Winter Storm Warning", zones=[ZONE])]})
    fake_http.add("api.weather.gov", "/zones/forecast/CAZ069", json={"geometry": _box(39.4, 41.0)})
    run(weather.warm())
    assert weather._feed_cache["alerts"] and ZONE in weather._zone_cache and weather._zone_cache[ZONE]
    assert run(weather.alerts_along(_geometry()))[0].event == "Winter Storm Warning"
    assert len(fake_http.calls) == 2  # nothing more for the route


def test_warm_survives_a_dead_nws(fake_http):
    fake_http.raise_for("api.weather.gov", "/alerts/active", httpx.ConnectError("no"))
    run(weather.warm())
    assert "alerts" not in weather._feed_cache
