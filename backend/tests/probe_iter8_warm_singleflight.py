"""Probe: agent background warm + immediate /api/parking for same place -> one vision log (run after backend restart)."""
import os
import subprocess
import time

import requests

BASE = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/")
LOG = "/var/log/supervisor/backend.err.log"
start = int(subprocess.run(["wc", "-l", LOG], capture_output=True, text=True).stdout.split()[0])

for text in ["I need fuel", "find me a hotel"]:
    t = time.time()
    d = requests.post(f"{BASE}/api/agent", json={"text": text, "lat": 38.5816, "lng": -121.4944}, timeout=120).json()
    ta = time.time() - t
    place = d["place"]
    t = time.time()
    p = requests.post(f"{BASE}/api/parking", json={"place": place}, timeout=120)
    tp = time.time() - t
    print(f"{text!r}: agent {ta:.1f}s place={place['name']} cached_parking_in_agent={bool(d.get('parking'))} "
          f"parking {p.status_code} {tp:.1f}s verdict={p.json().get('verdict')} entry={p.json().get('entry_tip')!r} park={p.json().get('park_tip')!r}")

time.sleep(25)  # let other background warms finish
lines = subprocess.run(["tail", "-n", "+%d" % (start + 1), LOG], capture_output=True, text=True).stdout.splitlines()
names = [ln.split("parking ", 1)[1].split(" ->")[0] for ln in lines if "truckbuddy.parking - INFO - parking " in ln]
from collections import Counter
print("vision checks per place:", Counter(names))
