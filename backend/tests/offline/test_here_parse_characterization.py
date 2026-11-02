"""Locks today's HERE parsing on a recorded response so later refactors can't change it unnoticed.

Fixture: here/route_sac_reno.json (Sacramento -> Reno, truck, 3 routes). Only routes[0] is parsed
here; alternatives, speed limits and incidents get their own tests when those features land.
"""
import copy
import math

import flexpolyline
import pytest
from conftest import fixture_json, params_of, run

from models import TruckProfile
from services import routing
from services.errors import NoTruckRoute
from services.routing import MAX_POINTS, _Index, _here_parse, _simplify, _stops_s, _thin, _thin_indices

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
    with pytest.raises(NoTruckRoute, match="Couldn't match origin"):  # HERE answered: no route, not an outage
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
    # Rounded up: 162 in = 411.48 cm -> 412; 73 ft = 2225.04 cm -> 2226; 80,000 lb = 36,287.4 kg -> 36288.
    assert p["vehicle[height]"] == "412" and p["vehicle[length]"] == "2226" and p["vehicle[grossWeight]"] == "36288"
    assert p["vehicle[type]"] == "tractor" and p["vehicle[trailerCount]"] == "1"
    assert p["vehicle[shippedHazardousGoods]"] == routing.HERE_HAZMAT
    assert p["avoid[features]"] == "tollRoad"
    assert "apiKey" not in p  # the key is appended at call time, never part of the pure params


def _line(n, wobble=0.0):
    """n points along a parallel; `wobble` degrees of latitude on every odd point."""
    return [[38.0 + (wobble if i % 2 else 0.0), -121.0 + i * 0.001] for i in range(n)]


def test_straight_line_collapses_to_its_ends_and_anchors_stay():
    pts = _line(2000)
    kept = _thin_indices(pts)
    assert kept == [0, 1999]
    kept = _thin_indices(pts, keep=[5, 1200, 1999, 10 ** 6])
    assert kept == [0, 5, 1200, 1999]
    idx = _Index(kept)
    assert idx(5) == 1 and idx(1200) == 2 and idx(1999) == 3 and idx(7) == 1  # between anchors: the one before
    assert idx(-4) == 0 and idx(10 ** 9) == 3


def test_a_bend_wider_than_the_tolerance_is_kept():
    pts = _line(11, wobble=0.0002)  # ~22 m zigzag: every point matters
    assert _thin_indices(pts) == list(range(11))
    assert len(_simplify(pts, 50.0)) == 2  # at 50 m it is a straight line


def test_the_cap_is_met_by_loosening_the_tolerance():
    pts = _line(MAX_POINTS * 3, wobble=0.0002)
    kept = _thin_indices(pts)
    assert 2 <= len(kept) <= MAX_POINTS
    assert len(_thin(pts)) == len(kept)


def test_maneuvers_limits_and_incidents_sit_on_their_raw_points():
    info = _here_parse(DATA)
    raw = [[round(p[0], 6), round(p[1], 6)] for p in flexpolyline.decode(DATA["routes"][0]["sections"][0]["polyline"])]
    actions = [a for a in DATA["routes"][0]["sections"][0]["actions"] if a["action"] != "depart"]
    for m, a in zip(info.maneuvers, actions):
        assert info.geometry[m.index] == raw[min(a["offset"], len(raw) - 1)]
    for inc in info.incidents:
        assert info.geometry[inc.index] in raw
    assert len(info.geometry) < len(raw) // 2  # a real simplification, not a copy


def test_via_stop_time_counts_in_the_eta():
    two = copy.deepcopy(DATA)
    s = two["routes"][0]["sections"][0]
    second = copy.deepcopy(s)
    second["departure"]["time"] = "2026-10-27T18:11:50-07:00"  # 30 min after the first section's 17:41:50 arrival
    two["routes"][0]["sections"] = [s, second]
    info = _here_parse(two)
    assert info.duration_s == 2 * 10330 + 1800
    assert _stops_s([s, s]) == 0.0  # no gap, no stop
    assert _stops_s([s, {"departure": {}}]) == 0.0  # missing times never break the parse


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


def test_no_route_is_not_an_outage(fake_http, api_client):
    fake_http.add("router.hereapi.com", "/v8/routes", json=fixture_json("here/error_no_route")["body"])
    with pytest.raises(NoTruckRoute):
        run(routing.get_routing_provider().route(38.58, -121.49, 39.53, -119.81, TruckProfile()))
    place = {"id": "x", "name": "Pier 7", "category": "truck_stop", "lat": 39.53, "lng": -119.81}
    r = api_client.post("/api/route", json={"lat": 38.58, "lng": -121.49, "place": place, "skip_parking": True})
    assert r.status_code == 422 and "No truck route" in r.json()["detail"]
