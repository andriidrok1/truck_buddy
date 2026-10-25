# Probe (1 live research run): retry "call the cheapest one" after a tire breakdown search (iter15 flaky check).
# Run: EXPO_PUBLIC_BACKEND_URL=... python tests/probe_iter15_call_cheapest.py
import os
import uuid

import requests

BASE = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
SID = f"t1p-{uuid.uuid4().hex[:6]}"


def agent(text):
    r = requests.post(f"{BASE}/api/agent", json={"text": text, "lat": 38.5816, "lng": -121.4944, "session_id": SID},
                      timeout=60)
    r.raise_for_status()
    return r.json()


agent("I blew a tire")
a = agent("drive tire, can't roll, come out to me")
res = requests.get(f"{BASE}/api/breakdown/{a['research_id']}", timeout=60).json()
for i, p in enumerate(res["providers"], 1):
    print(i, p["name"], p.get("phone"), [(x["what"], x["amount_usd_low"]) for x in p["facts"]["prices"]])
for t in ("call the cheapest one", "call the closest one"):
    r = agent(t)
    print(f"{t!r} -> status={r['status']} dial={r.get('dial_phone')} reply={r['reply']!r}")
