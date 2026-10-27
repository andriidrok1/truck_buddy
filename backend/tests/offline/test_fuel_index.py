"""EIA regional diesel baseline: area mapping, parsing, caching, the spoken comparison, failure modes."""
import httpx
from conftest import fixture_json, params_of, run

from models import Place, RouteInfo
from services import agent, fuel_index
from services.fuel_index import STATE_TO_DUOAREA, compare, parse_weekly

EIA_R50 = fixture_json("eia/diesel_weekly_r50")
EIA_NUS = fixture_json("eia/diesel_weekly_nus")
REV_CA = {"items": [{"address": {"countryCode": "USA", "stateCode": "CA", "state": "California"}}]}
REV_MX = {"items": [{"address": {"countryCode": "MEX", "stateCode": "BCN"}}]}


def setup_function():
    fuel_index._avg_cache.clear()
    fuel_index._state_cache.clear()


def test_every_state_maps_to_an_eia_area():
    states = ("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC "
              "ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY").split()
    assert all(s in STATE_TO_DUOAREA for s in states), [s for s in states if s not in STATE_TO_DUOAREA]
    assert STATE_TO_DUOAREA["CA"] == "SCA" and STATE_TO_DUOAREA["NV"] == "R50" and STATE_TO_DUOAREA["IA"] == "R20"
    assert STATE_TO_DUOAREA["TX"] == "STX" and STATE_TO_DUOAREA["LA"] == "R30" and STATE_TO_DUOAREA["VT"] == "R1X"


def test_parse_weekly_takes_the_newest_valid_row():
    assert parse_weekly(EIA_R50) == (7.329, "2026-10-26")
    assert parse_weekly({"response": {"data": []}}) is None
    assert parse_weekly({}) is None
    assert parse_weekly({"response": {"data": [{"value": "x"}, {"value": "0"}, {"value": "5.1", "period": "p"}]}}) \
        == (5.1, "p")


def test_compare_wording():
    assert compare(5.39, 5.09, "California") == "That's 30 cents above the California average."
    assert compare(4.95, 5.09, "the West Coast") == "That's 14 cents below the the West Coast average." \
        or compare(4.95, 5.09, "the West Coast") == "That's 14 cents below the West Coast average."
    assert compare(5.10, 5.09) == "That's right at the regional average."
    assert compare(5.11, 5.09, "Texas") == "That's right at the Texas average."
    assert compare(5.12, 5.09, "Texas") == "That's 3 cents above the Texas average."


def test_regional_note_end_to_end(monkeypatch, fake_http):
    monkeypatch.setenv("EIA_API_KEY", "eia-test")
    fake_http.add("revgeocode.search.hereapi.com", "/v1/revgeocode", json=REV_CA)
    sca = {"response": {"data": [{"period": "2026-10-26", "value": "6.00"}]}}
    fake_http.add("api.eia.gov", "/v2/petroleum/pri/gnd/data/", json=sca)
    note = run(fuel_index.regional_note(38.58, -121.49, 6.30))
    assert note == " That's 30 cents above the California average."
    eia = fake_http.calls_to("api.eia.gov")[0]
    p = dict(params_of(eia))
    assert p["facets[duoarea][]"] == "SCA" and p["facets[product][]"] == "EPD2D" and p["api_key"] == "eia-test"
    rev = dict(params_of(fake_http.calls_to("revgeocode.search.hereapi.com")[0]))
    assert rev["at"] == "38.58,-121.49"
    # Same cell and same area: both cached, no new calls.
    assert run(fuel_index.regional_note(38.60, -121.40, 5.95)) == " That's 5 cents below the California average."
    assert len(fake_http.calls) == 2


def test_outside_the_us_falls_back_to_the_national_average(monkeypatch, fake_http):
    monkeypatch.setenv("EIA_API_KEY", "eia-test")
    fake_http.add("revgeocode.search.hereapi.com", "/v1/revgeocode", json=REV_MX)
    fake_http.add("api.eia.gov", "/v2/petroleum/pri/gnd/data/", json=EIA_NUS)
    note = run(fuel_index.regional_note(32.5, -117.0, 5.0))
    assert note.endswith("the US average.")
    assert dict(params_of(fake_http.calls_to("api.eia.gov")[0]))["facets[duoarea][]"] == "NUS"


def test_failures_are_silent(monkeypatch, fake_http):
    monkeypatch.setenv("EIA_API_KEY", "eia-test")
    fake_http.add("revgeocode.search.hereapi.com", "/v1/revgeocode", json=REV_CA)
    fake_http.add("api.eia.gov", "/v2/petroleum/pri/gnd/data/", status=503, text="down")
    assert run(fuel_index.regional_note(38.58, -121.49, 6.30)) == ""
    fuel_index._avg_cache.clear()
    fake_http.routes.clear()
    fake_http.add("revgeocode.search.hereapi.com", "/v1/revgeocode", json=REV_CA)
    fake_http.raise_for("api.eia.gov", "/v2/petroleum/pri/gnd/data/", httpx.ReadTimeout("slow"))
    assert run(fuel_index.regional_note(38.58, -121.49, 6.30)) == ""
    fake_http.routes.clear()
    fake_http.raise_for("revgeocode.search.hereapi.com", "/v1/revgeocode", httpx.ConnectError("no"))
    fake_http.add("api.eia.gov", "/v2/petroleum/pri/gnd/data/", json=EIA_NUS)
    fuel_index._state_cache.clear()
    assert run(fuel_index.regional_note(40.0, -100.0, 6.30)).endswith("the US average.")  # revgeocode down: NUS


def test_no_key_or_flag_off_means_no_calls(monkeypatch, fake_http):
    monkeypatch.delenv("EIA_API_KEY", raising=False)
    assert run(fuel_index.regional_note(38.58, -121.49, 6.30)) == ""
    monkeypatch.setenv("EIA_API_KEY", "eia-test")
    monkeypatch.setenv("EIA_ENABLED", "0")
    assert run(fuel_index.regional_note(38.58, -121.49, 6.30)) == ""
    assert run(fuel_index.regional_note(38.58, -121.49, None)) == ""
    assert fake_http.calls == []


def test_cache_expires_after_a_day(monkeypatch, fake_http):
    monkeypatch.setenv("EIA_API_KEY", "eia-test")
    fake_http.add("api.eia.gov", "/v2/petroleum/pri/gnd/data/", json=EIA_R50)
    assert run(fuel_index.weekly_avg("R50", now=0.0)) == (7.329, "2026-10-26")
    assert run(fuel_index.weekly_avg("R50", now=1000.0)) == (7.329, "2026-10-26")
    assert len(fake_http.calls) == 1
    run(fuel_index.weekly_avg("R50", now=fuel_index.TTL_S + 1))
    assert len(fake_http.calls) == 2


def test_diesel_sentence_carries_the_note():
    place = Place(id="p", name="Pilot", category="truck_stop", lat=38.5, lng=-121.4, address="1 Main St, Lodi, CA",
                  diesel_price=5.39, diesel_kind="truck")
    assert agent._diesel_sentence(place, " That's 30 cents above the California average.") == \
        " Diesel's $5.39 there. That's 30 cents above the California average."
    route = RouteInfo(geometry=[[38.5, -121.4], [38.6, -121.3]], distance_m=10000.0, duration_s=600.0, distance_mi=6.2,
                      duration_min=10, summary="10 min · 6.2 mi", source="here")
    out = agent._route_reply(place, route, 0, None, " That's right at the California average.")
    assert out == ("Alright, got one. Pilot, right off Main St, about 6.2 miles out. Diesel's $5.39 there. "
                   "That's right at the California average.")


def test_route_to_adds_the_note_within_the_time_budget(agent_sandbox, fake_http, monkeypatch):
    monkeypatch.setenv("EIA_API_KEY", "eia-test")
    fake_http.add("router.hereapi.com", "/v8/routes", json=fixture_json("here/route_sac_reno"))
    fake_http.add("revgeocode.search.hereapi.com", "/v1/revgeocode", json=REV_CA)
    fake_http.add("api.eia.gov", "/v2/petroleum/pri/gnd/data/", json={"response": {"data": [{"value": "5.09"}]}})
    pilot = Place(id="g1", name="Pilot Travel Center", category="truck_stop", lat=38.58, lng=-121.55,
                  address="3645 W Capitol Ave, West Sacramento, CA", diesel_price=5.39, diesel_kind="truck",
                  distance_mi=3.4)
    agent_sandbox.places_provider.places = [pilot]
    agent_sandbox.llm.on(r"truck stop", {"intent": "find", "category": "truck_stop"})
    from models import AgentRequest
    res = run(agent_sandbox.provider().handle(AgentRequest(text="find a truck stop", session_id="eia", lat=38.5816,
                                                           lng=-121.4944), None))
    assert res.status == "route_set"
    assert res.reply.endswith("Diesel's $5.39 there. That's 30 cents above the California average.")
