# Manual probe: geocode edge phrases through /api/agent (not collected by pytest)
import os
import requests

BASE_URL = os.environ.get("EXPO_PUBLIC_BACKEND_URL").rstrip("/")
for t in ["take me to 1600 Amphitheatre Parkway Mountain View", "take me to Sacramento airport",
          "take me to Walmart in Stockton", "take me to Denver", "take me to Springfield",
          "take me to Asdfgh", "take me to the Golden Gate Bridge", "take me to Main Street",
          "take me to Fresno, CA"]:
    d = requests.post(f"{BASE_URL}/api/agent", json={"text": t, "lat": 38.5816, "lng": -121.4944}, timeout=120).json()
    p = d.get("place") or {}
    print(f"{t!r} -> {d['status']} | {p.get('name')} | {p.get('address')} | {(d.get('route') or {}).get('summary')} | {d['reply'][:90]}")
