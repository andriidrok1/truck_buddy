"""Eval 2026-11-02: the grounded check gives the verdict, the bare-image call still gives the directions; when
one of them fails the other answers alone."""
import asyncio

import pytest

from models import Place
from services import osm_access, parking

P = Place(id="t", name="Test Stop", category="truck_stop", lat=38.5, lng=-121.5, tags=[])
BARE = {"fits": "likely", "reason": "big lot", "entry_tip": "Use the east driveway", "park_tip": "Park out back",
        "spots": ["entry"], "nearby": None}
GROUNDED = {"fits": "unlikely", "reason": "car-only lot", "entry_tip": None, "park_tip": None, "spots": [],
            "usage": {}, "samples": []}


def _stub(monkeypatch, bare, grounded):
    async def ask(value):
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(parking, "_ask_bare", lambda img, place: ask(bare))
    monkeypatch.setattr(parking, "_ask_grounded", lambda place: ask(grounded))


def test_verdict_from_grounded_directions_from_bare(monkeypatch):
    _stub(monkeypatch, BARE, GROUNDED)
    v = asyncio.run(parking._ask_vision(b"", P))
    assert (v["fits"], v["reason"]) == ("unlikely", "car-only lot")
    assert (v["entry_tip"], v["spots"]) == ("Use the east driveway", ["entry"])


def test_grounded_failure_keeps_the_bare_check(monkeypatch):
    _stub(monkeypatch, BARE, TimeoutError())
    assert asyncio.run(parking._ask_vision(b"", P)) == BARE


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
