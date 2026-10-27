#!/usr/bin/env python3
"""Record provider responses once, with real keys, into tests/fixtures/<provider>/*.json.

Manual tool, never run in CI. Reads backend/.env. Keys are stripped from what is written (the
request URL is not stored at all). Usage:

    python scripts/record_fixtures.py                 # everything a key exists for
    python scripts/record_fixtures.py --only here,nws # a subset

Scenarios are fixed so tests can rely on them: Sacramento -> Reno over Donner (traffic, speed
limits, incidents, alternatives, tolls, route handle in one response) and Sacramento -> SF.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
FIX = ROOT / "tests" / "fixtures"

SAC = (38.5816, -121.4944)
RENO = (39.5296, -119.8138)
SF = (37.7749, -122.4194)
DONNER = (39.3167, -120.3333)

TRUCK = {"vehicle[height]": "411", "vehicle[length]": "2225", "vehicle[grossWeight]": "36288"}  # 13'6", 73 ft, 80k lb
SECRET_KEYS = {"apikey", "api_key", "key", "x-goog-api-key", "access_token", "token"}


def _scrub(obj):
    """Drop anything that looks like a credential or a personal photo URL, recursively."""
    if isinstance(obj, dict):
        return {k: _scrub(v) for k, v in obj.items()
                if k.lower() not in SECRET_KEYS and k not in ("photoUri",)}
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    return obj


def _write(provider: str, name: str, data, scenario: str) -> None:
    out = FIX / provider
    out.mkdir(parents=True, exist_ok=True)
    body = _scrub(data)
    if isinstance(body, dict):
        body = {"_meta": {"recorded_at": time.strftime("%Y-%m-%d"), "provider": provider, "scenario": scenario},
                **body}
    (out / f"{name}.json").write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n")
    print(f"  wrote {provider}/{name}.json")


def _get(url: str, params=None, headers=None, timeout=30) -> httpx.Response:
    r = httpx.get(url, params=params, headers=headers, timeout=timeout)
    if r.is_error:
        print(f"  ! {url.split('?')[0]} -> {r.status_code}: {r.text[:200]}")
    return r


# ---- HERE -------------------------------------------------------------------------------------

HERE_URL = "https://router.hereapi.com/v8/routes"


def here():
    key = os.environ.get("HERE_API_KEY")
    if not key:
        print("here: no HERE_API_KEY, skipped")
        return
    base = [("transportMode", "truck"), ("lang", "en-US"), ("units", "imperial"),
            ("return", "polyline,summary,actions,instructions,incidents,routeLabels,tolls,routeHandle"),
            ("spans", "dynamicSpeedInfo,maxSpeed,incidents"), ("currency", "USD"),
            *TRUCK.items(), ("apiKey", key)]
    for name, o, d, alt in (("route_sac_reno", SAC, RENO, 2), ("route_sac_sf", SAC, SF, 0)):
        params = base + [("origin", f"{o[0]},{o[1]}"), ("destination", f"{d[0]},{d[1]}")]
        if alt:
            params.append(("alternatives", str(alt)))
        r = _get(HERE_URL, params)
        if r.is_error:
            continue
        data = r.json()
        _write("here", name, data, f"{o} -> {d}, alternatives={alt}")
        handle = (data.get("routes") or [{}])[0].get("routeHandle")
        if name == "route_sac_reno" and handle:
            # Re-route from a point just off the line near Auburn, heading east.
            p = [("origin", "38.9100,-121.0700;course=80"), ("rerouting[mode]", "returnToRoute"),
                 ("rerouting[lastTraveledSectionIndex]", "0"), ("rerouting[traveledDistanceOnLastSection]", "50000"),
                 ("return", "polyline,summary,actions,instructions,incidents,routeHandle"),
                 ("spans", "dynamicSpeedInfo,maxSpeed,incidents"), ("lang", "en-US"), ("units", "imperial"),
                 *TRUCK.items(), ("transportMode", "truck"), ("apiKey", key)]
            r2 = _get(f"{HERE_URL}/{handle}", p)
            if not r2.is_error:
                _write("here", "reroute_ok", r2.json(), "returnToRoute from near Auburn on the Sac->Reno handle")
            r3 = _get(f"{HERE_URL}/{handle[:40]}xx", p)
            _write("here", "reroute_handle_bad", _status_body(r3), "corrupted handle -> error body as HERE returns it")
    # Error shape for a request HERE rejects, for parse-tolerance tests.
    r = _get(HERE_URL, base + [("origin", "0,0"), ("destination", "0,1")])
    _write("here", "error_no_route", _status_body(r), "origin/destination in the ocean")


def _status_body(r: httpx.Response) -> dict:
    is_json = "json" in r.headers.get("content-type", "")
    return {"status": r.status_code, "body": r.json() if is_json else r.text[:500]}


# ---- NWS --------------------------------------------------------------------------------------

NWS = "https://api.weather.gov"
UA = os.environ.get("NWS_USER_AGENT", "(truckbuddy-fixtures, dev@truckbuddy.io)")


def nws():
    h = {"User-Agent": UA, "Accept": "application/geo+json"}
    r = _get(f"{NWS}/alerts/active", {"point": f"{DONNER[0]},{DONNER[1]}"}, h)
    if not r.is_error:
        _write("nws", "alerts_point_donner", r.json(), "active alerts at Donner Pass (may be empty)")
    r = _get(f"{NWS}/alerts/active", {"area": "CA"}, h)
    if not r.is_error:
        d = r.json()
        d["features"] = d.get("features", [])[:12]
        _write("nws", "alerts_area_ca", d, "active alerts for CA, first 12")
    r = _get(f"{NWS}/points/{RENO[0]},{RENO[1]}", headers=h)
    if not r.is_error:
        pts = r.json()
        _write("nws", "points_reno", pts, "gridpoint lookup for Reno")
        url = pts.get("properties", {}).get("forecastHourly")
        if url:
            r2 = _get(url, headers=h)
            if not r2.is_error:
                d = r2.json()
                d["properties"]["periods"] = d["properties"].get("periods", [])[:36]
                _write("nws", "forecast_hourly_reno", d, "hourly forecast Reno, first 36 periods")


# ---- Caltrans chain controls ------------------------------------------------------------------

def caltrans():
    for dist in (3, 10):
        r = _get(f"https://cwwp2.dot.ca.gov/data/d{dist}/cc/ccStatusD{dist:02d}.json")
        if not r.is_error:
            _write("caltrans", f"ccStatusD{dist:02d}", r.json(), f"chain control status, district {dist}")


# ---- TPIMS ------------------------------------------------------------------------------------

TPIMS = {
    "wi": ("https://511wi.gov/api/TPIMS_Dynamic", "https://511wi.gov/api/TPIMS_Static"),
    "ky": ("http://www.trimarc.org/dat/tpims/TPIMS_Dynamic.json", "http://www.trimarc.org/dat/tpims/TPIMS_Static.json"),
    "il": ("https://truckparking.travelmidwest.com/TPIMS_Dynamic.json",
           "https://truckparking.travelmidwest.com/TPIMS_Static.json"),
}


def tpims():
    for st, (dyn, stat) in TPIMS.items():
        for kind, url in (("dynamic", dyn), ("static", stat)):
            try:
                r = _get(url, timeout=20)
            except httpx.HTTPError as e:
                print(f"  ! {url}: {e}")
                continue
            if r.is_error:
                continue
            try:
                data = r.json()
            except ValueError:
                print(f"  ! {url}: not JSON")
                continue
            if isinstance(data, list):
                data = data[:20]
            _write("tpims", f"{st}_{kind}", data, f"TPIMS {kind} feed, {st.upper()}, first 20 sites")


# ---- EIA --------------------------------------------------------------------------------------

def eia():
    key = os.environ.get("EIA_API_KEY", "DEMO_KEY")
    params = {"api_key": key, "frequency": "weekly", "data[0]": "value", "facets[product][]": "EPD2D",
              "sort[0][column]": "period", "sort[0][direction]": "desc", "length": "12"}
    for area in ("R50", "NUS"):
        r = _get("https://api.eia.gov/v2/petroleum/pri/gnd/data/", {**params, "facets[duoarea][]": area})
        if not r.is_error:
            _write("eia", f"diesel_weekly_{area.lower()}", r.json(), f"weekly No 2 diesel retail, {area}, last 12")


# ---- Google -----------------------------------------------------------------------------------

G_URL = "https://places.googleapis.com/v1/places:searchText"
G_MASK = ("places.id,places.displayName,places.rating,places.userRatingCount,places.editorialSummary,"
          "places.formattedAddress,places.location,places.types,places.primaryType,places.fuelOptions,places.reviews")


def google():
    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        print("google: no GOOGLE_MAPS_API_KEY, skipped")
        return
    h = {"X-Goog-Api-Key": key, "X-Goog-FieldMask": G_MASK, "Content-Type": "application/json"}
    bias = {"circle": {"center": {"latitude": SAC[0], "longitude": SAC[1]}, "radius": 25000}}
    for name, body in (
        ("truck_stop_sac", {"textQuery": "truck stop", "includedType": "truck_stop", "strictTypeFiltering": True}),
        ("walmart_sac", {"textQuery": "Walmart Supercenter"}),
        ("truck_wash_sac", {"textQuery": "truck wash", "includedType": "car_wash"}),
        ("cat_scale_sac", {"textQuery": "CAT Scale"}),
    ):
        r = httpx.post(G_URL, headers=h, json={**body, "locationBias": bias, "maxResultCount": 10}, timeout=30)
        if r.is_error:
            print(f"  ! google {name} -> {r.status_code}: {r.text[:200]}")
            continue
        _write("google", name, r.json(), f"Text Search near Sacramento: {body['textQuery']}")


# ---- Motive -----------------------------------------------------------------------------------

def motive():
    key = os.environ.get("MOTIVE_API_KEY")
    if not key:
        print("motive: no MOTIVE_API_KEY, skipped (hand-written fixture stays)")
        return
    r = _get("https://api.gomotive.com/v1/available_time", headers={"X-Api-Key": key})
    if not r.is_error:
        data = r.json()
        for u in data.get("users", []):
            if "user" in u:
                u["user"] = {"id": 1, "first_name": "Test", "last_name": "Driver"}
        _write("motive", "available_time", data, "available_time for the key owner's drivers")


ALL = {"here": here, "nws": nws, "caltrans": caltrans, "tpims": tpims, "eia": eia, "google": google, "motive": motive}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma-separated subset of: " + ",".join(ALL))
    args = ap.parse_args()
    names = [n.strip() for n in args.only.split(",") if n.strip()] or list(ALL)
    bad = [n for n in names if n not in ALL]
    if bad:
        sys.exit(f"unknown provider(s): {bad}")
    for n in names:
        print(f"{n}:")
        ALL[n]()
