"""/api/warm is a no-op kept for 1.0 builds: opening the map must not start anything paid (Places, satellite)."""
import pytest


@pytest.fixture
def nothing_paid(monkeypatch):
    """Every paid path the old warm-up took fails the test if reached."""
    import server
    from services import parking, places

    def boom(*_a, **_k):
        raise AssertionError("/api/warm reached a paid call")

    class Boom:
        def __getattr__(self, _name):
            return boom

    monkeypatch.setattr(server, "check_parking", boom)
    monkeypatch.setattr(parking, "check_parking", boom)
    monkeypatch.setattr(parking, "_check_parking", boom)
    monkeypatch.setattr(parking, "fetch_satellite", boom)
    monkeypatch.setattr(server, "get_places_provider", lambda: Boom())
    monkeypatch.setattr(places, "get_places_provider", lambda: Boom())
    monkeypatch.setattr(server, "_bg", boom)  # nothing goes to the background either


def test_warm_is_a_noop(api_client, nothing_paid):
    for _ in range(3):  # every call, not just the first one per driver
        r = api_client.post("/api/warm", params={"lat": 38.5816, "lng": -121.4944})
        assert r.status_code == 200 and r.json() == {"ok": True}


def test_warm_still_validates(api_client, nothing_paid):
    assert api_client.post("/api/warm", params={"lat": 999, "lng": -121.4944}).status_code == 422
