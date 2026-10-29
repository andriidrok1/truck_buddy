"""New place categories (walmart, cat_scale, truck_wash, weigh_station): consistency, Google query shape,
name filters, data-file search, the CATEGORIES_EXTRA gate and the spoken templates."""
import json
from typing import get_args

import pytest
from conftest import fixture_json, run

from models import AgentRequest, Category, Place, PlacesResponse
from services import agent, google_places, places
from services.google_places import INCLUDED_TYPE, KEEP_NAME, TEXT_QUERY, GooglePlacesProvider

SAC = {"lat": 38.5816, "lng": -121.4944}


def test_every_category_is_wired_everywhere():
    for c in get_args(Category):
        assert c in agent.CATEGORY_WORD, c
        assert c in agent.TEMPLATES and len(agent.TEMPLATES[c]) >= 2, c
        assert c in TEXT_QUERY or c in places.DATA_ONLY, c
        assert c in agent.CATEGORIES
    assert set(places.DATA_ONLY).isdisjoint(TEXT_QUERY)
    for c, (intro, body) in ((c, t) for c in get_args(Category) for t in agent.TEMPLATES[c]):
        body.format(name="X", dest="X", mi="1", time="1 min", off="", up="")  # every placeholder is known


def test_gate_defaults_to_core_only(monkeypatch):
    monkeypatch.delenv("CATEGORIES_EXTRA", raising=False)
    assert agent.enabled_categories() == ("truck_stop", "hotel", "rest_area")
    monkeypatch.setenv("CATEGORIES_EXTRA", "walmart, cat_scale")
    assert agent.enabled_categories() == ("truck_stop", "hotel", "rest_area", "walmart", "cat_scale")
    monkeypatch.setenv("CATEGORIES_EXTRA", "all")
    assert agent.enabled_categories() == agent.CATEGORIES
    monkeypatch.setenv("CATEGORIES_EXTRA", "bogus")
    assert agent.enabled_categories() == ("truck_stop", "hotel", "rest_area")


def test_disabled_category_gets_the_off_line(agent_sandbox, monkeypatch):
    monkeypatch.delenv("CATEGORIES_EXTRA", raising=False)
    agent_sandbox.llm.on(r"cat scale", {"intent": "find", "category": "cat_scale"})
    res = run(agent_sandbox.provider().handle(AgentRequest(text="find a CAT scale", session_id="off", **SAC), None))
    assert res.status == "chat"
    assert res.reply == "I can't look up CAT scales yet. Ask me for a truck stop, a hotel or a rest area."
    assert agent_sandbox.places_provider.calls == []


def test_google_body_per_category(monkeypatch, fake_http):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-google")
    fake_http.add("places.googleapis.com", "/v1/places:searchText", json={"places": []})
    prov = GooglePlacesProvider()
    run(prov.search("walmart", **SAC))
    run(prov.search("truck_wash", **SAC))
    run(prov.search("truck_stop", **SAC))
    bodies = [json.loads(c.content) for c in fake_http.calls_to("places.googleapis.com")]
    walmart = next(b for b in bodies if b["textQuery"] == "Walmart Supercenter")
    assert "includedType" not in walmart and "strictTypeFiltering" not in walmart
    wash = next(b for b in bodies if b["textQuery"] == "truck wash")
    assert wash["includedType"] == "car_wash" and wash["strictTypeFiltering"] is True
    ts = next(b for b in bodies if b["textQuery"] == "truck stop")
    assert ts["includedType"] == "truck_stop"


def _gp(name, lat=38.59, lng=-121.49, types=("establishment",), primary="store"):
    return {"id": f"g-{name.lower().replace(' ', '-')}", "displayName": {"text": name},
            "formattedAddress": f"{name}, Sacramento, CA", "location": {"latitude": lat, "longitude": lng},
            "types": list(types), "primaryType": primary}


def test_name_filters(monkeypatch, fake_http):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-google")
    body = {"places": [_gp("Walmart Supercenter"), _gp("Walmart Neighborhood Market", lat=38.6),
                       _gp("Walmart Pharmacy", lat=38.58)]}
    fake_http.add("places.googleapis.com", "/v1/places:searchText", json=body)
    res = run(GooglePlacesProvider().search("walmart", **SAC))
    assert [p.name for p in res.places] == ["Walmart Supercenter"]
    fake_http.routes.clear()
    fake_http.add("places.googleapis.com", "/v1/places:searchText", json={"places": [
        _gp("Blue Beacon Truck Wash", primary="car_wash"), _gp("Quick Car Wash", lat=38.6, primary="car_wash"),
        _gp("Big Rig Wash & Lube", lat=38.58, primary="car_wash")]})
    prov = GooglePlacesProvider()
    prov._searches.clear()
    res = run(prov.search("truck_wash", **SAC))
    assert sorted(p.name for p in res.places) == ["Big Rig Wash & Lube", "Blue Beacon Truck Wash"]
    assert KEEP_NAME["walmart"].search("walmart supercenter #123") and not KEEP_NAME["walmart"].search("Walmart")
    assert INCLUDED_TYPE["truck_wash"] == "car_wash"


def test_cat_scale_data_search_is_local_and_widens():
    assert places.has_data("cat_scale")
    res = places.search_data("cat_scale", SAC["lat"], SAC["lng"], "data")
    assert res.places and res.places[0].category == "cat_scale" and res.places[0].name.startswith("CAT Scale at ")
    assert res.places[0].distance_mi is not None and res.places[0].distance_mi < 15
    assert all(a.distance_km <= b.distance_km for a, b in zip(res.places, res.places[1:]))
    assert res.radius_km in places.DATA_RADII_KM and len(res.places) >= places.MIN_RESULTS
    dense = places.search_data("cat_scale", 32.78, -96.80, "data")  # Dallas: plenty within 50 km
    assert dense.radius_km == 50.0 and dense.widened is False and len(dense.places) == 10
    remote = places.search_data("cat_scale", 44.5, -104.0, "data")  # Devils Tower, WY: nothing within 50 km
    assert remote.widened is True and remote.radius_km in (150.0, 400.0) and remote.places


def test_data_only_search_skips_google(monkeypatch, fake_http):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-google")
    prov = GooglePlacesProvider()
    res = run(prov.search("cat_scale", **SAC))
    assert res.source == "data" and res.places and fake_http.calls == []
    assert run(prov.get(res.places[0].id, **SAC)) is not None  # remembered for later lookups
    mock = run(places.MockPlacesProvider().search("cat_scale", 44.5, -104.0))  # outside the demo coverage too
    assert mock.places and mock.message is None
    assert run(places.MockPlacesProvider().get(mock.places[0].id, 44.5, -104.0)).id == mock.places[0].id


def test_missing_data_file_is_honest(monkeypatch):
    monkeypatch.setitem(places._data_cache, "weigh_station", None)
    res = places.search_data("weigh_station", **SAC, source="data")
    assert res.places == [] and res.message == "I don't have weigh station data loaded yet."


def test_broken_data_file_is_ignored(tmp_path, monkeypatch):
    bad = tmp_path / "truck_wash.json"
    bad.write_text("{not json")
    monkeypatch.setattr(places, "DATA_DIR", tmp_path)
    places._data_cache.pop("truck_wash", None)
    assert places.data_rows("truck_wash") == []
    (tmp_path / "x.json").write_text('[{"id": "a"}, 5, "s"]')
    assert places.data_rows("x") == [{"id": "a"}]


def test_find_speaks_the_category_template(agent_sandbox, fake_http, monkeypatch):
    monkeypatch.setenv("CATEGORIES_EXTRA", "all")
    fake_http.add("router.hereapi.com", "/v8/routes", json=fixture_json("here/route_sac_reno"))
    scale = Place(id="cat-3390", name="CAT Scale at Maverik #654", category="cat_scale", lat=38.571849,
                  lng=-121.577917, address="I-80 and EXIT 81, West Sacramento, CA 95691", distance_mi=4.6)
    wal = Place(id="g-wal", name="Walmart Supercenter", category="walmart", lat=38.6, lng=-121.4,
                address="8915 Gerber Rd, Sacramento, CA", distance_mi=6.0)
    agent_sandbox.places_provider.places = [scale, wal]
    llm = agent_sandbox.llm
    llm.on(r"scale", {"intent": "find", "category": "cat_scale"})
    llm.on(r"walmart", {"intent": "find", "category": "walmart"})
    prov = agent_sandbox.provider()
    res = run(prov.handle(AgentRequest(text="find me a CAT scale", session_id="c1", **SAC), None))
    assert res.status == "route_set" and res.category == "cat_scale"
    assert res.reply == ("Got a scale for you. CAT scale at CAT Scale at Maverik #654, right off N Virginia St, "
                         "about 132 miles out.")
    res = run(prov.handle(AgentRequest(text="is there a Walmart nearby", session_id="c2", **SAC), None))
    assert res.reply == ("Found a Walmart. Walmart Supercenter, right off N Virginia St, about 132 miles out. Big lot, "
                         "but overnight is up to the store manager.")


def test_no_parking_checks_for_scales_and_washes(agent_sandbox, fake_http, monkeypatch):
    monkeypatch.setenv("CATEGORIES_EXTRA", "all")
    fake_http.add("router.hereapi.com", "/v8/routes", json=fixture_json("here/route_sac_reno"))
    checked = []

    async def _noop():
        return None

    def spy(place, *a, **k):  # records at call time; the sandbox's _background just closes the coroutine
        checked.append(place.category)
        return _noop()

    monkeypatch.setattr(agent, "check_parking", spy)
    wash = Place(id="w", name="Blue Beacon Truck Wash", category="truck_wash", lat=38.6, lng=-121.4, distance_mi=3.0)
    wal = Place(id="g-wal", name="Walmart Supercenter", category="walmart", lat=38.6, lng=-121.4, distance_mi=6.0)
    agent_sandbox.places_provider.places = [wash, wal]
    agent_sandbox.llm.on(r"wash", {"intent": "find", "category": "truck_wash"})
    agent_sandbox.llm.on(r"walmart", {"intent": "find", "category": "walmart"})
    prov = agent_sandbox.provider()
    run(prov.handle(AgentRequest(text="truck wash", session_id="p1", **SAC), None))
    assert checked == []
    run(prov.handle(AgentRequest(text="walmart", session_id="p2", **SAC), None))
    assert checked == ["walmart", "walmart"]  # precheck + the route destination


@pytest.mark.parametrize("category,code", [("walmart", 200), ("cat_scale", 200), ("xyz", 422)])
def test_places_endpoint_validates_the_category(api_client, category, code):
    r = api_client.get("/api/places", params={"category": category, **SAC})
    assert r.status_code == code, r.text
    if code == 200:
        body = PlacesResponse(**r.json())
        assert body.category == category
        if category == "cat_scale":
            assert body.places and body.source == "data"


def test_google_module_imports_places_without_a_cycle():
    assert google_places.DATA_ONLY == places.DATA_ONLY
