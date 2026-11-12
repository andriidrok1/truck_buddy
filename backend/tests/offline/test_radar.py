"""Radar proxy for the map's weather view (services/radar.py, services/radar_api.py)."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from conftest import run
from services import radar

HOST = "mesonet.agron.iastate.edu"
_now = datetime.now(timezone.utc)
STAMP = (_now - timedelta(minutes=_now.minute % 5 + 5)).strftime("%Y%m%d%H%M")  # a real, recent 5-minute frame
LIVE = f"n0q-{STAMP}"
PNG = b"\x89PNG\r\n\x1a\nfake"
META = {"model_init_utc": "2026-11-11T22:00:00Z", "forecast_minute": 0.0, "model_forecast_utc": "2026-11-11T22:00:00Z"}
TMS = {"services": [{"id": "ridge_uscomp_n0q", "layername": f"ridge::USCOMP-N0Q-{STAMP}"},
                    {"id": "ridge_uscomp_n0r", "layername": f"ridge::USCOMP-N0R-{STAMP}"}]}


@pytest.fixture(autouse=True)
def _fresh():
    radar.reset_for_tests()
    yield
    radar.reset_for_tests()


def png(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=PNG, headers={"content-type": "image/png"}, request=request)


def test_only_known_layers_map_to_iem():
    assert radar.iem_layer(LIVE) == f"ridge::USCOMP-N0Q-{STAMP}"
    assert radar.iem_layer("hrrr-f060") == "hrrr::REFD-F0060-0"
    assert radar.iem_layer("hrrr-f180") == "hrrr::REFD-F0180-0"
    assert radar.iem_layer("hrrr-f015") == "hrrr::REFD-F0015-0"
    assert radar.iem_layer("hrrr-f1080") == "hrrr::REFD-F1080-0"
    for bad in ("hrrr-f050", "hrrr-f1095", "n0q-2026", "ridge::USCOMP-N0Q-0", "../etc", LIVE + "/../x", ""):
        assert radar.iem_layer(bad) is None


def test_tile_bounds_and_zoom_cap():
    assert radar.tile_ok(0, 0, 0)
    assert radar.tile_ok(10, 1023, 1023)
    assert not radar.tile_ok(11, 0, 0)  # past z10 the map stretches z10 tiles
    assert not radar.tile_ok(3, 8, 0)
    assert not radar.tile_ok(-1, 0, 0)


def test_key_round_trip_and_expiry():
    now = datetime(2026, 11, 11, 23, 59, tzinfo=timezone.utc)
    k = radar.key_for("drv-1", now)
    assert radar.driver_of(k, now) == "drv-1"
    assert radar.driver_of(k, now + timedelta(minutes=5)) == "drv-1"  # handed out at 23:59, still good after midnight
    assert radar.driver_of(k, now + timedelta(days=2)) is None
    day, sub, sig = k.split(".")
    assert radar.driver_of(f"{day}.{sub}.{sig[:-1]}x", now) is None  # forged signature
    other = radar.key_for("drv-2", now).split(".")[1]
    assert radar.driver_of(f"{day}.{other}.{sig}", now) is None  # someone else's id with this signature
    assert radar.driver_of("garbage", now) is None


def test_latest_frame_parsed_and_cached(fake_http):
    fake_http.add(HOST, "/json/tms.json", json=TMS)
    assert run(radar.latest_frame(now=1000.0)) == STAMP
    assert run(radar.latest_frame(now=1050.0)) == STAMP
    assert len(fake_http.calls_to(HOST, "/json")) == 1


def test_frame_failure_keeps_the_last_one(fake_http):
    fake_http.add(HOST, "/json/tms.json", json=TMS, times=1)
    fake_http.add(HOST, "/json/tms.json", status=503, text="down")
    assert run(radar.latest_frame(now=0.0)) == STAMP
    assert run(radar.latest_frame(now=1000.0)) == STAMP


def test_tile_fetched_once_then_cached(fake_http):
    fake_http.add(HOST, "/c/tile.py", fn=png)
    assert run(radar.fetch_tile(LIVE, 6, 10, 24, now=0.0)) == PNG
    assert run(radar.fetch_tile(LIVE, 6, 10, 24, now=10.0)) == PNG
    calls = fake_http.calls_to(HOST, "/c/tile.py")
    assert len(calls) == 1
    assert calls[0].url.path == f"/c/tile.py/1.0.0/ridge::USCOMP-N0Q-{STAMP}/6/10/24.png"


def test_forecast_goes_through_short_cache_endpoint(fake_http):
    fake_http.add(HOST, "/cache/tile.py", fn=png)
    run(radar.fetch_tile("hrrr-f120", 5, 5, 12, now=0.0))
    assert fake_http.calls_to(HOST, "/cache/tile.py")[0].url.path.endswith("/hrrr::REFD-F0120-0/5/5/12.png")


def test_tile_expires(fake_http):
    fake_http.add(HOST, "/c/tile.py", fn=png)
    run(radar.fetch_tile(LIVE, 4, 2, 6, now=0.0))
    run(radar.fetch_tile(LIVE, 4, 2, 6, now=radar.LIVE_TTL_S + 1))
    assert len(fake_http.calls_to(HOST, "/c/tile.py")) == 2


def test_bad_layer_never_reaches_iem(fake_http):
    with pytest.raises(ValueError):
        run(radar.fetch_tile("hrrr-f999", 5, 5, 12))
    with pytest.raises(ValueError):
        run(radar.fetch_tile(LIVE, 12, 0, 0))
    assert fake_http.calls == []


def test_iem_error_is_tile_unavailable(fake_http):
    fake_http.add(HOST, "/c/tile.py", status=503, text="busy")
    with pytest.raises(radar.TileUnavailable):
        run(radar.fetch_tile(LIVE, 6, 10, 24))
    fake_http.add(HOST, "/c/tile.py", text="<html>error</html>")  # 200 but not an image
    radar.reset_for_tests()
    with pytest.raises(radar.TileUnavailable):
        run(radar.fetch_tile(LIVE, 6, 10, 25))


def test_lru_bounded_by_bytes():
    lru = radar._Lru(10)
    lru.put("a", b"12345", 100)
    lru.put("b", b"12345", 100)
    lru.put("c", b"12345", 100)
    assert lru.get("a", 0) is None and lru.get("c", 0) == b"12345"
    assert lru.size == 10


# ---- endpoints ---------------------------------------------------------------------------------

def test_frame_endpoint_hands_out_a_signed_tile_url(api_client, fake_http):
    fake_http.add(HOST, "/json/tms.json", json=TMS)
    fake_http.add(HOST, "/data/gis/images/4326/hrrr/", json=META)
    body = api_client.get("/api/radar/frame").json()
    assert body["enabled"] is True
    assert body["live"] == LIVE
    assert body["time"] == radar.frame_time(STAMP).isoformat()
    assert body["forecast_init"] == "2026-11-11T22:00:00Z"
    assert body["forecast_step_min"] == 15 and body["forecast_max_min"] == 1080
    assert body["tile_url"].startswith("/api/radar/tiles/{layer}/{z}/{x}/{y}.png?k=")


def test_frame_needs_a_token(api_client):
    r = api_client.get("/api/radar/frame", headers={"Authorization": ""})
    assert r.status_code in (401, 403)


def test_tile_endpoint(api_client, fake_http):
    fake_http.add(HOST, "/json/tms.json", json=TMS)
    fake_http.add(HOST, "/data/gis/images/4326/hrrr/", json=META)
    fake_http.add(HOST, "/c/tile.py", fn=png)
    url = api_client.get("/api/radar/frame").json()["tile_url"]
    tile = url.replace("{layer}", LIVE).replace("{z}/{x}/{y}", "6/10/24")
    r = api_client.get(tile, headers={"Authorization": ""})  # the map's tile layer sends no header
    assert r.status_code == 200
    assert r.content == PNG
    assert r.headers["content-type"] == "image/png"
    assert "max-age=3600" in r.headers["cache-control"]


def test_tile_endpoint_refuses_bad_key_and_layers(api_client, fake_http):
    fake_http.add(HOST, "/json/tms.json", json=TMS)
    fake_http.add(HOST, "/data/gis/images/4326/hrrr/", json=META)
    url = api_client.get("/api/radar/frame").json()["tile_url"]
    good = url.replace("{layer}", LIVE).replace("{z}/{x}/{y}", "6/10/24")
    assert api_client.get(good.split("?k=")[0] + "?k=forged.key.x").status_code == 401
    assert api_client.get(good.replace("6/10/24", "13/0/0")).status_code == 404
    assert api_client.get(url.replace("{layer}", "nexrad-n0q").replace("{z}/{x}/{y}", "6/10/24")).status_code == 404
    assert fake_http.calls_to(HOST, "/c/") == []


def test_radar_flag_off(api_client, monkeypatch):
    monkeypatch.setenv("RADAR", "0")
    assert api_client.get("/api/radar/frame").json() == {"enabled": False}


def test_made_up_frame_times_are_refused():
    now = datetime(2026, 11, 12, 1, 0, tzinfo=timezone.utc)
    assert radar.iem_layer("n0q-202611120055", now)  # five minutes ago
    assert radar.iem_layer("n0q-202611120053", now) is None  # off the 5-minute grid
    assert radar.iem_layer("n0q-202611111200", now) is None  # 13 h old
    assert radar.iem_layer("n0q-999912312355", now) is None  # the future
    assert radar.iem_layer("n0q-202613450000", now) is None  # not a date


def test_non_ascii_key_is_refused_not_a_crash():
    assert radar.driver_of("20261111.YQ.\u00e9") is None


def test_failed_tile_not_refetched_for_a_minute(fake_http):
    fake_http.add(HOST, "/c/tile.py", status=503, text="busy")
    for t in (0.0, 30.0):
        with pytest.raises(radar.TileUnavailable):
            run(radar.fetch_tile(LIVE, 6, 10, 24, now=t))
    assert len(fake_http.calls_to(HOST, "/c/tile.py")) == 1


def test_upstream_budget_shared_by_everyone(fake_http, monkeypatch):
    fake_http.add(HOST, "/c/tile.py", fn=png)
    monkeypatch.setattr(radar, "UPSTREAM_PER_MIN", 3)
    radar._upstream.update(tokens=3.0, at=0.0)
    for y in range(3):
        run(radar.fetch_tile(LIVE, 6, 10, y, now=0.0))
    with pytest.raises(radar.TileUnavailable):
        run(radar.fetch_tile(LIVE, 6, 10, 9, now=0.0))
    run(radar.fetch_tile(LIVE, 6, 10, 0, now=0.0))  # a cached tile still comes back


def test_tile_endpoint_bad_key_is_401(api_client):
    r = api_client.get(f"/api/radar/tiles/{LIVE}/6/10/24.png", params={"k": "20261111.YQ.\u00e9"})
    assert r.status_code == 401
def test_hrrr_run_cached_and_kept_on_failure(fake_http):
    fake_http.add(HOST, "/data/gis/images/4326/hrrr/", json=META, times=1)
    fake_http.add(HOST, "/data/gis/images/4326/hrrr/", status=503, text="down")
    assert run(radar.hrrr_init(now=0.0)) == "2026-11-11T22:00:00Z"
    assert run(radar.hrrr_init(now=10.0)) == "2026-11-11T22:00:00Z"
    assert len(fake_http.calls_to(HOST, "/data/")) == 1
    assert run(radar.hrrr_init(now=1000.0)) == "2026-11-11T22:00:00Z"  # IEM down: the last run we knew


def test_forecast_tiles_cached_per_run(fake_http):
    fake_http.add(HOST, "/cache/tile.py", fn=png)
    radar._hrrr.update(init="2026-11-11T22:00:00Z")
    run(radar.fetch_tile("hrrr-f060", 4, 2, 6, now=0.0))
    run(radar.fetch_tile("hrrr-f060", 4, 2, 6, now=1.0))
    radar._hrrr.update(init="2026-11-11T23:00:00Z")  # a new run: the same layer name shows other weather
    run(radar.fetch_tile("hrrr-f060", 4, 2, 6, now=2.0))
    assert len(fake_http.calls_to(HOST, "/cache/tile.py")) == 2
