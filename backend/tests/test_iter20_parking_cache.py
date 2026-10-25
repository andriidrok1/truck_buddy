"""Parking verdicts on disk + the single re-check for verdicts without an entry point."""
import asyncio
import json

import pytest

from models import ParkingCheck, ParkingSpot, Place
from services import parking, parking_store

P = Place(id="t", name="Test Stop", category="truck_stop", lat=40.12345, lng=-120.54321)
K = parking._key(P.lat, P.lng)
E = [ParkingSpot(kind="entry", x=0.5, y=0.5, lat=40.1235, lng=-120.5432)]


def chk(spots=()):
    return ParkingCheck(verdict="likely", label="l", reason="r", source="satellite", image_path="/s", spots=list(spots))


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(parking_store, "DIR", tmp_path)
    monkeypatch.setattr(parking_store, "_mem", {})
    parking._verdict_cache.pop(K, None)
    yield tmp_path
    parking._verdict_cache.pop(K, None)


def test_write_then_read_back(tmp_store):
    parking_store.save(K, chk(E), retried=False)
    f = next(tmp_store.glob("*.json"))
    d = json.loads(f.read_text())
    assert d["key"] == K and d["retried"] is False and d["check"]["spots"][0]["kind"] == "entry"
    parking_store._mem.clear()  # "restart"
    loaded = parking_store.load_all()
    assert loaded[K].spots[0].lat == E[0].lat
    assert parking_store.stats() == {"total": 1, "with_entry": 1}


def test_cached_parking_falls_back_to_disk():
    parking_store.save(K, chk(E), retried=False)
    assert K not in parking._verdict_cache
    assert parking.cached_parking(P).verdict == "likely"  # memory miss -> disk
    assert K in parking._verdict_cache  # and promoted to memory


def test_needs_retry_only_without_entry_and_once():
    parking_store.save(K, chk(E), retried=False)
    assert not parking_store.needs_retry(K)
    parking_store.save(K, chk(), retried=False)
    assert parking_store.needs_retry(K)
    parking_store.save(K, chk(), retried=True)
    assert not parking_store.needs_retry(K)


def test_single_retry_then_fixed(monkeypatch):
    calls = []

    async def fake_check(place, force=False):
        calls.append(force)
        return chk()  # vision still finds no entry

    monkeypatch.setattr(parking, "_check_parking", fake_check)
    parking_store.save(K, chk(), retried=False)

    async def run():
        await parking.retry_once(P)
        await parking.retry_once(P)  # already retried: no second vision call
        await parking.check_parking(P)  # cached, final: no background retry either
        await asyncio.sleep(0)
    asyncio.run(run())
    assert calls == [True]
    assert parking_store._mem[K][1] is True


def test_retry_keeps_the_verdict_with_entry(monkeypatch):
    async def fake_check(place, force=False):
        return chk(E)

    monkeypatch.setattr(parking, "_check_parking", fake_check)
    parking_store.save(K, chk(), retried=False)
    res = asyncio.run(parking.retry_once(P))
    assert parking_store.has_entry(res) and parking_store.has_entry(parking_store.get(K))


def test_warm_up_skips_saved_and_rechecks_once(monkeypatch):
    from services import parking_warm, places
    monkeypatch.setattr(places, "MOCK_PLACES", [P.model_dump()])
    calls = []

    async def fake_check(place, force=False):
        calls.append(force)
        if not force:  # like the real one: a first check is saved (not final yet)
            parking_store.save(K, chk(), retried=False)
        return chk()

    monkeypatch.setattr(parking, "_check_parking", fake_check)
    s = asyncio.run(parking_warm.warm_demo_places())  # first check + its one re-check
    assert calls == [False, True] and s == {"total": 1, "with_entry": 0}
    asyncio.run(parking_warm.warm_demo_places())  # on disk and final: nothing to do
    assert calls == [False, True]
