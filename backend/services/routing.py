"""Routing provider. MOCK implementation: a gently curved "hand-drawn" line with
distance = straight line x road factor and an average truck speed.

Real implementation to plug in later: OSRM public server (same interface).
"""
import math
from typing import Protocol

from models import RouteInfo
from services.geo import haversine_km, km_to_mi

ROAD_FACTOR = 1.3
AVG_SPEED_MPH = 32.0
POINTS = 32


class RoutingProvider(Protocol):
    source: str

    async def route(self, from_lat: float, from_lng: float, to_lat: float, to_lng: float) -> RouteInfo: ...


def format_summary(duration_min: int, distance_mi: float) -> str:
    return f"{duration_min} min · {distance_mi:.1f} mi"


class MockRoutingProvider:
    source = "mock"

    async def route(self, from_lat: float, from_lng: float, to_lat: float, to_lng: float) -> RouteInfo:
        # Quadratic bezier with the control point offset perpendicular to the chord.
        mid_lat, mid_lng = (from_lat + to_lat) / 2, (from_lng + to_lng) / 2
        d_lat, d_lng = to_lat - from_lat, to_lng - from_lng
        ctrl_lat, ctrl_lng = mid_lat - d_lng * 0.18, mid_lng + d_lat * 0.18
        geometry = []
        for i in range(POINTS + 1):
            t = i / POINTS
            lat = (1 - t) ** 2 * from_lat + 2 * (1 - t) * t * ctrl_lat + t ** 2 * to_lat
            lng = (1 - t) ** 2 * from_lng + 2 * (1 - t) * t * ctrl_lng + t ** 2 * to_lng
            # small wobble so it reads like a road, zero at both ends
            wobble = math.sin(t * math.pi * 3) * math.sin(t * math.pi) * 0.0025
            geometry.append([round(lat + wobble, 6), round(lng - wobble, 6)])

        km = haversine_km(from_lat, from_lng, to_lat, to_lng) * ROAD_FACTOR
        mi = km_to_mi(km)
        minutes = max(1, round(mi / AVG_SPEED_MPH * 60))
        return RouteInfo(
            geometry=geometry,
            distance_m=round(km * 1000),
            duration_s=minutes * 60,
            distance_mi=round(mi, 1),
            duration_min=minutes,
            summary=format_summary(minutes, mi),
            source=self.source,
        )


_provider: RoutingProvider = MockRoutingProvider()


def get_routing_provider() -> RoutingProvider:
    return _provider
