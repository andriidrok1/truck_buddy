"""Our own places (scripts/build_places.py + services.places.OwnPlacesProvider), replacing Google Places."""
import json

from conftest import run

from scripts import build_places as bp
from services import places

LOVES = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-119.3, 36.2]},
         "properties": {"ref": "382", "name": "Love's Travel Stop", "branch": "Tulare", "hgv": "yes", "shower": "yes",
                        "fuel:adblue": "yes", "fuel:HGV_diesel": "yes", "phone": "+1 559-686-1400",
                        "opening_hours": "Mo-Su 00:00-24:00", "addr:street_address": "2700 S. Blackstone St.",
                        "addr:city": "Tulare", "addr:state": "CA", "addr:postcode": "93274", "addr:country": "US"}}


def test_atp_row_keeps_what_a_driver_asks_about():
    r = bp.atp_row(LOVES, "loves_us", "truck_stop", False)
    assert r["id"] == "atp-loves_us-382" and r["name"] == "Love's Travel Stop Tulare"
    assert r["tags"] == ["Truck diesel", "DEF", "Showers", "24/7", "5596861400"]
    assert r["address"] == "2700 S. Blackstone St., Tulare, CA 93274" and r["source"] == "atp"


def test_atp_row_drops_what_is_not_ours():
    car_only = {**LOVES, "properties": {**LOVES["properties"], "hgv": "no", "fuel:HGV_diesel": "no"}}
    assert bp.atp_row(car_only, "maverik", "truck_stop", True) is None  # a car-only Maverik
    market = {**LOVES, "properties": {"name": "Walmart Neighborhood Market", "ref": "1"}}
    assert bp.atp_row(market, "walmart_us", "walmart", False) is None  # no room for a truck
    soon = {**LOVES, "properties": {"name": "Blue Beacon", "branch": "McAllen, TX - Coming Soon"}}
    assert bp.atp_row(soon, "blue_beacon_us", "truck_wash", False) is None
    abroad = {**LOVES, "geometry": {"type": "Point", "coordinates": [-79.4, 43.7]},  # Toronto: inside the US box
              "properties": {**LOVES["properties"], "addr:country": "CA"}}
    assert bp.atp_row(abroad, "loves_us", "truck_stop", False) is None


def test_pick_run_skips_empty_and_cut_short_runs():
    # Walmart in October 2026: 1 484, then a run cut to 540, then 0
    assert bp.pick_run({"2026-10-10": 1484, "2026-10-17": 540, "2026-10-24": 0}) == "2026-10-10"
    assert bp.pick_run({"2026-10-10": 720, "2026-10-17": 700}) == "2026-10-17"  # within 90 %: the newest
    assert bp.pick_run({"a": 0}) is None and bp.pick_run({}) is None


def _row(id_, cat, lat, lng, source, tags=()):
    return {"id": id_, "name": id_, "category": cat, "lat": lat, "lng": lng, "tags": list(tags), "address": None,
            "source": source}


def test_dedupe_merges_across_sources_only():
    rows = [
        _row("ov-1", "truck_stop", 38.6174, -121.5386, "overture", ["5551234567"]),  # Overture's copy of the chain stop
        _row("atp-pilot-1", "truck_stop", 38.6172, -121.5384, "atp", ["Showers"]),
        _row("atp-loves-1", "truck_stop", 38.6180, -121.5390, "atp"),  # another chain across the road: its own stop
        _row("rest-1", "rest_area", 38.67183, -121.61215, "ntad"),  # Elkhorn southbound
        _row("rest-2", "rest_area", 38.67148, -121.61224, "ntad"),  # Elkhorn northbound: the other side
    ]
    out = {r["id"]: r for r in bp.dedupe(rows)}
    assert set(out) == {"atp-pilot-1", "atp-loves-1", "rest-1", "rest-2"}
    assert out["atp-pilot-1"]["tags"] == ["Showers", "5551234567"]  # the loser's phone is kept
    twice = [_row("ov-a", "truck_stop", 38.86210, -121.9506, "overture"), _row("ov-b", "truck_stop", 38.86215, -121.9507, "overture"),
             _row("ov-w1", "walmart", 38.6, -121.4, "overture"), _row("ov-w2", "walmart", 38.6012, -121.4, "overture")]
    assert {r["id"] for r in bp.dedupe(twice)} == {"ov-a", "ov-w1"}  # Overture's two records of one place


def test_overture_kinds():
    assert bp.overture_kind("Iowa 80 Truckstop", "gas_station") == "truck_stop"
    assert bp.overture_kind("Sapp Bros Travel Center", "convenience_store") == "truck_stop"
    assert bp.overture_kind("Sunrise Travel Center", "travel_agent") is None  # an agency
    assert bp.overture_kind("Truck Stop Tire Repair", "tire_shop") is None
    assert bp.overture_kind("Motel 6 Fairfield", "hotel") == "hotel"
    assert bp.overture_kind("Hilton San Francisco Union Square", "hotel") is None  # no truck goes downtown
    row = bp.overture_row({"id": "x", "name": "Walmart Pharmacy", "kind": "pharmacy", "brand": "Walmart", "lat": 38.5,
                           "lng": -121.5, "confidence": 0.9, "status": "open"})
    assert row is None
    row = bp.overture_row({"id": "y", "name": "Walmart Supercenter", "kind": "department_store", "brand": "Walmart",
                           "lat": 38.5, "lng": -121.5, "confidence": 0.9, "status": "open", "phone": "+19165550100"})
    assert row["category"] == "walmart" and row["tags"] == ["9165550100"]


def _own(tmp_path, rows_by_cat):
    for cat, rows in rows_by_cat.items():
        (tmp_path / f"{cat}.json").write_text(json.dumps(rows))
    return places.OwnPlacesProvider(tmp_path)


SAC = (38.5816, -121.4944)


def test_own_search_nearest_first_and_widens(tmp_path):
    near = [_row(f"ts-{i}", "truck_stop", SAC[0] + 0.01 * i, SAC[1], "atp") for i in range(1, 15)]
    far = [_row("ts-far", "truck_stop", 39.5, -119.8, "atp")]  # Reno, ~180 km
    prov = _own(tmp_path, {"truck_stop": near + far})
    res = run(prov.search("truck_stop", *SAC))
    assert res.source == "own" and res.radius_km == 25.0 and not res.widened
    assert [p.id for p in res.places][:3] == ["ts-1", "ts-2", "ts-3"] and len(res.places) == 10
    lonely = _own(tmp_path / "x" if (tmp_path / "x").mkdir() is None else tmp_path, {"truck_stop": far})
    res = run(lonely.search("truck_stop", *SAC))
    assert res.places == [] and res.widened and res.radius_km == 150.0  # Reno is past the widest radius
    res = run(lonely.search("truck_stop", 39.4, -119.9))
    assert [p.id for p in res.places] == ["ts-far"] and res.places[0].distance_mi is not None


def test_own_get_and_missing_category(tmp_path):
    prov = _own(tmp_path, {"rest_area": [_row("rest-9", "rest_area", 38.67, -121.61, "ntad")]})
    p = run(prov.get("rest-9", *SAC))
    assert p and p.category == "rest_area" and p.distance_km > 0
    assert run(prov.get("nope", *SAC)) is None
    res = run(prov.search("hotel", *SAC))
    assert res.places == [] and res.message == "I don't have hotel data loaded yet."


def test_provider_switch(monkeypatch):
    monkeypatch.setenv("PLACES_PROVIDER", "own")
    assert places.get_places_provider().source == "own"
    monkeypatch.setenv("PLACES_PROVIDER", "mock")
    assert places.get_places_provider().source == "mock"


def test_osm_rows():
    fuel = {"type": "way", "id": 42, "center": {"lat": 38.6174, "lon": -121.5386},
            "tags": {"amenity": "fuel", "hgv": "yes", "name": "Sacramento 49er Travel Plaza", "shower": "yes",
                     "opening_hours": "24/7", "phone": "+1 916-927-4774", "addr:housenumber": "2828",
                     "addr:street": "El Centro Road", "addr:city": "Sacramento", "addr:state": "CA"}}
    r = bp.osm_row(fuel)
    assert r["id"] == "osm-w42" and r["category"] == "truck_stop" and r["source"] == "osm"
    assert r["tags"] == ["Truck diesel", "Showers", "24/7", "9169274774"]
    assert r["address"] == "2828 El Centro Road, Sacramento, CA"
    rest = {"type": "node", "id": 7, "lat": 38.152, "lon": -122.217, "tags": {"highway": "rest_area", "hgv": "yes"}}
    assert bp.osm_row(rest)["name"] == "Rest Area" and bp.osm_row(rest)["tags"] == ["Truck parking"]
    assert bp.osm_row({"type": "node", "id": 8, "lat": 38.1, "lon": -122.2, "tags": {"amenity": "fuel", "hgv": "yes"}}) is None


def test_osm_plaza_and_its_pumps_are_one_stop_named_after_the_plaza():
    pumps = bp.osm_row({"type": "way", "id": 1, "center": {"lat": 38.6170, "lon": -121.5384},
                        "tags": {"amenity": "fuel", "hgv": "yes", "name": "Pilot"}})
    plaza = bp.osm_row({"type": "way", "id": 2, "center": {"lat": 38.6175, "lon": -121.5367},
                        "tags": {"highway": "services", "name": "Sacramento 49er Travel Plaza", "hgv": "yes"}})
    out = bp.dedupe([pumps, plaza])
    assert len(out) == 1 and out[0]["name"] == "Sacramento 49er Travel Plaza" and "Truck diesel" in out[0]["tags"]
    assert bp.osm_row({"type": "way", "id": 3, "center": {"lat": 38.6, "lon": -121.5}, "tags": {"highway": "services"}}) is None
