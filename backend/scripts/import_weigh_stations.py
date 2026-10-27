#!/usr/bin/env python3
"""Weigh station locations from OpenStreetMap -> backend/data/weigh_station.json (category weigh_station).

OSM tags weigh stations as amenity=weighbridge, which also covers grain elevators and private
scales, so only named stations that look like enforcement sites are kept (Weigh, Inspection,
Port of Entry, Scales, DOT, Highway Patrol). Status (open/closed) does not exist in OSM.

    python scripts/import_weigh_stations.py                  # queries Overpass (slow, ~1 min)
    python scripts/import_weigh_stations.py overpass.json    # uses a saved Overpass response

Re-run a few times a year. ODbL: keep the "© OpenStreetMap contributors" line in the app's about screen.
"""
import json
import re
import sys
from pathlib import Path

import httpx

OUT = Path(__file__).resolve().parents[1] / "data" / "weigh_station.json"
MIRRORS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
           "https://lz4.overpass-api.de/api/interpreter")
QUERY = """
[out:json][timeout:180];
area["ISO3166-1"="US"][admin_level=2]->.us;
(
  nwr["amenity"="weighbridge"](area.us);
  nwr["highway"="weigh_station"](area.us);
  nwr["amenity"="weigh_station"](area.us);
);
out center tags;
"""
KEEP = re.compile(r"weigh|inspection|port of entry|\bscales?\b|\bdot\b|highway patrol|\bpoe\b|check(?:ing)? station",
                  re.I)
DROP = re.compile(r"grain|elevator|co-?op|feed|landfill|quarry|recycl|scrap|mill|farm|ranch|cement|gravel|transfer",
                  re.I)


def rows_from_overpass(data: dict) -> list[dict]:
    out, seen = [], set()
    for el in data.get("elements") or []:
        tags = el.get("tags") or {}
        name = (tags.get("name") or tags.get("official_name") or "").strip()
        operator = (tags.get("operator") or "").strip()
        label = f"{name} {operator}"
        if not name or not KEEP.search(label) or DROP.search(label):
            continue
        lat = el.get("lat") if "lat" in el else (el.get("center") or {}).get("lat")
        lng = el.get("lon") if "lon" in el else (el.get("center") or {}).get("lon")
        if lat is None or lng is None or not (24 <= lat <= 50 and -125.5 <= lng <= -66):
            continue
        key = (round(lat, 3), round(lng, 3))
        if key in seen:
            continue
        seen.add(key)
        tags_out = ["Weigh station"]
        if operator:
            tags_out.append(operator)
        if tags.get("opening_hours"):
            tags_out.append(tags["opening_hours"])
        road = tags.get("addr:street") or tags.get("highway:ref") or tags.get("ref") or ""
        out.append({"id": f"osm-{el.get('type', 'n')[0]}{el.get('id')}", "name": name, "category": "weigh_station",
                    "lat": round(lat, 6), "lng": round(lng, 6), "tags": tags_out, "address": road or None})
    out.sort(key=lambda x: x["id"])
    return out


def fetch() -> dict:
    last = None
    for url in MIRRORS:
        try:
            r = httpx.post(url, data={"data": QUERY}, timeout=240)
            if r.status_code == 200:
                return r.json()
            last = f"{url}: {r.status_code}"
        except httpx.HTTPError as e:
            last = f"{url}: {e}"
        print(f"  ! {last}")
    sys.exit(f"every Overpass mirror failed ({last})")


def main() -> None:
    data = json.loads(Path(sys.argv[1]).read_text()) if len(sys.argv) > 1 else fetch()
    rows = rows_from_overpass(data)
    if len(rows) < 200:
        sys.exit(f"only {len(rows)} stations parsed; check the query/filters, nothing written")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"wrote {len(rows)} weigh stations to {OUT}")


if __name__ == "__main__":
    main()
