"""What a route costs the driver: tolls plus diesel, so the Routes sheet and the voice can say which way is the
cheapest, not only the fastest.

fuel = miles / mpg x the regional weekly diesel average (EIA, services/fuel_index.py) at the truck's position.
mpg is the truck profile's own, else DEFAULT_MPG (a loaded tractor-trailer). Without an EIA price the routes get
no cost and no "cheapest" tag; the fastest is tagged either way.
"""
import asyncio
import logging
import os
from typing import Iterable, Optional

from models import RouteInfo, TruckProfile
from services import fuel_index
from services.flags import on

logger = logging.getLogger("truckbuddy.route_cost")

DEFAULT_MPG = 6.5
PRICE_BUDGET_S = 1.0  # the regional price may add at most this much to a route reply
SAVE_MIN_USD = 10.0  # a cheaper way is offered by voice only when it saves at least this much
SAME_COST_USD = 1.0  # within this, two routes cost the same: no "cheapest" tag


async def diesel_price(lat: float, lng: float) -> Optional[tuple[float, str]]:
    """(regional average $/gal, region name) at a point, or None (no key, EIA off, slow or down)."""
    if not on("EIA_ENABLED") or not os.environ.get("EIA_API_KEY"):
        return None

    async def lookup():
        state = await fuel_index.state_code(lat, lng)
        area = fuel_index.STATE_TO_DUOAREA.get(state or "", "NUS")
        avg = await fuel_index.weekly_avg(area)
        return (avg[0], fuel_index.REGION_NAME.get(area, "the region")) if avg else None

    try:
        return await asyncio.wait_for(lookup(), PRICE_BUDGET_S)
    except Exception as e:  # noqa: BLE001 - incl. timeout: the route goes out without a cost
        logger.info("route cost: no diesel price: %s", type(e).__name__)
        return None


def mpg_of(t: Optional[TruckProfile]) -> float:
    return (t.mpg if t is not None and t.mpg else None) or DEFAULT_MPG


def _routes(route: RouteInfo) -> list[RouteInfo]:
    return [route, *route.alternatives]


def price(routes: Iterable[RouteInfo], per_gal: Optional[float], mpg: float) -> None:
    """fuel_usd / cost_usd on each route (None without a diesel price)."""
    for r in routes:
        if per_gal:
            r.fuel_usd = round(r.distance_mi / mpg * per_gal, 2)
            r.cost_usd = round(r.fuel_usd + (r.tolls_usd or 0), 2)
        else:
            r.fuel_usd = r.cost_usd = None


def tag(routes: list[RouteInfo]) -> None:
    """"fastest" on the quickest; "cheapest" on the lowest cost when it beats the fastest by SAME_COST_USD."""
    for r in routes:
        r.tags = []
    if not routes:
        return
    fastest = min(routes, key=lambda r: r.duration_s)
    fastest.tags.append("fastest")
    costed = [r for r in routes if r.cost_usd is not None]
    if len(routes) > 1 and costed and fastest.cost_usd is not None:
        cheapest = min(costed, key=lambda r: r.cost_usd)
        if cheapest is not fastest and cheapest.cost_usd <= fastest.cost_usd - SAME_COST_USD:
            cheapest.tags.append("cheapest")


async def annotate(route: RouteInfo, lat: float, lng: float, truck: Optional[TruckProfile]) -> Optional[float]:
    """Costs and tags on a route and its alternatives, in place; returns the $/gal used (None without one)."""
    got = await diesel_price(lat, lng)
    per_gal = got[0] if got else None
    routes = _routes(route)
    price(routes, per_gal, mpg_of(truck))
    tag(routes)
    return per_gal


def cheaper_way(route: RouteInfo) -> Optional[tuple[RouteInfo, float]]:
    """(the alternative tagged cheapest, dollars it saves) when it saves at least SAVE_MIN_USD, else None."""
    if route.cost_usd is None:
        return None
    for alt in route.alternatives:
        if "cheapest" in alt.tags and alt.cost_usd is not None and route.cost_usd - alt.cost_usd >= SAVE_MIN_USD:
            return alt, route.cost_usd - alt.cost_usd
    return None
