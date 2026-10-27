"""Caltrans chain controls: parsing, route matching, caching/degradation, the spoken line, route wiring."""
import copy

import httpx
import pytest
from conftest import fixture_json, run

from models import AgentRequest, ChainControl, Place
from services import chains
from services.chains import along_route, parse, speech
from services.routing import _here_parse

D03 = fixture_json("caltrans/ccStatusD03")
ROUTE = fixture_json("here/route_sac_reno")
SAC_SF = fixture_json("here/route_sac_sf")
SAC = {"lat": 38.5816, "lng": -121.4944}
RENO = Place(id="here-reno", name="Reno, NV", category="destination", lat=39.5296, lng=-119.8138)
NYACK_E, KINGVALE_E, LUTHER = "3-PLA-80-54.7-E-186", "3-PLA-80-69.22-E-190", "3-ALP-89-23.6-N-237"


def setup_function():
    chains._cache.clear()


def _storm() -> dict:
    """The recorded D3 feed with a winter evening painted on it: R-1 at Nyack, R-2 at Kingvale, R-3 on SR-89."""
    data = copy.deepcopy(D03)
    want = {NYACK_E: "R-1", NYACK_E + "a": "R-1", KINGVALE_E: "R-2", KINGVALE_E + "a": "R-2", LUTHER: "R-3"}
    for row in data["data"]:
        cc = row["cc"]
        if cc["index"] in want:
            cc["statusData"]["status"] = want[cc["index"]]
            cc["statusData"]["statusDescription"] = "Chains are required"
    return data


def test_parse_recorded_feed():
    controls = parse(D03)
    assert len(controls) == 230 and all(c.status == "R-0" for c in controls)
    luther = next(c for c in controls if c.id == LUTHER)
    assert luther.route == "SR-89" and luther.location == "Luther Pass" and luther.nearby == "Markleeville"
    assert (luther.lat, luther.lng) == (38.787944, -119.93968) and luther.direction == "North"
    assert luther.updated == "2026-05-05 05:58:27" and luther.in_service and luther.index == -1


def test_parse_is_tolerant():
    assert parse({}) == [] and parse(None) == [] and parse({"data": "x"}) == [] and parse({"data": [None, 1]}) == []
    rows = [{"cc": {"index": "a", "location": {"latitude": "x", "longitude": "1"}, "statusData": {"status": "R-1"}}},
            {"cc": {"index": "b", "location": {"latitude": "39", "longitude": "-120"}, "statusData": {}}},
            {"cc": {"index": "c", "location": {"latitude": "39", "longitude": "-120", "route": "I-80"},
                    "statusData": {"status": "r-2"}, "inService": "false"}}]
    out = parse({"data": rows})
    assert [c.id for c in out] == ["c"] and out[0].status == "R-2" and out[0].in_service is False
    assert out[0].location == "I-80"


def test_along_route_finds_active_controls_in_order_and_dedupes():
    controls = parse(_storm())
    g = _here_parse(ROUTE).geometry
    hits = along_route(g, controls)
    assert [(c.location, c.status) for c in hits] == [("Nyack", "R-1"), ("Kingvale", "R-2")]
    assert 0 < hits[0].index < hits[1].index < len(g)
    assert all(c.id not in (LUTHER,) for c in hits)  # SR-89 is 60 km off the line
    assert along_route(_here_parse(SAC_SF).geometry, controls) == []
    assert along_route(g, parse(D03)) == []  # all R-0
    assert along_route([], controls) == []


def test_out_of_service_and_r0_are_ignored():
    on = ChainControl(id="1", route="I-80", location="Kingvale", lat=39.3142, lng=-120.4461, status="R-2")
    off = on.model_copy(update={"id": "2", "in_service": False})
    none = on.model_copy(update={"id": "3", "status": "R-0"})
    g = _here_parse(ROUTE).geometry
    assert [c.id for c in along_route(g, [on, off, none])] == ["1"]


def test_speech_lines():
    base = dict(route="I-80", location="Kingvale", lat=39.3, lng=-120.4)
    r1 = ChainControl(id="1", status="R-1", **base)
    r2 = ChainControl(id="2", status="R-2", **base)
    r3 = ChainControl(id="3", status="R-3", **base)
    assert speech([]) == ""
    assert speech([r1]) == "Chains or snow tires required on I-80 near Kingvale."
    assert speech([r2]) == "Heads up, chains required on I-80 near Kingvale."
    assert speech([r3]) == "Heads up, chains required on every vehicle, I-80 near Kingvale."
    assert speech([r1, r2]) == ("Chains or snow tires required on I-80 near Kingvale. "
                                "And 1 more chain control along the route.")
    assert speech([r2, r1, r3]).endswith(" And 2 more chain controls along the route.")


def test_fetch_district_caches_and_keeps_the_last_good_copy(fake_http):
    fake_http.add("cwwp2.dot.ca.gov", "/data/d3/cc/ccStatusD03.json", json=_storm(), times=1)
    fake_http.add("cwwp2.dot.ca.gov", "/data/d3/cc/ccStatusD03.json", status=500, text="boom")
    first = run(chains.fetch_district(3, now=0.0))
    assert len(first) == 230
    assert run(chains.fetch_district(3, now=100.0)) is first  # cached
    stale = run(chains.fetch_district(3, now=chains.TTL_S + 1))  # refresh fails: last good copy
    assert stale is first and len(fake_http.calls) == 2
    chains._cache.clear()
    assert run(chains.fetch_district(3, now=0.0)) == []  # nothing ever fetched, feed down
    fake_http.routes.clear()
    fake_http.raise_for("cwwp2.dot.ca.gov", "/data/", httpx.ReadTimeout("slow"))
    assert run(chains.fetch_district(10, now=0.0)) == []
    fake_http.routes.clear()
    fake_http.add("cwwp2.dot.ca.gov", "/data/", text="<html>", status=200)
    assert run(chains.fetch_district(10, now=0.0)) == []


def test_districts_env_parsing(monkeypatch):
    monkeypatch.delenv("CHAIN_DISTRICTS", raising=False)
    assert chains.districts() == [2, 3, 6, 9, 10]
    monkeypatch.setenv("CHAIN_DISTRICTS", "3, 10, x, 99, 8")
    assert chains.districts() == [3, 10, 8]


def _mock_caltrans(fake_http, d3=None):
    storm = d3 or _storm()

    def respond(request: httpx.Request) -> httpx.Response:
        body = storm if "/d3/" in request.url.path else {"data": []}
        return httpx.Response(200, json=body, request=request)

    fake_http.add("cwwp2.dot.ca.gov", "/data/", fn=respond)


def test_for_route_only_touches_caltrans_for_california(fake_http, monkeypatch):
    _mock_caltrans(fake_http)
    texas = [[32.7, -96.8], [33.0, -97.0], [33.5, -97.3]]
    assert run(chains.for_route(texas)) == [] and fake_http.calls == []
    hits = run(chains.for_route(_here_parse(ROUTE).geometry))
    assert [c.location for c in hits] == ["Nyack", "Kingvale"]
    assert len(fake_http.calls) == 5  # one per default district, then cached
    assert run(chains.for_route(_here_parse(ROUTE).geometry)) and len(fake_http.calls) == 5
    monkeypatch.setenv("CHAIN_CONTROLS", "0")
    assert run(chains.for_route(_here_parse(ROUTE).geometry)) == []


def test_new_route_speaks_the_chain_line_and_carries_it(agent_sandbox, fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", json=ROUTE)
    fake_http.add("api.weather.gov", "/alerts/active", json={"features": []})
    _mock_caltrans(fake_http)
    agent_sandbox.llm.on(r"take me to reno", {"intent": "go", "query": "Reno, NV"})
    agent_sandbox.geocode["reno"] = [RENO]
    res = run(agent_sandbox.provider().handle(AgentRequest(text="take me to Reno", session_id="ch", **SAC), None))
    assert res.status == "route_set"
    assert res.reply.endswith("Chains or snow tires required on I-80 near Nyack. And 1 more chain control along the "
                              "route.")
    assert [c.status for c in res.route.chain_controls] == ["R-1", "R-2"]


def test_route_endpoint_carries_chain_controls_and_survives_caltrans_down(api_client, fake_http):
    fake_http.add("router.hereapi.com", "/v8/routes", json=ROUTE)
    fake_http.add("api.weather.gov", "/alerts/active", json={"features": []})
    _mock_caltrans(fake_http)
    r = api_client.post("/api/route", json={**SAC, "place": RENO.model_dump(), "skip_parking": True})
    assert r.status_code == 200
    cc = r.json()["route"]["chain_controls"]
    assert [c["location"] for c in cc] == ["Nyack", "Kingvale"] and cc[0]["index"] > 0
    chains._cache.clear()
    fake_http.routes.clear()
    fake_http.add("router.hereapi.com", "/v8/routes", json=ROUTE)
    fake_http.add("api.weather.gov", "/alerts/active", json={"features": []})
    fake_http.raise_for("cwwp2.dot.ca.gov", "/data/", httpx.ConnectError("no"))
    r = api_client.post("/api/route", json={**SAC, "place": RENO.model_dump(), "skip_parking": True})
    assert r.status_code == 200 and r.json()["route"]["chain_controls"] == []


def test_poller_respects_the_flag(monkeypatch):
    import asyncio

    async def check(flag):
        monkeypatch.setenv("CHAIN_CONTROLS", flag)
        chains._task = None
        await chains.start()
        running = chains._task is not None
        await chains.stop()
        return running

    assert run(check("0")) is False
    monkeypatch.setattr(chains, "refresh_all", lambda: asyncio.sleep(0))
    assert run(check("1")) is True and chains._task is None


@pytest.mark.parametrize("status,expected", [("R-1", "Chains or snow tires"),
                                             ("R-2", "Heads up, chains required on I-80"),
                                             ("R-9", "Heads up, chains required on I-80")])
def test_unknown_status_falls_back_to_the_r2_wording(status, expected):
    c = ChainControl(id="x", route="I-80", location="Kingvale", lat=0, lng=0, status=status)
    assert speech([c]).startswith(expected)
