# T1 probe iter29: after a find-near dialog, does a plain "I need fuel" (category change) still search near the old point?
import os
import uuid

import requests

BASE_URL = [l.split("=", 1)[1].strip().strip('"') for l in open("/app/frontend/.env")
            if l.startswith("EXPO_PUBLIC_BACKEND_URL") or l.startswith("EXPO_BACKEND_URL")][0].rstrip("/")
_tok = requests.post(f"{BASE_URL}/api/auth/access-code", json={"code": os.environ["ACCESS_CODE"]}, timeout=30).json()["access_token"]
H = {"Authorization": f"Bearer {_tok}"}
SAC = {"lat": 38.58, "lng": -121.49}
sid = f"T1-leak-{uuid.uuid4().hex[:6]}"
active = None
for t in ["find a hotel near Stockton", "I need fuel"]:
    body = {"text": t, "session_id": sid, **SAC}
    if active:
        body["active_place"] = active
    r = requests.post(f"{BASE_URL}/api/agent", headers=H, json=body, timeout=90).json()
    active = r.get("place") or active
    p = r.get("place") or {}
    print(f"{t!r} -> {r['status']} {p.get('category')} lat={p.get('lat')} {r['reply']!r}")
