"""/api/warm prefetches Places only: opening the map must never start a paid satellite (vision) check."""
import asyncio

import pytest


@pytest.fixture
def no_vision(monkeypatch):
    """Every way into the satellite check fails the test if reached; returns the searched categories."""
    import server
    from services import parking

    def boom(*_a, **_k):
        raise AssertionError("warm-up started a satellite parking check")

    monkeypatch.setattr(server, "check_parking", boom)
    monkeypatch.setattr(parking, "check_parking", boom)
    monkeypatch.setattr(parking, "_check_parking", boom)
    monkeypatch.setattr(parking, "fetch_satellite", boom)

    searched: list[str] = []
    found: list[int] = []
    real = server.get_places_provider()

    class Spy:
        async def search(self, category, lat, lng):
            searched.append(category)
            res = await real.search(category, lat, lng)
            found.append(len(res.places))  # places came back: the old code would have checked the first one
            return res

    monkeypatch.setattr(server, "get_places_provider", lambda: Spy())
    yield searched
    assert found and all(found), found


def test_warm_searches_places_without_satellite(no_vision):
    import server

    async def go():
        await server._warm(38.5816, -121.4944)  # Sacramento: demo places in every category
        await asyncio.sleep(0)  # anything it scheduled in the background gets a chance to run
        await asyncio.gather(*list(server._BG), return_exceptions=False)

    asyncio.run(go())
    assert sorted(no_vision) == ["hotel", "rest_area", "truck_stop"]


def test_warm_endpoint_schedules_no_vision(api_client, no_vision, monkeypatch):
    import server

    scheduled = []
    monkeypatch.setattr(server, "_bg", lambda coro: scheduled.append(coro))
    monkeypatch.setattr(server, "_WARM_LAST", {})
    r = api_client.post("/api/warm", params={"lat": 38.5816, "lng": -121.4944})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert len(scheduled) == 1

    async def go():
        for c in scheduled:
            await c

    asyncio.run(go())
    assert sorted(no_vision) == ["hotel", "rest_area", "truck_stop"]
