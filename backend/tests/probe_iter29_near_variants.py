# T1 probe iter29: find-near phrasing variants (park/staging near a named point)
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import requests

BASE_URL = [l.split("=", 1)[1].strip().strip('"') for l in open("/app/frontend/.env")
            if l.startswith("EXPO_PUBLIC_BACKEND_URL") or l.startswith("EXPO_BACKEND_URL")][0].rstrip("/")
_tok = requests.post(f"{BASE_URL}/api/auth/access-code", json={"code": os.environ["ACCESS_CODE"]}, timeout=30).json()["access_token"]
H = {"Authorization": f"Bearer {_tok}"}
SAC = {"lat": 38.58, "lng": -121.49}
PHRASES = __import__("sys").argv[1:] or [
    "where can I park near my delivery in Lodi",
    "I need parking close to my drop in Modesto",
    "park near Stockton",
    "where do I park close to my delivery in Stockton",
    "find me a rest area near Stockton",
    "find a truck stop near 2828 El Centro Rd, Sacramento",
]


def one(t):
    r = requests.post(f"{BASE_URL}/api/agent", headers=H, json={"text": t, "session_id": f"T1v-{uuid.uuid4().hex[:6]}",
                                                                 **SAC}, timeout=90).json()
    p = r.get("place") or {}
    return f"{t!r} -> {r['status']} cat={p.get('category')} lat={p.get('lat')} reply={r['reply']!r}"


with ThreadPoolExecutor(3) as ex:
    for line in ex.map(one, PHRASES):
        print(line)
