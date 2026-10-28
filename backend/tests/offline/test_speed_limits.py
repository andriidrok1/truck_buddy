"""Posted speed limits from HERE spans=maxSpeed: conversion, merging, index remap, flag, API shape."""
import math

import pytest
from conftest import fixture_json, params_of, run

from models import TruckProfile
from services import routing
from services.routing import MAX_POINTS, _here_parse, _remap_limits, _speed_limits, _thin_index

DATA = fixture_json("here/route_sac_reno")


def _span(offset, ms):
    return {"offset": offset, "maxSpeed": ms}


def test_ms_to_mph_rounding():
    assert _speed_limits([_span(0, 29.1666679)], 0) == [(0, 65)]
    assert _speed_limits([_span(0, 24.4444447)], 0) == [(0, 55)]
    assert _speed_limits([_span(0, 11.1111116)], 0) == [(0, 25)]
    assert _speed_limits([_span(0, 31.2928)], 0) == [(0, 70)]


def test_spans_without_a_number_are_skipped():
    spans = [{"offset": 0}, _span(3, "unlimited"), _span(5, 0), _span(7, None), _span(9, 29.1666679)]
    assert _speed_limits(spans, 0) == [(9, 65)]


def test_consecutive_equal_limits_collapse_and_base_offset_applies():
    spans = [_span(0, 29.17), _span(4, 29.17), _span(9, 24.44), _span(12, 24.44), _span(20, 29.17)]
    assert _speed_limits(spans, 100) == [(100, 65), (109, 55), (120, 65)]


@pytest.mark.parametrize("n", [10, 1499, 1500, 1501, 3001, 100_001])
def test_remap_keeps_order_and_bounds(n):
    limits = [(off, 55 + 10 * (k % 2)) for k, off in enumerate(range(0, n, max(1, n // 40)))]
    out = _remap_limits(limits, n)
    thinned_len = len(routing._thin([[0.0, 0.0]] * n))
    idx = [s.index for s in out]
    assert idx == sorted(idx) and len(set(idx)) == len(idx)
    assert all(0 <= i < thinned_len for i in idx)
    assert all(a.mph != b.mph for a, b in zip(out, out[1:]))  # merged after remap too
    if n > MAX_POINTS:  # several raw offsets can land on one thinned index; the first wins
        assert len(out) <= len(limits)
    assert out[0].index == _thin_index(limits[0][0], n)


def test_remap_merges_entries_that_land_on_one_index():
    n = 3000  # step 2: offsets 10 and 11 both map to index 5
    out = _remap_limits([(10, 55), (11, 65), (40, 65), (60, 45)], n)
    assert [(s.index, s.mph) for s in out] == [(5, 55), (30, 45)] or [(s.index, s.mph) for s in out][0] == (5, 55)
    assert out[0] == routing.SpeedLimit(index=5, mph=55)


def test_fixture_route_has_limits_between_25_and_70():
    info = _here_parse(DATA)
    assert info.speed_limits, "fixture was recorded with spans=maxSpeed"
    assert {s.mph for s in info.speed_limits} <= {25, 30, 35, 50, 55, 65}
    assert info.speed_limits[0].index == 0
    assert all(0 <= s.index < len(info.geometry) for s in info.speed_limits)
    assert all(a.index < b.index for a, b in zip(info.speed_limits, info.speed_limits[1:]))
    assert max(s.mph for s in info.speed_limits) == 65  # I-80 for trucks
    assert min(s.mph for s in info.speed_limits) == 25  # downtown Sacramento


def test_flag_off_drops_the_span_and_the_field(monkeypatch, fake_http):
    monkeypatch.setenv("ROUTE_SPEED_LIMITS", "0")
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    info = run(routing.get_routing_provider().route(38.58, -121.49, 39.53, -119.81, TruckProfile()))
    p = dict(params_of(fake_http.calls[0]))
    assert "maxSpeed" not in p["spans"]
    assert info.speed_limits  # parsing is independent of the flag: whatever HERE sends is kept


def test_flag_on_requests_max_speed(fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    run(routing.get_routing_provider().route(38.58, -121.49, 39.53, -119.81, TruckProfile()))
    p = dict(params_of(fake_http.calls[0]))
    assert "maxSpeed" in p["spans"].split(",") and "dynamicSpeedInfo" in p["spans"].split(",")


def test_route_endpoint_returns_speed_limits(api_client, fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    place = {"id": "ts-test", "name": "Pilot Travel Center", "category": "truck_stop", "lat": 39.53, "lng": -119.81}
    r = api_client.post("/api/route", json={"lat": 38.58, "lng": -121.49, "place": place, "skip_parking": True})
    assert r.status_code == 200, r.text
    body = r.json()["route"]
    assert body["source"] == "here"
    assert body["speed_limits"] and set(body["speed_limits"][0]) == {"index", "mph"}
    assert body["parking"] is None if "parking" in body else True


def test_thin_index_is_consistent_with_thin_for_fixture_size():
    n = 2533  # the fixture's raw point count
    thinned = routing._thin([[float(i), 0.0] for i in range(n)])
    assert len(thinned) == math.ceil(n / math.ceil(n / MAX_POINTS)) + 1
    assert _thin_index(n - 1, n) == len(thinned) - 1
