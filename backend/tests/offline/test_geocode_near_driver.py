"""QA 2026-11-05 M-2: "Boomtown" near Sacramento/Reno is the Boomtown Casino truck stop at Verdi, NV (I-80 exit 4).
HERE's geocoder answered "Boomtown, NV" with Boom Town Dr in Las Vegas (street, score 0.99, 396 miles) and that won
before Discover was read. A place's name now goes to the matching place near the driver; a far match is taken only
when nothing of that name is close, and the agent still asks "Is that the one?" about it."""
import httpx
from conftest import fixture_json, run

from models import AgentRequest
from services import agent, geocode
from services.geocode import name_query

SAC = (38.5816, -121.4944)
TRUCKEE = (39.3280, -120.1833)
VERDI = (39.5219, -119.9339)  # Boomtown's access point


def _here(fake_http, geo="geocode_boomtown", disc="discover_boomtown", around="discover_empty"):
    def discover(request: httpx.Request):
        circle = request.url.params.get("in", "").startswith("circle:")
        return httpx.Response(200, json=fixture_json(f"here/{around if circle else disc}"), request=request)
    fake_http.add("geocode.search.hereapi.com", "/v1/geocode", json=fixture_json(f"here/{geo}") if geo else {"items": []})
    fake_http.add("discover.search.hereapi.com", "/v1/discover", fn=discover)


def _around_calls(fake_http):
    return [c for c in fake_http.calls_to("discover.search.hereapi.com") if c.url.params.get("in", "").startswith("circle:")]


def test_name_query():
    for q in ("Boomtown, NV", "Boomtown", "the 49er", "Pilot", "Jubitz, OR"):
        assert name_query(q), q
    for q in ("2828 El Centro Rd", "Boom Town Dr, Las Vegas", "W Capitol Ave", "I-80 exit 4", "Interstate eighty"):
        assert not name_query(q), q


def test_boomtown_from_sacramento_is_the_casino_at_verdi_not_the_street_in_vegas(fake_http):
    _here(fake_http)
    found = run(geocode.geocode("Boomtown, NV", *SAC))
    assert len(found) == 1 and found[0].name == "Boomtown Casino Hotel", found
    assert (found[0].lat, found[0].lng) == VERDI and found[0].distance_mi == 106.0
    assert found[0].address == "2100 Garson Rd, Verdi, NV 89439, United States"
    assert _around_calls(fake_http) == []  # the first answer was already close


def test_boomtown_from_truckee_is_the_one_down_the_road(fake_http):
    _here(fake_http)
    found = run(geocode.geocode("Boomtown", *TRUCKEE))
    assert found and found[0].name == "Boomtown Casino Hotel" and found[0].distance_mi < 20


def test_when_the_country_wide_search_misses_it_the_area_around_the_driver_is_asked(fake_http):
    _here(fake_http, disc="discover_boomtown_far_only", around="discover_boomtown")
    found = run(geocode.geocode("Boomtown, NV", *SAC))
    assert found and found[0].name == "Boomtown Casino Hotel"
    (call,) = _around_calls(fake_http)
    assert call.url.params["in"] == f"circle:{SAC[0]},{SAC[1]};r=320000" and "at" not in call.url.params


def test_a_name_that_only_exists_far_away_still_resolves(fake_http):
    _here(fake_http, disc="discover_boomtown_far_only")
    found = run(geocode.geocode("Boomtown, NV", *SAC))
    assert found and found[0].name.startswith("Boom Town Dr") and found[0].distance_mi > 300
    assert len(_around_calls(fake_http)) == 1


def test_a_street_the_driver_named_stays_the_street(fake_http):
    _here(fake_http)
    found = run(geocode.geocode("Boom Town Dr, Las Vegas", *SAC))
    assert found and found[0].name.startswith("Boom Town Dr") and found[0].id.startswith("here-street-")
    assert _around_calls(fake_http) == []


def test_nothing_found_asks_no_more(fake_http):
    _here(fake_http, geo=None, disc="discover_empty")
    assert run(geocode.geocode("Zzqxv", *SAC)) == []
    assert _around_calls(fake_http) == []


def _agent(agent_sandbox, fake_http, monkeypatch, phrase, query):
    fake_http.add("router.hereapi.com", "/v8/routes", json=fixture_json("here/route_sac_reno"))
    agent_sandbox.llm.on(rf"take me to {phrase}$", {"intent": "go", "query": query})
    prov = agent_sandbox.provider()
    monkeypatch.setattr(agent, "geocode", geocode.geocode)  # the real ranking over the HERE fixtures
    return prov


def test_the_driver_is_asked_about_boomtown_at_verdi(agent_sandbox, fake_http, monkeypatch):
    _here(fake_http)
    prov = _agent(agent_sandbox, fake_http, monkeypatch, "boomtown", "Boomtown, NV")
    res = run(prov.handle(AgentRequest(text="take me to boomtown", session_id="bt1", lat=SAC[0], lng=SAC[1]), None))
    assert res.status == "confirm"
    assert res.reply == "I found Boomtown Casino Hotel on Garson Road in Verdi, Nevada, about 106 miles away. Is that the one?"


def test_a_far_only_place_is_read_back_before_the_route(agent_sandbox, fake_http, monkeypatch):
    _here(fake_http, geo=None, disc="discover_jubitz")
    prov = _agent(agent_sandbox, fake_http, monkeypatch, "jubitz", "Jubitz, OR")
    res = run(prov.handle(AgentRequest(text="take me to jubitz", session_id="bt2", lat=SAC[0], lng=SAC[1]), None))
    assert res.status == "confirm" and res.route is None
    assert res.reply == ("I found Jubitz Travel Center on North Vancouver Way in Portland, Oregon, about 489 miles away. "
                         "Is that the one?")
    assert len(_around_calls(fake_http)) == 1 and fake_http.calls_to("router.hereapi.com") == []
