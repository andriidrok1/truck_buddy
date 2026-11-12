"""Every route that can reach a paid provider (Google Places, HERE, OpenAI STT/LLM/TTS, ElevenLabs, Gemini,
satellite tiles + vision) sits behind the driver token. Offline twin of the live TestProtection in
tests/test_iter21_auth_memory.py, so a new route on `public` (or a new router without the dependency) fails CI."""
from fastapi.routing import APIRoute

from services import auth

# Public on purpose: liveness (?deep=1 needs a tester token inside), sign-in, and the reply audio that the
# player opens by URL without headers (it only serves clips already made behind the token).
PUBLIC = {
    ("GET", "/api/health"),
    ("POST", "/api/auth/access-code"),
    ("POST", "/api/auth/device"),
    ("POST", "/api/auth/refresh"),
    ("GET", "/api/voice/audio/{audio_id}"),
}


def _calls(dependant) -> set:
    out = set()
    for d in dependant.dependencies:
        out.add(d.call)
        out |= _calls(d)
    return out


def _routes():
    import server

    for r in server.app.routes:
        if isinstance(r, APIRoute):
            for m in r.methods:
                yield m, r.path, r


def test_every_non_public_route_needs_the_driver_token():
    open_routes = sorted((m, p) for m, p, r in _routes()
                         if (m, p) not in PUBLIC and auth.require_driver not in _calls(r.dependant))
    assert open_routes == []


def test_public_list_is_current():
    """A public route that was removed or renamed must leave the list too."""
    assert PUBLIC <= {(m, p) for m, p, _ in _routes()}


def test_places_401_without_token_200_with(api_client):
    url = "/api/places?category=truck_stop&lat=38.58&lng=-121.49"
    assert api_client.get(url, headers={"Authorization": ""}).status_code == 401
    r = api_client.get(url)
    assert r.status_code == 200
    assert "places" in r.json()
