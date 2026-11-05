#!/usr/bin/env python3
"""Our own US places for the copilot -> backend/data/places/<category>.json (served by OwnPlacesProvider).

Why: Google's Maps Platform terms forbid what the copilot does with places (speaking them, keeping them in the
dialog, showing them on a non-Google map), and Google search was ~40 % of the cost per driver (vault note
"TruckBuddy — План: своя база мест", 2026-11-04). These sources allow storing, speaking and any map:

  AllThePlaces (CC0)   chain locations from the chains' own sites: Pilot/Flying J, Love's, TA/Petro, Roady's,
                       Walmart, Blue Beacon, with amenities (showers, truck diesel, DEF, hours, phone)
  FHWA NTAD (public)   ~1 900 public truck parking facilities (rest areas, welcome centers) with truck spaces,
                       already in backend/data/truck_parking_capacity.json (scripts/import_ntad.py)
  OpenStreetMap (ODbL) fuel stations marked for trucks (hgv=yes), service plazas and rest areas: the dealer and
                       independent stops no chain list has (the 49er Travel Plaza is a Pilot dealer). ODbL: the app
                       credits "© OpenStreetMap contributors", and the OSM-derived rows are offered under ODbL
                       (data/places is a derivative database once merged)
  Overture Maps        independent truck stops by name and budget motels (CDLA-Permissive-2.0 / Apache-2.0
                       per record; attribution "© Overture Maps Foundation"); needs `pip install duckdb`

A spider of AllThePlaces is sometimes empty or cut short in a weekly run (Pilot empty and Walmart at a third of its
size in late October 2026), so each one comes from the newest run that has at least 90 % of the spider's best
count in the last RUNS_BACK runs (the per-run stats file says the counts). Run monthly:

    python scripts/build_places.py                # everything (Overture scan takes minutes, ~GBs read)
    python scripts/build_places.py --no-overture  # chains + NTAD only

Rows: id, name, category, lat, lng, tags, address, source (+ phone in tags, as the CAT scale rows do).
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Iterable, Optional

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "places"
NTAD = ROOT / "data" / "truck_parking_capacity.json"
ATP_RUNS = "https://data.alltheplaces.xyz/runs/history.json"
ATP_SPIDER = "https://alltheplaces-data.openaddresses.io/runs/{run}/output/{spider}.geojson"
ATP_STATS = "https://alltheplaces-data.openaddresses.io/runs/{run}/stats/_results.json"
OVERTURE = "s3://overturemaps-us-west-2/release/{release}/theme=places/type=place/*"
OVERTURE_RELEASES = "https://overturemaps-us-west-2.s3.amazonaws.com/?list-type=2&prefix=release/&delimiter=/"
US_BBOX = (-125.0, 24.4, -66.9, 49.5)  # lower 48
RUNS_BACK = 8  # how many weekly runs to look back for a non-empty spider

# spider -> category; "hgv_only" keeps only features the chain marks for trucks (hgv / truck diesel)
SPIDERS = {
    "pilot_flying_j": ("truck_stop", False),
    "loves_us": ("truck_stop", False),
    "travelcenters_of_america_us": ("truck_stop", False),
    "roadys_us": ("truck_stop", False),
    "maverik": ("truck_stop", True),
    "kwik_trip": ("truck_stop", True),
    "speedway_us": ("truck_stop", True),
    "walmart_us": ("walmart", False),
    "blue_beacon_us": ("truck_wash", False),
}
# Who wins when two sources describe the same place (lower = better).
PRIORITY = {"atp": 0, "ntad": 1, "osm": 2, "overture": 3}
OVERPASS = ("https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter")
OVERPASS_QUERY = """[out:json][timeout:900];
area["ISO3166-1"="US"][admin_level=2]->.us;
(
  nwr["amenity"="fuel"]["hgv"~"^(yes|designated)$"](area.us);
  nwr["highway"="services"](area.us);
  nwr["highway"="rest_area"](area.us);
);
out center tags;"""
SAME_PLACE_M = {"truck_stop": 300, "walmart": 250, "truck_wash": 200, "rest_area": 400, "hotel": 80}

TRUCK_STOP_NAME = re.compile(r"\btruck ?stop\b|\btruckstop\b|\btruck plaza\b|\btravel plaza\b|\bauto ?truck plaza\b|"
                             r"\btravel (center|centre|stop)\b|\bfuel stop\b", re.I)
# A "travel center" can be a travel agency, a "truck stop" a diner: only these kinds of place count.
TRUCK_STOP_KINDS = {"truck_stop", "truck_gas_station", "gas_station", "convenience_store", "travel_and_transportation",
                    "restaurant", "american_restaurant", "diner", "parking", "fuel_station", "service_station"}
NOT_A_STOP = re.compile(r"\bcat scale\b|\bwash\b|\brepair\b|\btire\b|\bparts\b|\bservice center\b|\bagency\b|"
                        r"\btravel (agent|agency|service)\b", re.I)
# Big service stations that ban or do not take trucks.
NO_TRUCKS = re.compile(r"buc-?ee'?s", re.I)
# Budget motels at exits: where a driver might find a truck spot. Downtown hotels are left out.
MOTEL_NAME = re.compile(r"\bmotel\b|\bmotor ?inn\b|\bmotor lodge\b|motel 6|super 8|days inn|red roof|econo ?lodge|"
                        r"travelodge|rodeway|americas? best value|budget inn|knights inn|baymont|quality inn|"
                        r"howard johnson|studio 6|scottish inns|red carpet inn|ramada", re.I)


def meters(a: dict, b: dict) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lng"], b["lat"], b["lng"]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def in_us(lat: float, lng: float) -> bool:
    x0, y0, x1, y1 = US_BBOX
    return y0 <= lat <= y1 and x0 <= lng <= x1


def _yes(p: dict, *keys: str) -> bool:
    return any(str(p.get(k, "")).lower() in ("yes", "true", "1") for k in keys)


def _phone(p: dict) -> Optional[str]:
    digits = re.sub(r"\D", "", str(p.get("phone") or "").split(";")[0])
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == 10 else None


def _address(p: dict) -> Optional[str]:
    if p.get("addr:full"):
        return str(p["addr:full"]).strip()
    street = p.get("addr:street_address") or " ".join(x for x in (p.get("addr:housenumber"), p.get("addr:street")) if x)
    parts = [street, p.get("addr:city"), " ".join(x for x in (p.get("addr:state"), p.get("addr:postcode")) if x)]
    text = ", ".join(str(x).strip() for x in parts if x and str(x).strip())
    return text or None


def atp_row(feature: dict, spider: str, category: str, hgv_only: bool) -> Optional[dict]:
    """One AllThePlaces GeoJSON feature -> our row, or None when it is not one of ours."""
    p = feature.get("properties") or {}
    geom = feature.get("geometry") or {}
    if geom.get("type") != "Point":
        return None
    lng, lat = geom["coordinates"][:2]
    if not in_us(lat, lng) or (p.get("addr:country") not in (None, "", "US")):  # the box also holds Toronto
        return None
    name = str(p.get("name") or p.get("brand") or "").strip()
    if not name or "coming soon" in str(p.get("branch") or "").lower():
        return None
    if hgv_only and not _yes(p, "hgv", "fuel:HGV_diesel"):
        return None
    if category == "walmart" and ("neighborhood" in name.lower() or "pharmacy" in name.lower()):
        return None  # no room for a truck at a Neighborhood Market
    tags: list[str] = []
    if category == "truck_stop":
        if _yes(p, "fuel:HGV_diesel", "hgv") or spider in ("pilot_flying_j", "loves_us", "travelcenters_of_america_us"):
            tags.append("Truck diesel")
        if _yes(p, "fuel:adblue"):
            tags.append("DEF")
        if _yes(p, "shower"):
            tags.append("Showers")
        if _yes(p, "truck_wash"):
            tags.append("Truck wash")
        if _yes(p, "laundry"):
            tags.append("Laundry")
    if category == "truck_wash":
        tags.append("Truck wash")
    if str(p.get("opening_hours") or "").replace(" ", "") in ("24/7", "Mo-Su00:00-24:00"):
        tags.append("24/7")
    cap = str(p.get("capacity:hgv") or "")
    if cap.isdigit() and int(cap) > 0:
        tags.append(f"{int(cap)} truck spots")
    phone = _phone(p)
    if phone:
        tags.append(phone)
    branch = str(p.get("branch") or "").strip()
    display = name if not branch or branch.lower() in name.lower() else f"{name} {branch}"
    ref = str(p.get("ref") or f"{lat:.5f},{lng:.5f}")
    return {"id": f"atp-{spider}-{ref}"[:80], "name": display, "category": category, "lat": round(lat, 6),
            "lng": round(lng, 6), "tags": tags, "address": _address(p), "source": "atp"}


def ntad_rows(raw: list[dict]) -> list[dict]:
    """FHWA NTAD public truck parking -> rest areas (every facility there is public parking with truck spaces)."""
    out = []
    for r in raw:
        try:
            lat, lng = float(r["lat"]), float(r["lng"])
        except (KeyError, TypeError, ValueError):
            continue
        if not in_us(lat, lng) or not r.get("name"):
            continue
        where = " ".join(x for x in (r.get("highway"), f"mile {r['milepost']}" if r.get("milepost") else None, r.get("state")) if x)
        tags = ["Truck parking"] + ([f"{r['spots']} truck spots"] if r.get("spots") else [])
        out.append({"id": f"rest-{r['id']}", "name": str(r["name"]).strip(), "category": "rest_area", "lat": round(lat, 6),
                    "lng": round(lng, 6), "tags": tags, "address": where or None, "source": "ntad"})
    return out


def osm_row(e: dict) -> Optional[dict]:
    """One Overpass element -> our row: hgv fuel and service plazas are truck stops, rest areas are rest areas."""
    t = e.get("tags") or {}
    lat = e.get("lat") if "lat" in e else (e.get("center") or {}).get("lat")
    lng = e.get("lon") if "lon" in e else (e.get("center") or {}).get("lon")
    if lat is None or lng is None or not in_us(lat, lng):
        return None
    if NO_TRUCKS.search(str(t.get("name") or t.get("brand") or "")):
        return None
    if t.get("highway") == "rest_area":
        cat, name = "rest_area", t.get("name") or "Rest Area"
    elif t.get("highway") == "services":
        name = t.get("name") or t.get("brand")
        if not name:
            return None  # "Service Plaza" is nothing a driver can find or a voice can say
        cat = "truck_stop"
    elif t.get("amenity") == "fuel":
        name = t.get("name") or t.get("brand")
        if not name:
            return None  # an unnamed pump cannot be said or found
        cat = "truck_stop"
    else:
        return None
    tags: list[str] = []
    if cat == "truck_stop" and t.get("amenity") == "fuel":
        tags.append("Truck diesel")
    if t.get("hgv") in ("yes", "designated") and cat == "rest_area":
        tags.append("Truck parking")
    if _yes(t, "shower"):
        tags.append("Showers")
    if t.get("opening_hours") == "24/7":
        tags.append("24/7")
    phone = _phone(t)
    if phone:
        tags.append(phone)
    kind = "services" if t.get("highway") == "services" else "fuel" if t.get("amenity") == "fuel" else "rest_area"
    return {"id": f"osm-{e['type'][0]}{e['id']}", "name": str(name).strip(), "category": cat, "lat": round(lat, 6),
            "lng": round(lng, 6), "tags": tags, "address": _address(t), "source": "osm", "kind": kind}


def osm_elements(client: httpx.Client) -> list[dict]:
    for url in OVERPASS:
        try:
            res = client.post(url, data={"data": OVERPASS_QUERY}, timeout=1000,
                              headers={"User-Agent": "TruckBuddy/1.0 (places build)"})
            res.raise_for_status()
            return res.json().get("elements") or []
        except Exception as e:  # noqa: BLE001 - try the next mirror
            print(f"overpass {url}: {e}", file=sys.stderr)
    return []


def overture_kind(name: str, kind: Optional[str]) -> Optional[str]:
    """Category of an Overture place by its name and taxonomy, or None when it is none of ours."""
    kind = (kind or "").lower()
    if NO_TRUCKS.search(name):
        return None
    if kind in ("truck_stop", "truck_gas_station") and not NOT_A_STOP.search(name):
        return "truck_stop"
    if TRUCK_STOP_NAME.search(name) and kind in TRUCK_STOP_KINDS and not NOT_A_STOP.search(name):
        return "truck_stop"
    if kind in ("motel", "hotel") and MOTEL_NAME.search(name):
        return "hotel"
    return None


def overture_row(r: dict) -> Optional[dict]:
    cat = "walmart" if r.get("brand") == "Walmart" or (r["name"] or "").lower().startswith("walmart supercenter") \
        else overture_kind(r["name"] or "", r.get("kind"))
    if cat == "walmart" and re.search(r"neighborhood|pharmacy|vision|auto care|fuel|money|photo|bakery|deli|garden|"
                                      r"pickup|tire|optical|grocery|liquor|connection", r["name"] or "", re.I):
        return None  # a department of a store, or a store without truck room
    if not cat or (r.get("confidence") or 0) < 0.5 or (r.get("status") or "open") != "open":
        return None
    if not in_us(r["lat"], r["lng"]):
        return None
    tags = []
    phone = _phone({"phone": r.get("phone")})
    if phone:
        tags.append(phone)
    return {"id": f"ov-{r['id']}"[:80], "name": r["name"].strip(), "category": cat, "lat": round(r["lat"], 6),
            "lng": round(r["lng"], 6), "tags": tags, "address": r.get("address"), "source": "overture"}


def _same_place_m(a: dict, b: dict) -> float:
    """How close two rows must be to be one place (0 = never the same)."""
    if a["source"] != b["source"] or a["category"] == "walmart":
        return SAME_PLACE_M.get(a["category"], 150)
    if a["source"] == "overture" and a["category"] == "truck_stop":
        return 80
    if a["source"] == "osm" and {a.get("kind"), b.get("kind")} == {"services", "fuel"}:
        return 300  # the fuel island inside a mapped travel plaza: one stop
    return 0


def dedupe(rows: Iterable[dict]) -> list[dict]:
    """One row per real place: a row from another source within SAME_PLACE_M in the same category is the same place,
    and the better source wins (chain site > NTAD > Overture); a loser's tags the winner lacks are kept. Rows of
    one source are never merged with each other: a Pilot and a Love's across the road at one exit are two stops,
    and the two sides of a rest area are two facilities (a driver can only reach the one on their side). Except where
    a source repeats itself: Overture often has one store or truck stop as two records a few meters apart, and no
    two Walmarts stand within SAME_PLACE_M."""
    rows = sorted(rows, key=lambda r: (PRIORITY.get(r["source"], 9), r["id"]))
    kept: list[dict] = []
    grid: dict[tuple[int, int], list[dict]] = {}
    for r in rows:
        cell = (int(r["lat"] * 100), int(r["lng"] * 100))  # ~1 km cells
        near = [k for dx in (-1, 0, 1) for dy in (-1, 0, 1) for k in grid.get((cell[0] + dx, cell[1] + dy), ())]
        # the nearest one: an Overture copy of a Pilot belongs to that Pilot, not to the Love's across the road
        twins = [(meters(k, r), i, k) for i, k in enumerate(near) if k["category"] == r["category"]
                 and meters(k, r) <= _same_place_m(k, r)]
        twin = min(twins)[2] if twins else None
        if twin:
            if r.get("kind") == "services" and twin.get("kind") == "fuel":
                twin["name"] = r["name"]  # "Sacramento 49er Travel Plaza", not its "Pilot" pumps
            twin["tags"] += [t for t in r["tags"] if t not in twin["tags"]]
            if not twin.get("address") and r.get("address"):
                twin["address"] = r["address"]
            continue
        kept.append(r)
        grid.setdefault(cell, []).append(r)
    return kept


def pick_run(counts: dict[str, int]) -> Optional[str]:
    """{run: features} -> the newest run with at least 90 % of the best count (None when all are empty)."""
    best = max(counts.values(), default=0)
    if best <= 0:
        return None
    return max(run for run, n in counts.items() if n >= 0.9 * best)


def atp_counts(client: httpx.Client) -> dict[str, dict[str, int]]:
    """{spider: {run: features}} over the last RUNS_BACK runs, from each run's stats file."""
    runs = [r["run_id"] for r in client.get(ATP_RUNS).json()][-RUNS_BACK:]
    out: dict[str, dict[str, int]] = {s: {} for s in SPIDERS}
    for run in runs:
        res = client.get(ATP_STATS.format(run=run))
        if res.status_code != 200:
            continue
        stats = res.json()
        for r in stats.get("results", stats) if isinstance(stats, dict) else stats:
            if r.get("spider") in out:
                out[r["spider"]][run] = int(r.get("features") or 0)
    return out


def atp_features(client: httpx.Client, spider: str, run: str) -> list[dict]:
    res = client.get(ATP_SPIDER.format(run=run, spider=spider))
    return (res.json().get("features") or []) if res.status_code == 200 and res.content.strip() else []


def overture_rows(limit_bbox: tuple[float, float, float, float] = US_BBOX) -> list[dict]:
    import duckdb  # build-time only

    releases = re.findall(r"<Prefix>release/([^<]+)/</Prefix>", httpx.get(OVERTURE_RELEASES, timeout=30).text)
    release = sorted(releases)[-1]
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
    x0, y0, x1, y1 = limit_bbox
    q = f"""
    SELECT id, names."primary" AS name, taxonomy."primary" AS kind, bbox.xmin AS lng, bbox.ymin AS lat, confidence,
           operating_status AS status, brand.names."primary" AS brand,
           addresses[1].freeform || coalesce(', ' || addresses[1].locality, '') || coalesce(', ' || addresses[1].region, '')
             AS address,
           phones[1] AS phone
    FROM read_parquet('{OVERTURE.format(release=release)}', hive_partitioning=1)
    WHERE bbox.xmin BETWEEN {x0} AND {x1} AND bbox.ymin BETWEEN {y0} AND {y1}
      AND addresses[1].country = 'US'  -- the box also holds southern Ontario and Tijuana
      AND (taxonomy."primary" IN ('truck_stop', 'truck_gas_station', 'motel', 'hotel')
           OR (brand.names."primary" = 'Walmart' OR names."primary" ILIKE 'Walmart Supercenter%')
           OR regexp_matches(lower(names."primary"), 'truck ?stop|truckstop|truck plaza|travel plaza|travel cent|travel stop|fuel stop'))
    """
    cols = ["id", "name", "kind", "lng", "lat", "confidence", "status", "brand", "address", "phone"]
    print(f"overture {release}: scanning…", file=sys.stderr)
    return [dict(zip(cols, t)) for t in con.execute(q).fetchall()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-overture", action="store_true")
    ap.add_argument("--no-osm", action="store_true")
    ap.add_argument("--osm-file", help="a saved Overpass JSON answer instead of querying (Overpass takes minutes)")
    a = ap.parse_args()
    rows: list[dict] = []
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        counts = atp_counts(client)
        for spider, (category, hgv_only) in SPIDERS.items():
            run = pick_run(counts[spider]) or ""
            feats = atp_features(client, spider, run) if run else []
            got = [x for x in (atp_row(f, spider, category, hgv_only) for f in feats) if x]
            print(f"atp {spider}: run {run or '-'}, {len(feats)} features, {len(got)} kept", file=sys.stderr)
            rows += got
    rows += ntad_rows(json.loads(NTAD.read_text()))
    if not a.no_osm:
        if a.osm_file:
            elements = json.loads(Path(a.osm_file).read_text()).get("elements") or []
        else:
            with httpx.Client(follow_redirects=True) as client:
                elements = osm_elements(client)
        osm = [x for x in (osm_row(e) for e in elements) if x]
        print(f"osm: {len(elements)} elements, {len(osm)} kept", file=sys.stderr)
        rows += osm
    if not a.no_overture:
        ov = [x for x in (overture_row(r) for r in overture_rows()) if x]
        print(f"overture: {len(ov)} kept", file=sys.stderr)
        rows += ov
    OUT.mkdir(parents=True, exist_ok=True)
    final = dedupe(rows)
    for category in sorted({r["category"] for r in final}):
        part = sorted(({k: v for k, v in r.items() if k != "kind"} for r in final if r["category"] == category),
                      key=lambda r: r["id"])
        (OUT / f"{category}.json").write_text(json.dumps(part, separators=(",", ":")) + "\n")
        print(f"{category}: {len(part)}", file=sys.stderr)


if __name__ == "__main__":
    main()
