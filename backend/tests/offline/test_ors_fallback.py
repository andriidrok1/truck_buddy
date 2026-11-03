"""Bug 20 (review 2026-11-02): HERE answered "no truck route" and the code still asked OpenRouteService, then
handed its line over as a normal route, without restrictions and without turns ("Route set", then silence).
HERE's "no" is final; ORS only stands in when HERE is down, and then the route says it has no turn prompts."""
import pytest

from models import TruckProfile
from services import agent, routing
from services.errors import NoTruckRoute

from conftest import run

ORS = {"features": [{"geometry": {"coordinates": [[-121.4, 38.5], [-119.8, 39.5]]},
                     "properties": {"summary": {"distance": 1000.0, "duration": 100.0}}}]}
ORS_PATH = "/v2/directions/driving-hgv/geojson"


def test_here_no_route_is_final(fake_http, monkeypatch):
    monkeypatch.setenv("ORS_API_KEY", "x")
    fake_http.add("router.hereapi.com", "/v8/routes", json={"routes": [], "notices": [{"title": "noRouteFound"}]})
    fake_http.add("api.openrouteservice.org", ORS_PATH, json=ORS)
    with pytest.raises(NoTruckRoute):
        run(routing.get_routing_provider().route(38.5, -121.4, 39.5, -119.8, TruckProfile()))
    assert not [c for c in fake_http.calls if "openrouteservice" in c.url.host]


def test_ors_when_here_is_down_says_there_are_no_turn_prompts(fake_http, monkeypatch):
    monkeypatch.setenv("ORS_API_KEY", "x")
    fake_http.add("router.hereapi.com", "/v8/routes", status=503, text="down")
    fake_http.add("api.openrouteservice.org", ORS_PATH, json=ORS)
    info = run(routing.get_routing_provider().route(38.5, -121.4, 39.5, -119.8, TruckProfile()))
    assert info.source == "openrouteservice" and not info.maneuvers
    assert info.notices == [routing.NO_TURNS_NOTICE]
    assert agent._route_warning(info).startswith("Heads up, the truck router is down")
