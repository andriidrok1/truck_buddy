# Iteration 24: Google truck_stop filtering, 60 m dedupe, curated places merged in
from models import Place
from services.google_places import dedupe_nearby, truck_stop_reject


def test_reject_truck_wash_scale_and_food():
    assert truck_stop_reject("AAA truck Wash Scale", ["truck_stop", "gas_station"]) == "name"
    assert truck_stop_reject("Taqueria El Trailero", ["truck_stop", "gas_station"]) == "name"
    assert truck_stop_reject("Joe's Diner", ["truck_stop", "restaurant", "gas_station"], "restaurant") == "type"
    assert truck_stop_reject("FleetTech Office", ["truck_stop"]) == "name"


def test_keep_rules():
    assert truck_stop_reject("Chevron", ["truck_stop", "gas_station", "food"], "gas_station") is None  # truck_stop + fuel
    assert truck_stop_reject("Chevron", ["truck_stop"]) == "no fuel/chain"
    assert truck_stop_reject("Love's Travel Stop", ["restaurant"]) is None  # chain wins
    assert truck_stop_reject("Pilot Travel Center", []) is None


def _p(pid, name, lat, lng, km):
    return Place(id=pid, name=name, category="truck_stop", lat=lat, lng=lng, distance_km=km, distance_mi=km / 1.6)


def test_dedupe_60m_prefers_curated_then_truck_stop_name():
    a = _p("g1", "Shell", 38.61736, -121.53861, 1.0)
    b = _p("g2", "Sacramento 49er Travel Plaza", 38.61740, -121.53865, 1.0)
    c = _p("ts-49er", "Sacramento 49er Travel Plaza", 38.61736, -121.53861, 1.0)
    far = _p("g3", "Pilot", 38.70, -121.60, 9.0)
    assert [p.id for p in dedupe_nearby([a, b, far])] == ["g2", "g3"]
    assert [p.id for p in dedupe_nearby([a, b, c, far])] == ["ts-49er", "g3"]


def test_same_name_within_500m_is_one_place():
    g = _p("g1", "Sacramento 49er Travel Plaza", 38.61736, -121.53861, 5.8)
    c = _p("ts-49er", "Sacramento 49er Travel Plaza", 38.61900, -121.53861, 5.9)  # ~180 m apart
    other = _p("g2", "Shell", 38.61900, -121.53861, 5.9)
    assert [p.id for p in dedupe_nearby([g, c])] == ["ts-49er"]
    assert len(dedupe_nearby([g, other])) == 2
