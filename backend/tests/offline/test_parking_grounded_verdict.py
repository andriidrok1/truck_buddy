"""Eval 2026-11-02: the grounded check gives the verdict and, with OSM driveways on the frame, the entry; the
bare-image call gives the parking spot; when one of them fails the other answers alone."""
import asyncio
from datetime import datetime, timezone

import pytest

from models import ParkingSpot, Place
from services import osm_access, parking

P = Place(id="t", name="Test Stop", category="truck_stop", lat=38.5, lng=-121.5, tags=[])
E_BARE = ParkingSpot(kind="entry", x=0.1, y=0.1, lat=38.6, lng=-121.6)
E_MAP = ParkingSpot(kind="entry", x=0.2, y=0.2, lat=38.5, lng=-121.5)
PARK = ParkingSpot(kind="park", x=0.5, y=0.5, lat=38.5, lng=-121.5)
BARE = {"fits": "likely", "reason": "big lot", "entry_tip": "Use the east driveway", "park_tip": "Park out back",
        "spots": [E_BARE, PARK], "nearby": None}
GROUNDED = {"fits": "unlikely", "reason": "car-only lot", "entry_tip": "Use the west driveway off Main St",
            "park_tip": None, "spots": [E_MAP], "usage": {}, "samples": [], "markers": 3}


def _stub(monkeypatch, bare, grounded):
    async def ask(value):
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(parking, "_ask_bare", lambda img, place: ask(bare))
    monkeypatch.setattr(parking, "_ask_grounded", lambda place: ask(grounded))


def test_verdict_and_entry_from_grounded_park_from_bare(monkeypatch):
    _stub(monkeypatch, BARE, GROUNDED)
    v = asyncio.run(parking._ask_vision(b"", P))
    assert (v["fits"], v["reason"]) == ("unlikely", "car-only lot")
    assert (v["entry_tip"], v["park_tip"], v["spots"]) == ("Use the west driveway off Main St", "Park out back",
                                                           [E_MAP, PARK])


def test_no_map_driveways_no_entry(monkeypatch):
    _stub(monkeypatch, BARE, {**GROUNDED, "markers": 0, "entry_tip": "Use the north driveway"})
    v = asyncio.run(parking._ask_vision(b"", P))
    assert (v["entry_tip"], v["spots"]) == (None, [PARK])


def test_grounded_failure_keeps_the_bare_verdict_without_entry(monkeypatch):
    _stub(monkeypatch, BARE, TimeoutError())
    v = asyncio.run(parking._ask_vision(b"", P))
    assert (v["fits"], v["park_tip"], v["entry_tip"], v["spots"]) == ("likely", "Park out back", None, [PARK])


def test_bare_failure_keeps_the_grounded_check(monkeypatch):
    _stub(monkeypatch, RuntimeError("openai"), GROUNDED)
    v = asyncio.run(parking._ask_vision(b"", P))
    assert v["fits"] == "unlikely" and v["nearby"] is None


def test_both_fail_raises(monkeypatch):
    _stub(monkeypatch, RuntimeError("openai"), TimeoutError())
    with pytest.raises(RuntimeError):
        asyncio.run(parking._ask_vision(b"", P))


def test_slow_overpass_gives_no_markers_and_keeps_loading(monkeypatch):
    done = asyncio.Event()

    async def slow_fetch(lat, lng, timeout=60):
        await done.wait()
        return {"elements": []}
    monkeypatch.setattr(osm_access, "fetch", slow_fetch)
    monkeypatch.setattr(osm_access, "_points", {})

    async def go():
        assert await osm_access.around(1.0, 2.0, 0.01) == []
        done.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return await osm_access.around(1.0, 2.0, 0.01)
    assert asyncio.run(go()) == []
    assert osm_access._points == {"1.00000,2.00000": []}


def test_osm_points_survive_a_restart_through_the_store(monkeypatch):
    rows = {}

    async def put(name, payload):
        rows[name] = (payload, datetime.now(timezone.utc))

    async def get(name):
        return rows.get(name)

    async def fetch(lat, lng, timeout=60):
        calls.append((lat, lng))
        return {"elements": [
            {"type": "node", "id": 1, "lat": 1.0, "lon": 2.0}, {"type": "node", "id": 2, "lat": 1.0005, "lon": 2.0},
            {"type": "way", "id": 10, "nodes": [1, 3], "tags": {"highway": "primary", "name": "Main St"}},
            {"type": "way", "id": 11, "nodes": [1, 2], "tags": {"highway": "service"}}]}
    calls = []
    monkeypatch.setattr(osm_access.kv, "feed_put", put)
    monkeypatch.setattr(osm_access.kv, "feed_get", get)
    monkeypatch.setattr(osm_access, "fetch", fetch)
    monkeypatch.setattr(osm_access, "_points", {})
    first = asyncio.run(osm_access.around(1.0, 2.0, 5))
    assert [a.road for a in first] == ["Main St"] and len(calls) == 1
    monkeypatch.setattr(osm_access, "_points", {})  # restart: memory gone, store kept
    assert asyncio.run(osm_access.around(1.0, 2.0, 5)) == first and len(calls) == 1
