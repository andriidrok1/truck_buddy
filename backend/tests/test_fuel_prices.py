"""Diesel prices from Google Places `fuelOptions`: parsing, freshness, and how the copilot says them."""
from datetime import datetime, timedelta, timezone

from models import Place, RouteInfo
from services import agent
from services.google_places import diesel_from_fuel_options

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)


def _fp(kind: str, units: int, nanos: int, days_old: float = 0.0, currency: str = "USD") -> dict:
    t = (NOW - timedelta(days=days_old)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"type": kind, "price": {"currencyCode": currency, "units": str(units), "nanos": nanos}, "updateTime": t}


def test_prefers_truck_lane_over_car_pump():
    fuel = {"fuelPrices": [_fp("DIESEL", 5, 190000000), _fp("TRUCK_DIESEL", 5, 390000000), _fp("REGULAR_UNLEADED", 4, 0)]}
    assert diesel_from_fuel_options(fuel, NOW.timestamp()) == (5.39, "truck", fuel["fuelPrices"][1]["updateTime"])


def test_car_pump_when_no_truck_lane():
    fuel = {"fuelPrices": [_fp("DIESEL", 5, 190000000)]}
    price, kind, _ = diesel_from_fuel_options(fuel, NOW.timestamp())
    assert (price, kind) == (5.19, "auto")


def test_stale_and_foreign_and_missing_are_dropped():
    assert diesel_from_fuel_options({"fuelPrices": [_fp("TRUCK_DIESEL", 5, 0, days_old=9)]}, NOW.timestamp()) is None
    assert diesel_from_fuel_options({"fuelPrices": [_fp("DIESEL", 5, 0, currency="CAD")]}, NOW.timestamp()) is None
    assert diesel_from_fuel_options({"fuelPrices": [_fp("REGULAR_UNLEADED", 4, 0)]}, NOW.timestamp()) is None
    assert diesel_from_fuel_options(None, NOW.timestamp()) is None
    assert diesel_from_fuel_options({"fuelPrices": [{"type": "DIESEL", "price": {"units": "x"}}]}, NOW.timestamp()) is None


def _place(**kw) -> Place:
    base = dict(id="p1", name="Pilot Travel Center", category="truck_stop", lat=38.5, lng=-121.4, address="123 Main St, Lodi, CA")
    base.update(kw)
    return Place(**base)


def _route() -> RouteInfo:
    return RouteInfo(geometry=[[38.5, -121.4]], distance_m=10000.0, duration_s=600.0, distance_mi=6.2, duration_min=10,
                     summary="10 min · 6.2 mi", source="here")


def test_route_reply_says_truck_lane_price():
    out = agent._route_reply(_place(diesel_price=5.39, diesel_kind="truck"), _route(), 0)
    assert "Diesel's $5.39 there." in out


def test_route_reply_flags_car_pump_price():
    out = agent._route_reply(_place(diesel_price=5.19, diesel_kind="auto"), _route(), 0)
    assert "$5.19 at the car pumps" in out


def test_route_reply_silent_without_price_or_for_hotels():
    assert "Diesel" not in agent._route_reply(_place(), _route(), 0)
    assert "Diesel" not in agent._route_reply(_place(category="hotel", diesel_price=5.0, diesel_kind="truck"), _route(), 0)


def test_model_place_list_carries_price_tag():
    assert agent._diesel_note(_place(diesel_price=5.39, diesel_kind="truck")) == ", diesel $5.39"
    assert agent._diesel_note(_place(diesel_price=5.19, diesel_kind="auto")) == ", diesel $5.19 (car pumps)"
    assert agent._diesel_note(_place()) == ""
