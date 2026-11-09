"""Nightly route canary: a synthetic driver asks the deployed backend for truck routes all over the US and checks
that every answer is sane. No LLM, no voice, no reviews: only POST /route (HERE + EIA) and GET /health.

Born from the 2026-11-08 driver test: HERE changed one field's shape and every route with a weight limit fell back
to "the router is down" for days without anyone noticing. A red run here means the deployment or a provider
changed, not the code in the PR.

Usage: python scripts/route_canary.py [BASE_URL]   (default https://api.truckbuddy.io)
Exit code 1 when any check fails; every failure is printed with the route it came from.
"""
import math
import sys
import time

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "https://api.truckbuddy.io").rstrip("/") + "/api"
PAUSE_S = 2.0  # under the per-driver rate limit
RETRY_S = 30.0
NO_TURNS = "The truck router is down"

HEAVY = {"weight_lb": 80000}
TALL = {"height_in": 162}  # 13 ft 6 in
HAZMAT = {"hazmat": True}
NO_TOLLS = {"avoid_tolls": True}

# (name, origin, destination, truck profile overrides): every EIA region, tolls, weight and height limits, cities.
ROUTES = [
    ("Sacramento-Reno", (38.5816, -121.4944), (39.5296, -119.8138), {}),
    ("Sacramento-Las Vegas", (38.5816, -121.4944), (36.1699, -115.1398), HEAVY),
    ("Sacramento-Portland", (38.5816, -121.4944), (45.5152, -122.6784), {}),
    ("LA-Phoenix", (34.0522, -118.2437), (33.4484, -112.0740), HAZMAT),
    ("Seattle-Spokane", (47.6062, -122.3321), (47.6588, -117.4260), TALL),
    ("Denver-Salt Lake", (39.7392, -104.9903), (40.7608, -111.8910), HEAVY),
    ("Dallas-Houston", (32.7767, -96.7970), (29.7604, -95.3698), {}),
    ("Dallas-Chicago", (32.7767, -96.7970), (41.8781, -87.6298), HEAVY),
    ("Chicago-Detroit", (41.8781, -87.6298), (42.3314, -83.0458), {}),
    ("Atlanta-Miami", (33.7490, -84.3880), (25.7617, -80.1918), {}),
    ("Newark-Boston", (40.7357, -74.1724), (42.3601, -71.0589), {}),
    ("Newark-Boston no tolls", (40.7357, -74.1724), (42.3601, -71.0589), NO_TOLLS),
    ("Pittsburgh-Philadelphia", (40.4406, -79.9959), (39.9526, -75.1652), {}),
    ("Jersey City-Brooklyn", (40.7178, -74.0431), (40.6782, -73.9442), HEAVY),  # weight limits: the 11-08 bug
    ("New Orleans-Memphis", (29.9511, -90.0715), (35.1495, -90.0490), {}),
    ("Minneapolis-Fargo", (44.9778, -93.2650), (46.8772, -96.7898), TALL),
    ("Sacramento in town", (38.5816, -121.4944), (38.6714, -121.6122), {}),
]


def _mi(a, b) -> float:
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def check_route(name: str, origin, dest, body: dict) -> list[str]:
    """Problems with one /route answer (empty = fine)."""
    out = []
    r = body.get("route") or {}
    ways = [r, *(r.get("alternatives") or [])]
    if r.get("source") != "here":
        out.append(f"source is {r.get('source')!r}, not here")
    if any(NO_TURNS in n for n in r.get("notices") or []):
        out.append("backup route (router down)")
    if not r.get("maneuvers"):
        out.append("no turn-by-turn")
    crow = _mi(origin, dest)
    for i, w in enumerate(ways):
        tag = f"way {i} {w.get('labels')}"
        d = w.get("distance_mi") or 0
        # Rivers and truck bans stretch city routes: Jersey City->Brooklyn is 44 mi for a legal 80,000 lb truck
        # (5.9 mi as the crow flies). The 11-08 backup line was 85 mi; the backup check above names it anyway.
        if not (crow * 0.95 <= d <= crow * 3 + 40):
            out.append(f"{tag}: {d:.1f} mi for {crow:.1f} mi as the crow flies")
        if not w.get("duration_s") or w["duration_s"] <= 0:
            out.append(f"{tag}: no duration")
        fuel, cost, tolls = w.get("fuel_usd"), w.get("cost_usd"), w.get("tolls_usd") or 0
        if cost is None or fuel is None:
            out.append(f"{tag}: no cost (EIA price missing?)")
            continue
        if abs(cost - (fuel + tolls)) > 0.05:
            out.append(f"{tag}: cost {cost} != fuel {fuel} + tolls {tolls}")
        per_gal = fuel / d * 6.5 if d else 0  # default mpg
        if not (2.5 <= per_gal <= 12):
            out.append(f"{tag}: diesel works out to ${per_gal:.2f}/gal")
    tags = [t for w in ways for t in w.get("tags") or []]
    if tags.count("fastest") != 1:
        out.append(f"{tags.count('fastest')} ways tagged fastest")
    if tags.count("cheapest") > 1:
        out.append("more than one cheapest")
    return out


def main() -> int:
    c = httpx.Client(timeout=40)
    tok = c.post(f"{BASE}/auth/device").json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    failed = 0
    try:
        for name, origin, dest, truck in ROUTES:
            req = {"lat": origin[0], "lng": origin[1], "skip_parking": True, "truck_profile": truck,
                   "place": {"id": f"canary-{name}", "name": name, "category": "destination", "lat": dest[0], "lng": dest[1]}}
            t0 = time.perf_counter()
            res = c.post(f"{BASE}/route", headers=h, json=req)
            if res.status_code in (502, 503, 504):  # a deploy swapping containers; a real outage fails twice
                time.sleep(RETRY_S)
                res = c.post(f"{BASE}/route", headers=h, json=req)
            ms = round((time.perf_counter() - t0) * 1000)
            if res.status_code != 200:
                problems = [f"HTTP {res.status_code}: {res.text[:200]}"]
            else:
                problems = check_route(name, origin, dest, res.json())
            if problems:
                failed += 1
                print(f"FAIL {name} ({ms} ms)")
                for p in problems:
                    print(f"     {p}")
            else:
                r = res.json()["route"]
                print(f"ok   {name}: {r['distance_mi']:.0f} mi, ${r['cost_usd']:.0f}, {len(r.get('alternatives') or [])} alt, {ms} ms")
            time.sleep(PAUSE_S)
        health = c.get(f"{BASE}/health").json()
        degraded = health.get("here_parse_degraded") or {}
        if degraded:
            failed += 1
            print(f"FAIL /health: HERE parts dropped since the last deploy: {degraded} (railway logs: 'here parse degraded')")
    finally:
        c.delete(f"{BASE}/memory", headers=h)  # the synthetic driver leaves nothing behind
    print(f"\nall {len(ROUTES)} routes fine" if not failed else f"\n{failed} check(s) failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
