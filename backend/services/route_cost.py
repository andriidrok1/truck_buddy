"""What a route costs the driver: tolls plus diesel, so the Routes sheet and the voice can say which way is the
cheapest, not only the fastest.

fuel = miles / mpg x the weekly EIA diesel average (services/fuel_index.py), state by state: HERE says how much of
the route lies in each state (RouteInfo.state_share), so Sacramento->Reno is priced at California for the CA miles
and at the West Coast average for the NV ones. A route with no states (ORS, mock) is priced at the truck's
position. mpg is the truck profile's own, else DEFAULT_MPG (a loaded tractor-trailer). Without an EIA price the
routes get no cost and no "cheapest" tag; the fastest is tagged either way.

fuel_index.prewarm keeps every regional average cached from startup, so pricing a route normally costs no I/O.
"""
import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Iterable, Optional, Union

from models import RouteInfo, TruckProfile
from services import fuel_index
from services.flags import on

logger = logging.getLogger("truckbuddy.route_cost")

DEFAULT_MPG = 6.5
PRICE_BUDGET_S = 1.5  # the regional price may add at most this much to a route reply
SAVE_MIN_USD = 10.0  # a cheaper way is offered by voice only when it saves at least this much
SAME_COST_USD = 1.0  # within this, two routes cost the same: no "cheapest" tag


_inflight: dict[tuple, "asyncio.Task"] = {}  # one lookup per key (EIA area or half-degree cell) at a time


@dataclass
class Diesel:
    """$/gal per state on the routes, and `base` for miles in no known state (the truck's own state or area)."""
    base: Optional[float] = None
    by_state: dict = field(default_factory=dict)

    def for_route(self, r: RouteInfo) -> Optional[float]:
        """The route's $/gal, weighted by its miles per state; None when a state on it has no price."""
        share = r.state_share
        if not share:
            return self.base
        total = 0.0
        for st, f in share.items():
            p = self.by_state.get(st, self.base)
            if not p:
                return None
            total += f * p
        return total


def _shared(key: tuple, make) -> "asyncio.Task":
    """The running lookup for `key`, or a new one. Shielded by design: a reply that stops waiting never cancels it,
    so it still fills the fuel_index caches for the next route."""
    task = _inflight.get(key)
    if task is None or task.done():
        task = asyncio.create_task(make())
        _inflight[key] = task
        task.add_done_callback(lambda t, k=key: _inflight.pop(k, None) if _inflight.get(k) is t else None)
    return task


def _enabled() -> bool:
    return on("EIA_ENABLED") and bool(os.environ.get("EIA_API_KEY"))


async def _lookup(lat: float, lng: float) -> Optional[tuple[float, str]]:
    state = await fuel_index.state_code(lat, lng)
    area = fuel_index.STATE_TO_DUOAREA.get(state or "", "NUS")
    avg = await fuel_index.weekly_avg(area)
    return (avg[0], fuel_index.REGION_NAME.get(area, "the region")) if avg else None


async def diesel_price(lat: float, lng: float) -> Optional[tuple[float, str]]:
    """(regional average $/gal, region name) at a point, or None (no key, EIA off, slow or down).

    Cold, the lookup is two calls (HERE state, EIA average) and can take longer than PRICE_BUDGET_S. It is shielded:
    past the budget this reply goes without a cost, but the lookup finishes in the background and fills the
    fuel_index caches (a day), so the next route has the price at once. Cancelling it instead left the caches empty
    and every route timed out (prod 2026-11-08)."""
    if not _enabled():
        return None
    task = _shared(("cell", *fuel_index._cell(lat, lng)), lambda: _lookup(lat, lng))
    try:
        return await asyncio.wait_for(asyncio.shield(task), PRICE_BUDGET_S)
    except Exception as e:  # noqa: BLE001 - incl. timeout: the route goes out without a cost
        logger.info("route cost: no diesel price yet: %s", type(e).__name__)
        return None


async def _area_price(area: str) -> Optional[float]:
    avg = await fuel_index.weekly_avg(area)
    return avg[0] if avg else None


async def diesel_for(routes: list[RouteInfo], lat: float, lng: float) -> Diesel:
    """Diesel prices for these routes: per state when the router gave states, else at the truck's position.
    Waits at most PRICE_BUDGET_S; what is still loading then is left out of this reply but keeps loading."""
    states: list[str] = []
    for r in routes:
        states += [st for st in r.state_share if st not in states]
    if not states:
        got = await diesel_price(lat, lng)
        return Diesel(got[0] if got else None)
    if not _enabled():
        return Diesel()
    areas = {st: fuel_index.STATE_TO_DUOAREA.get(st, "NUS") for st in states}
    tasks = {a: _shared(("eia", a), lambda a=a: _area_price(a)) for a in set(areas.values())}
    done, _ = await asyncio.wait(tasks.values(), timeout=PRICE_BUDGET_S)
    price_of = {a: t.result() for a, t in tasks.items() if t in done and t.exception() is None}
    by_state = {st: price_of[a] for st, a in areas.items() if price_of.get(a)}
    if len(by_state) < len(states):
        logger.info("route cost: no diesel price yet for %s", sorted(set(states) - set(by_state)))
    start = next(iter(routes[0].state_share), None) if routes else None
    return Diesel(by_state.get(start), by_state)


def mpg_of(t: Optional[TruckProfile]) -> float:
    return (t.mpg if t is not None and t.mpg else None) or DEFAULT_MPG


def _routes(route: RouteInfo) -> list[RouteInfo]:
    return [route, *route.alternatives]


def price(routes: Iterable[RouteInfo], diesel: Union[Diesel, float, None], mpg: float) -> None:
    """fuel_usd / cost_usd on each route (None without a diesel price). `diesel` is a Diesel (per state) or one
    $/gal for every mile."""
    for r in routes:
        per_gal = diesel.for_route(r) if isinstance(diesel, Diesel) else diesel
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


async def annotate(route: RouteInfo, lat: float, lng: float, truck: Optional[TruckProfile]) -> Diesel:
    """Costs and tags on a route and its alternatives, in place; returns the prices used, to price another way
    the same."""
    routes = _routes(route)
    diesel = await diesel_for(routes, lat, lng)
    price(routes, diesel, mpg_of(truck))
    tag(routes)
    return diesel


def cheaper_way(route: RouteInfo) -> Optional[tuple[RouteInfo, float]]:
    """(the alternative tagged cheapest, dollars it saves) when it saves at least SAVE_MIN_USD, else None."""
    if route.cost_usd is None:
        return None
    for alt in route.alternatives:
        if "cheapest" in alt.tags and alt.cost_usd is not None and route.cost_usd - alt.cost_usd >= SAVE_MIN_USD:
            return alt, route.cost_usd - alt.cost_usd
    return None
