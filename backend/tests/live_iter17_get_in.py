"""Live: 'I need fuel' -> 'how do I get in?' -> /parking/answer (waits for the check)."""
import os
import sys
import time

import requests

API = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/") + "/api"
sid = f"live-{int(time.time())}"
FIRST = sys.argv[1] if len(sys.argv) > 1 else "I need fuel"
loc = {"lat": 38.5816, "lng": -121.4944}
t0 = time.time()
r = requests.post(f"{API}/agent", json={"text": FIRST, **loc, "session_id": sid}, timeout=90).json()
print(f"[{time.time()-t0:5.1f}s] {FIRST} -> {r['status']} {r['place']['name']} | {r['reply']}")
place = r["place"]
print("approach:", r["route"].get("approach"))
if len(sys.argv) > 2:
    time.sleep(float(sys.argv[2]))  # let the background satellite check warm the cache
t1 = time.time()
r2 = requests.post(f"{API}/agent", json={"text": "how do I get in?", **loc, "session_id": sid, "active_place": place}, timeout=90).json()
print(f"[+{time.time()-t1:5.2f}s] how do I get in -> {r2['reply']} actions={r2['actions']}")
if r2["actions"]:
    a = requests.post(f"{API}/parking/answer", json={"place": place, "wait_s": 15}, timeout=60).json()
    print(f"[+{time.time()-t1:5.2f}s] answer ({a['status']}) -> {a['reply']}")
    if a["status"] == "pending":
        a = requests.post(f"{API}/parking/answer", json={"place": place, "wait_s": 45}, timeout=90).json()
        print(f"[{time.time()-t0:5.1f}s] late answer ({a['status']}) -> {a['reply']}")
    pk = a.get("parking") or {}
    print("relative:", pk.get("relative"), "spots:", [(s["kind"], s["lat"], s["lng"]) for s in pk.get("spots", [])])
r3 = requests.post(f"{API}/route", json={**loc, "place": place}, timeout=60).json()
end = r3["route"]["geometry"][-1]
print("reroute ends at", end, "place", (place["lat"], place["lng"]))
sys.exit(0)
