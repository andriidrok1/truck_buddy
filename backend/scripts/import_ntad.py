#!/usr/bin/env python3
"""FHWA NTAD "Truck Stop Parking" (Jason's Law survey) -> backend/data/truck_parking_capacity.json.

Public-domain layer of ~1,900 public truck parking facilities (rest areas, welcome centers, some
weigh stations) with the number of truck spaces. Used as the capacity fallback when there is no
live TPIMS count. Re-run yearly.

    python scripts/import_ntad.py                # downloads the layer
    python scripts/import_ntad.py saved.json     # uses a saved ArcGIS query response
"""
import json
import sys
from pathlib import Path

import httpx

URL = ("https://services.arcgis.com/xOi1kZaI0eWDREZv/ArcGIS/rest/services/NTAD_Truck_Stop_Parking/FeatureServer/0"
       "/query")
OUT = Path(__file__).resolve().parents[1] / "data" / "truck_parking_capacity.json"
PAGE = 2000


def rows_from_arcgis(data: dict) -> list[dict]:
    out = []
    for f in data.get("features") or []:
        a = f.get("attributes") or {}
        g = f.get("geometry") or {}
        lat, lng = a.get("latitude", g.get("y")), a.get("longitude", g.get("x"))
        spots = a.get("number_of_spots")
        if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)) or not isinstance(spots, int):
            continue
        if spots <= 0 or not (24 <= lat <= 50 and -125.5 <= lng <= -66):
            continue
        out.append({"id": f"ntad-{a.get('OBJECTID')}", "name": (a.get("nhs_rest_stop") or "").strip() or "Rest area",
                    "highway": (a.get("highway_route") or "").strip() or None,
                    "milepost": (a.get("mile_post") or "").strip() or None, "state": (a.get("state") or "").strip(),
                    "lat": round(float(lat), 6), "lng": round(float(lng), 6), "spots": spots})
    out.sort(key=lambda r: r["id"])
    return out


def fetch() -> dict:
    features, offset = [], 0
    while True:
        params = {"where": "1=1", "outFields": "*", "returnGeometry": "true", "f": "json", "outSR": "4326",
                  "resultRecordCount": str(PAGE), "resultOffset": str(offset)}
        r = httpx.get(URL, params=params, timeout=120)
        r.raise_for_status()
        page = r.json()
        features += page.get("features") or []
        if not page.get("exceededTransferLimit"):
            return {"features": features}
        offset += PAGE


def main() -> None:
    data = json.loads(Path(sys.argv[1]).read_text()) if len(sys.argv) > 1 else fetch()
    rows = rows_from_arcgis(data)
    if len(rows) < 1000:
        sys.exit(f"only {len(rows)} facilities parsed; the layer may have changed, nothing written")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"wrote {len(rows)} facilities, {sum(r['spots'] for r in rows)} truck spaces, to {OUT}")


if __name__ == "__main__":
    main()
