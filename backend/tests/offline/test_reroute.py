"""Re-routing after a deviation: HERE route handle first, fresh route as the fallback, POST /api/reroute."""
import httpx
import pytest
from conftest import fixture_json, params_of, run

from models import Place, TruckProfile
from services import routing
from services.routing import _here_parse, _reroute_params

ROUTE = fixture_json("here/route_sac_reno")
BY_HANDLE = fixture_json("here/reroute_ok")
BAD_HANDLE = fixture_json("here/reroute_handle_bad")
HANDLE = ROUTE["routes"][0]["routeHandle"]
AUBURN = (38.9100, -121.0700)  # just off I-80, heading east
RENO = (39.5296, -119.8138)
PLACE = Place(id="here-reno", name="Reno, NV", category="destination", lat=RENO[0], lng=RENO[1])


def _prov():
    return routing.get_routing_provider()


def test_routes_carry_a_handle_and_the_flag_requests_it(fake_http, monkeypatch):
    fake_http.add("router.hereapi.com", "/v8/routes", json=ROUTE)
    info = run(_prov().route(38.58, -121.49, *RENO, TruckProfile()))
    assert info.handle == HANDLE and all(a.handle for a in info.alternatives)
    assert "routeHandle" in dict(params_of(fake_http.calls[0]))["return"].split(",")
    monkeypatch.setenv("REROUTE_ENABLED", "0")
    run(_prov().route(38.58, -121.49, *RENO, TruckProfile()))
    assert "routeHandle" not in dict(params_of(fake_http.calls[1]))["return"]
    assert _here_parse({"routes": [{**ROUTE["routes"][0], "routeHandle": ""}]}).handle is None


def test_reroute_params_shape():
    t = TruckProfile(hazmat=True, avoid_tolls=True)
    p = _reroute_params(t, AUBURN, 80, 0, 50000)
    d = dict(p)
    assert d["origin"] == "38.91,-121.07;course=80"
    assert "destination" not in d and "alternatives" not in d and "currency" not in d
    ret = d["return"].split(",")
    assert {"polyline", "summary", "actions", "instructions", "incidents", "routeHandle"} <= set(ret)
    assert "routeLabels" not in ret and "tolls" not in ret
    assert d["rerouting[mode]"] == "returnToRoute"
    assert d["rerouting[lastTraveledSectionIndex]"] == "0" and d["rerouting[traveledDistanceOnLastSection]"] == "50000"
    assert d["vehicle[shippedHazardousGoods]"] == routing.HERE_HAZMAT and d["avoid[features]"] == "tollRoad"
    assert dict(_reroute_params(t, AUBURN, None, 2, 12.7))["origin"] == "38.91,-121.07"
    assert dict(_reroute_params(t, AUBURN, None, 2, 12.7))["rerouting[traveledDistanceOnLastSection]"] == "12"


def test_reroute_by_handle_hits_the_handle_endpoint_only(fake_http):
    fake_http.add("router.hereapi.com", f"/v8/routes/{HANDLE}", json=BY_HANDLE)
    info = run(_prov().reroute(HANDLE, *AUBURN, 80, *RENO, TruckProfile(), section_index=0, traveled_m=50000))
    assert info.source == "here" and info.handle and info.maneuvers and info.approach is not None
    assert info.alternatives == [] and info.distance_m == 159659
    assert len(fake_http.calls) == 1 and fake_http.calls[0].url.path == f"/v8/routes/{HANDLE}"
    assert dict(params_of(fake_http.calls[0]))["origin"].endswith(";course=80")


def test_expired_handle_falls_back_to_a_fresh_route(fake_http):
    fake_http.add("router.hereapi.com", f"/v8/routes/{HANDLE}", status=BAD_HANDLE["status"], json=BAD_HANDLE["body"])
    fake_http.add("router.hereapi.com", "/v8/routes", json=ROUTE)
    info = run(_prov().reroute(HANDLE, *AUBURN, 80, *RENO, TruckProfile()))
    assert info.source == "here" and info.alternatives == []  # fresh, but no alternatives on a re-route
    paths = [c.url.path for c in fake_http.calls]
    assert paths == [f"/v8/routes/{HANDLE}", "/v8/routes"]
    fresh = dict(params_of(fake_http.calls[1]))
    assert fresh["origin"] == "38.91,-121.07" and fresh["destination"] == f"{RENO[0]},{RENO[1]}"
    assert "alternatives" not in fresh and "rerouting[mode]" not in fresh


def test_network_error_on_the_handle_also_falls_back(fake_http):
    fake_http.raise_for("router.hereapi.com", f"/v8/routes/{HANDLE}", httpx.ReadTimeout("slow"))
    fake_http.add("router.hereapi.com", "/v8/routes", json=ROUTE)
    info = run(_prov().reroute(HANDLE, *AUBURN, None, *RENO, TruckProfile()))
    assert info.source == "here" and len(fake_http.calls) == 2


def test_no_handle_or_flag_off_goes_straight_to_a_fresh_route(fake_http, monkeypatch):
    fake_http.add("router.hereapi.com", "/v8/routes", json=ROUTE)
    run(_prov().reroute(None, *AUBURN, 80, *RENO, TruckProfile()))
    assert [c.url.path for c in fake_http.calls] == ["/v8/routes"]
    monkeypatch.setenv("REROUTE_ENABLED", "0")
    run(_prov().reroute(HANDLE, *AUBURN, 80, *RENO, TruckProfile()))
    assert [c.url.path for c in fake_http.calls] == ["/v8/routes", "/v8/routes"]


def test_everything_down_is_routing_unavailable(fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes/", status=503, text="down")
    fake_http.add("router.hereapi.com", "/v8/routes", status=503, text="down")
    with pytest.raises(routing.RoutingUnavailable):
        run(_prov().reroute(HANDLE, *AUBURN, 80, *RENO, TruckProfile()))


def _body(**kw):
    return {"lat": AUBURN[0], "lng": AUBURN[1], "place": PLACE.model_dump(), "handle": HANDLE, "heading": 80,
            "section_index": 0, "traveled_m": 50000, **kw}


def test_reroute_endpoint_ok(api_client, fake_http):
    fake_http.add("router.hereapi.com", f"/v8/routes/{HANDLE}", json=BY_HANDLE)
    r = api_client.post("/api/reroute", json=_body())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["place"]["id"] == PLACE.id and body["route"]["source"] == "here" and body["route"]["handle"]
    assert body["route"]["alternatives"] == [] and body["route"]["maneuvers"]
    assert "parking" in body  # cached verdict or null; never a new satellite check


def test_reroute_endpoint_falls_back_and_fails_honestly(api_client, fake_http):
    fake_http.add("router.hereapi.com", f"/v8/routes/{HANDLE}", status=400, json=BAD_HANDLE["body"])
    fake_http.add("router.hereapi.com", "/v8/routes", status=503, text="down")
    r = api_client.post("/api/reroute", json=_body())
    assert r.status_code == 503


def test_reroute_endpoint_validation(api_client, fake_http):
    assert api_client.post("/api/reroute", json=_body(lat=48.86, lng=2.35)).status_code == 422  # Paris
    assert api_client.post("/api/reroute", json=_body(heading=400)).status_code == 422
    assert api_client.post("/api/reroute", json=_body(traveled_m=-1)).status_code == 422
    assert api_client.post("/api/reroute", json={"lat": 1}).status_code == 422
    assert fake_http.calls == []


def test_reroute_endpoint_without_handle(api_client, fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", json=ROUTE)
    r = api_client.post("/api/reroute", json=_body(handle=None, heading=None))
    assert r.status_code == 200 and r.json()["route"]["alternatives"] == []
    assert fake_http.calls[0].url.path == "/v8/routes"
