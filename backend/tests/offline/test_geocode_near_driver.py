"""QA 2026-11-05 M-2: "Boomtown" near Sacramento/Reno is the Boomtown Casino truck stop at Verdi, NV (I-80 exit 4).
HERE's geocoder answered "Boomtown, NV" with Boom Town Dr in Las Vegas (street, score 0.99, 396 miles) and that won
before Discover was read. A place's name now goes to the matching place near the driver; a far match is taken only
when nothing of that name is close, and the agent still asks "Is that the one?" about it."""
import httpx
from conftest import fixture_json, run

from models import AgentRequest
from services import agent, geocode
from services.geocode import _location, _overlap, name_query, picked_nearby

SAC = (38.5816, -121.4944)
TRUCKEE = (39.3280, -120.1833)
VERDI = (39.5219, -119.9339)  # Boomtown's access point
RENO = (39.5296, -119.8138)
MEDFORD = (42.3265, -122.8756)


def _here(fake_http, geo="geocode_boomtown", disc="discover_boomtown", around="discover_empty"):
    def discover(request: httpx.Request):
        circle = request.url.params.get("in", "").startswith("circle:")
        if circle and isinstance(around, Exception):
            raise around
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


def test_state_words_and_filler_carry_no_weight():
    assert _overlap("Jubitz, OR", "Petro Medford, 5250 Biddle Rd, Central Point, OR 97502") == 0
    assert _overlap("Boomtown Nevada", "Boom Town Dr, Las Vegas, NV 89122") == 0
    assert _overlap("Boomtown Nevada", "Boomtown Casino Hotel, 2100 Garson Rd, Verdi, NV") == 1
    assert _overlap("Washington", "Washington Ave, Reno, NV") == 1  # a lone state word is still the name
    assert _location("Flying J in Ely") == ({"ely"}, None)
    assert _location("Jubitz, OR") == (set(), "or") and _location("Boomtown Nevada") == (set(), "nv")
    assert _location("Pilot, Fernley NV") == ({"fernley"}, "nv")


def test_the_town_the_driver_named_beats_a_nearer_one_of_the_chain(fake_http):
    _here(fake_http, geo=None, disc="discover_flyingj_ely")
    found = run(geocode.geocode("Flying J in Ely", *RENO))
    assert found and "Ely, NV" in found[0].address and found[0].distance_mi > 200, found
    assert _around_calls(fake_http) == []  # the driver said where it is


def test_jubitz_from_medford_is_portland_never_the_petro_next_door(fake_http):
    _here(fake_http, geo=None, disc="discover_jubitz", around="discover_around_medford")
    found = run(geocode.geocode("Jubitz, OR", *MEDFORD))
    assert found and found[0].name == "Jubitz Travel Center", found
    assert len(_around_calls(fake_http)) == 1  # asked around Medford, nothing there carries the name


def test_boomtown_with_the_state_spelled_out_is_still_verdi(fake_http):
    _here(fake_http, disc="discover_boomtown_far_only", around="discover_boomtown")
    found = run(geocode.geocode("Boomtown Nevada", *SAC))
    assert found and found[0].name == "Boomtown Casino Hotel" and picked_nearby(found[0])


def test_a_slow_search_around_the_driver_falls_back_to_the_far_answer(fake_http):
    _here(fake_http, disc="discover_boomtown_far_only", around=httpx.ReadTimeout("slow"))
    found = run(geocode.geocode("Boomtown, NV", *SAC))
    assert found and found[0].name.startswith("Boom Town Dr")
    (call,) = _around_calls(fake_http)
    assert geocode.AROUND_TIMEOUT_S == 3.0 and call.extensions["timeout"]["read"] == 3.0


def test_elm_swapped_for_a_diner_is_read_back(agent_sandbox, fake_http, monkeypatch):
    _here(fake_http, geo="geocode_elm", disc="discover_elm")
    prov = _agent(agent_sandbox, fake_http, monkeypatch, "elm", "Elm")
    res = run(prov.handle(AgentRequest(text="take me to elm", session_id="bt3", lat=SAC[0], lng=SAC[1]), None))
    assert res.status == "confirm" and res.route is None
    assert res.reply == "I found Elm Street Diner on Elm Street in Sacramento, California, about 1.8 miles away. Is that the one?"
    res = run(prov.handle(AgentRequest(text="yes", session_id="bt3", lat=SAC[0], lng=SAC[1]), None))
    assert res.status == "route_set" and res.place.name == "Elm Street Diner"
