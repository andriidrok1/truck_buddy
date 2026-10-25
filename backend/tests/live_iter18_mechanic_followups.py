# Live probe (1 research run): mechanic question -> shop_ok research -> follow-ups route/more/reviews/comchek.
import json
import os
import time
import uuid

import requests

BASE = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
SID = f"mech-{uuid.uuid4().hex[:6]}"


def agent(text):
    t = time.time()
    r = requests.post(f"{BASE}/api/agent", json={"text": text, "lat": 38.5816, "lng": -121.4944, "session_id": SID}, timeout=90)
    d = r.json()
    print(f"[{r.status_code} {time.time()-t:.1f}s] {text!r} -> {d.get('status')} | {d.get('reply')} | dial={d.get('dial_phone')} "
          f"place={(d.get('place') or {}).get('name')} kind={d.get('breakdown_kind')} mode={d.get('breakdown_mode')}")
    return d


q = agent("check engine light came on and I'm losing power")
a = agent("yeah it still drives, I can roll to a shop") if q["status"] == "chat" else q
if a.get("research_id"):
    t = time.time()
    r = requests.get(f"{BASE}/api/breakdown/{a['research_id']}", timeout=90).json()
    print(f"result {time.time()-t:.1f}s status={r['status']} n={len(r.get('providers') or [])} stats={r.get('research_stats')}")
    for p in r.get("providers") or []:
        print("  -", p["name"], p["distance_mi"], "mobile", p["facts"]["mobile_service"], "shop_only", p["shop_only"],
              "pay", p["facts"]["payment"], "prices", [(x["amount_usd_low"], x["quote"][:60]) for x in p["facts"]["prices"]])
    print("SPOKEN:", r["reply"])
    for t in ["route me to the mobile one", "anyone else", "read me reviews for the first one", "which one takes Comchek",
              "call the closest one"]:
        agent(t)
