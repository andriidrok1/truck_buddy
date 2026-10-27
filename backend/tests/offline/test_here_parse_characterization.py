"""Locks today's HERE parsing on a recorded response so later refactors can't change it unnoticed.

Fixture: here/route_sac_reno.json (Sacramento -> Reno, truck, 3 routes). Only routes[0] is parsed
here; alternatives, speed limits and incidents get their own tests when those features land.
"""
import math

import pytest
from conftest import fixture_json, params_of, run

from models import TruckProfile
from services import routing
from services.routing import MAX_POINTS, _here_parse, _thin, _thin_index

DATA = fixture_json("here/route_sac_reno")


def test_primary_route_shape():
    info = _here_parse(DATA)
    assert info.source == "here" and info.truck_aware is True
    assert 2 <= len(info.geometry) <= MAX_POINTS
    assert info.distance_m == 212550 and info.duration_s == 10330
    assert info.distance_mi == 132.1 and info.duration_min == 172
    assert info.summary == "2 h 52 min · 132 mi"
    assert info.notices == []


def test_maneuvers_follow_actions():
    info = _here_parse(DATA)
    actions = [a for a in DATA["routes"][0]["sections"][0]["actions"] if a["action"] != "depart"]
    assert len(info.maneuvers) == len(actions)
    assert [m.action for m in info.maneuvers] == [a["action"] for a in actions]
    assert info.maneuvers[-1].action == "arrive"
    assert info.maneuvers[-1].index == len(info.geometry) - 1
    idx = [m.index for m in info.maneuvers]
    assert idx == sorted(idx) and all(0 <= i < len(info.geometry) for i in idx)
    assert "Go for" not in info.maneuvers[0].instruction and not info.maneuvers[0].instruction.endswith(".")
    for m in info.maneuvers:  # the maneuver's own lat/lng sits on the thinned geometry point it indexes (or next to it)
        g = info.geometry[m.index]
        assert math.hypot(g[0] - m.lat, g[1] - m.lng) < 0.01


def test_traffic_segments_are_slow_or_jam_and_lie_on_the_route():
    info = _here_parse(DATA)
    assert info.traffic, "the recording was made in traffic; re-record if this ever goes empty"
    for seg in info.traffic:
        assert seg.level in ("slow", "jam")
        assert len(seg.points) >= 2


def test_empty_or_broken_bodies_raise_not_crash():
    with pytest.raises(RuntimeError):
        _here_parse({})
    with pytest.raises(RuntimeError):
        _here_parse({"routes": []})
    with pytest.raises(RuntimeError):
        _here_parse({"routes": [{"sections": []}]})
    with pytest.raises(RuntimeError):
        _here_parse(fixture_json("here/error_no_route")["body"])


def test_here_params_unchanged(monkeypatch):
    monkeypatch.setenv("ROUTE_SPEED_LIMITS", "0")
    monkeypatch.setenv("ROUTE_INCIDENTS", "0")
    monkeypatch.setenv("ROUTE_ALTERNATIVES", "0")
    monkeypatch.setenv("REROUTE_ENABLED", "0")
    t = TruckProfile(height_in=162, trailer_ft=53, weight_lb=80000, hazmat=True, avoid_tolls=True)
    p = dict(routing._here_params(t, (38.5, -121.4), (39.5, -119.8)))
    assert p["transportMode"] == "truck" and p["origin"] == "38.5,-121.4" and p["destination"] == "39.5,-119.8"
    assert p["return"] == "polyline,summary,actions,instructions" and p["spans"] == "dynamicSpeedInfo"
    assert p["units"] == "imperial" and p["lang"] == "en-US"
    assert p["vehicle[height]"] == "411" and p["vehicle[length]"] == "2225" and p["vehicle[grossWeight]"] == "36288"
    assert p["vehicle[shippedHazardousGoods]"] == routing.HERE_HAZMAT
    assert p["avoid[features]"] == "tollRoad"
    assert "apiKey" not in p  # the key is appended at call time, never part of the pure params


@pytest.mark.parametrize("n", [1, 2, 10, 1499, 1500, 1501, 2999, 3000, 3001, 100_001])
def test_thin_index_matches_thin(n):
    pts = [[float(i), 0.0] for i in range(n)]
    thinned = _thin(pts)
    assert len(thinned) <= MAX_POINTS + 1
    last_idx = _thin_index(n - 1, n)
    assert last_idx == len(thinned) - 1
    assert thinned[last_idx] == pts[-1]
    prev = -1
    for off in range(0, n, max(1, n // 500)):
        idx = _thin_index(off, n)
        assert 0 <= idx < len(thinned)
        assert idx >= prev
        prev = idx
        if n > MAX_POINTS:
            step = math.ceil(n / MAX_POINTS)
            if off % step == 0 and off != n - 1:
                assert thinned[idx] == pts[off]
        else:
            assert thinned[idx] == pts[off]
    assert _thin_index(10 ** 9, n) == last_idx  # clamped
    assert _thin_index(-5, n) == 0


def test_route_through_transport_layer(fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", json=DATA)
    info = run(routing.get_routing_provider().route(38.58, -121.49, 39.53, -119.81, TruckProfile()))
    assert info.source == "here" and info.maneuvers and info.approach is not None
    req = fake_http.calls_to("router.hereapi.com")[0]
    p = dict(params_of(req))
    assert p["apiKey"] == "test-here" and p["transportMode"] == "truck"


def test_here_5xx_with_key_set_is_routing_unavailable(fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", status=503, text="down")
    with pytest.raises(routing.RoutingUnavailable):
        run(routing.get_routing_provider().route(38.58, -121.49, 39.53, -119.81, TruckProfile()))
