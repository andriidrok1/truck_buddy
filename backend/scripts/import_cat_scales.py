#!/usr/bin/env python3
"""CAT Scale locations -> backend/data/cat_scale.json (served by the places provider, category cat_scale).

Source: the official CSV export behind catscale.com's locator download
(https://catscale.com/exl.php). Their terms allow internal business use; before shipping the
category, get CAT's written OK (partners@catscale.com) or switch to a Google text search.

    python scripts/import_cat_scales.py              # downloads the CSV
    python scripts/import_cat_scales.py path/to.csv  # uses a local copy

Run monthly. US only (Canada rows are dropped). Rows: id, name, category, lat, lng, tags, address.
"""
import csv
import io
import json
import sys
from pathlib import Path

import httpx

URL = "https://catscale.com/exl.php"
OUT = Path(__file__).resolve().parents[1] / "data" / "cat_scale.json"
US = set("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH "
         "OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split())


def rows_from_csv(text: str) -> list[dict]:
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        state = (r.get("State") or "").strip().upper()
        try:
            lat, lng = float(r["Latitude"]), float(r["Longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if state not in US or not (24 <= lat <= 50 and -125.5 <= lng <= -66):
            continue
        host = (r.get("TruckstopName") or "").strip() or "truck stop"
        city = (r.get("InterstateCity") or "").strip()
        street = (r.get("InterstateAddress") or "").strip()
        zip_ = (r.get("InterstatePostalCode") or "").strip()
        address = ", ".join(x for x in (street, f"{city}, {state} {zip_}".strip()) if x)
        tags = ["CAT Scale"]
        phone = (r.get("PhoneNumber") or "").strip()
        if phone:
            tags.append(phone)
        out.append({"id": f"cat-{(r.get('CATScaleNumber') or '').strip()}", "name": f"CAT Scale at {host}",
                    "category": "cat_scale", "lat": round(lat, 6), "lng": round(lng, 6), "tags": tags,
                    "address": address})
    out.sort(key=lambda x: x["id"])
    return out


def main() -> None:
    if len(sys.argv) > 1:
        text = Path(sys.argv[1]).read_text(encoding="utf-8-sig")
    else:
        r = httpx.get(URL, timeout=60, follow_redirects=True)
        r.raise_for_status()
        text = r.content.decode("utf-8-sig")
    rows = rows_from_csv(text)
    if len(rows) < 1500:
        sys.exit(f"only {len(rows)} US rows parsed; the export format may have changed, nothing written")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"wrote {len(rows)} CAT scales to {OUT}")


if __name__ == "__main__":
    main()
