"""Satellite parking check eval: hand-labelled places (evals/parking/places.json), old vs grounded check.

    python evals/run_parking.py --arms old,new        # live, costs money (~$0.03 old, ~$0.07 new per place)
    python evals/run_parking.py --score results/parking-<stamp>.json

Metrics: verdict accuracy and confident mistakes (likely<->unlikely) on places labelled likely/unlikely;
entry hit = within ENTRY_HIT_M of a real truck driveway, "wrong" = a point placed anywhere else (the route then
ends there); park within PARK_HIT_M of a labelled truck area; cost from token usage.
"""
import argparse
import asyncio
import base64
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from models import Place  # noqa: E402
from services import llm, osm_access, parking, parking_ground  # noqa: E402

HERE = Path(__file__).resolve().parent
ENTRY_HIT_M = 40.0
ENTRY_HIT_M_LOOSE = 60.0  # labels are +-15 m and OSM junctions sit on the road centre line
PARK_HIT_M = 60.0
PRICE_IN, PRICE_OUT = 5 / 1e6, 30 / 1e6  # gpt-5.5 $/token
CONCURRENCY = 4


def _slow_client() -> None:
    """The app's client gives up after 30 s; here latency is measured, not cut."""
    import os
    from openai import AsyncOpenAI
    llm._client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=180.0, max_retries=1)


def _m(a, b) -> float:
    return osm_access._m(a[0], a[1], b[0], b[1])


def _place(p: dict) -> Place:
    return Place(id=p["id"], name=p["name"], category=p["category"] if p["category"] in (
        "truck_stop", "rest_area", "hotel", "cat_scale") else "truck_stop", lat=p["lat"], lng=p["lng"],
        tags=p.get("tags", []))


async def run_old(p: dict) -> dict:
    place = _place(p)
    img = await parking.fetch_satellite(place.lat, place.lng)
    res = await llm.complete_full(parking.SYSTEM, "Judge ONLY from what is visible in this image. Can a 53 ft semi "
                                  "park at the center point? Where should it enter and where should it park?",
                                  "gpt-5.5", image_b64=base64.b64encode(img).decode())
    v = parking._parse(res.text, place)
    return {"fits": v["fits"], "reason": v["reason"], "entry_tip": v["entry_tip"], "park_tip": v["park_tip"],
            "spots": [s.model_dump() for s in v["spots"]], "usage": res.usage}


async def run_new(p: dict) -> dict:
    place = _place(p)
    f = HERE / "parking" / "osm" / f"{p['id']}.json"
    # No cached OSM (Overpass was down) = no markers, the grid fallback; as in production when OSM fails.
    access = osm_access.points(json.loads(f.read_text()), place.lat, place.lng) if f.exists() else []
    img = await parking_ground.fetch_image(place.lat, place.lng)
    v = await parking_ground.check(place, access, img)
    v["spots"] = [s.model_dump() for s in v["spots"]]
    v["n_access"] = len(access)
    return v


ARMS = {"old": run_old, "new": run_new}


async def run(arms: list[str], only: set[str]) -> Path:
    places = [p for p in json.loads((HERE / "parking" / "places.json").read_text()) if not only or p["id"] in only]
    _slow_client()
    sem = asyncio.Semaphore(CONCURRENCY)
    out = {a: {} for a in arms}

    async def one(arm: str, p: dict):
        async with sem:
            t = time.monotonic()
            try:
                r = await ARMS[arm](p)
            except Exception as e:  # noqa: BLE001
                r = {"error": repr(e)}
            r["seconds"] = round(time.monotonic() - t, 1)
            out[arm][p["id"]] = r
            print(f"{arm:4} {p['id']:28} {r.get('fits', 'ERR'):9} {r.get('entry_tip')!r}", flush=True)

    await asyncio.gather(*(one(a, p) for a in arms for p in places))
    path = HERE / "results" / f"parking-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(out, indent=1))
    return path


def remerge(path: Path, rerun: Path = None) -> Path:
    """Re-apply the current merge/build to saved samples (free), optionally taking some places from a rerun."""
    res = json.loads(path.read_text())
    extra = json.loads(rerun.read_text())["new"] if rerun else {}
    places = {p["id"]: p for p in json.loads((HERE / "parking" / "places.json").read_text())}
    rows = {}
    for pid, r in res["new"].items():
        r = extra.get(pid, r)
        if "samples" in r:
            place = _place(places[pid])
            f = HERE / "parking" / "osm" / f"{pid}.json"
            access = osm_access.points(json.loads(f.read_text()), place.lat, place.lng) if f.exists() else []
            b = parking_ground.build(place, access, r["samples"])
            r = {**r, **b, "spots": [s.model_dump() for s in b["spots"]]}
        rows[pid] = r
    out = path.with_name(path.stem + "-remerged.json")
    out.write_text(json.dumps({"new": rows}, indent=1))
    return out


def score(path: Path) -> None:
    places = {p["id"]: p for p in json.loads((HERE / "parking" / "places.json").read_text())}
    res = json.loads(path.read_text())
    for arm, rows in res.items():
        verdict = confident_wrong = n_v = 0
        hit = wrong = none = n_e = 0
        p_hit = n_p = 0
        dists, cost, secs, errors = [], 0.0, [], 0
        for pid, r in rows.items():
            if "error" in r:
                errors += 1
                continue
            t = places[pid]["truth"]
            u = r.get("usage") or {}
            cost += u.get("prompt_tokens", 0) * PRICE_IN + u.get("completion_tokens", 0) * PRICE_OUT
            secs.append(r["seconds"])
            if t["fits"] in ("likely", "unlikely"):
                n_v += 1
                verdict += r["fits"] == t["fits"]
                confident_wrong += {r["fits"], t["fits"]} == {"likely", "unlikely"}
            e = next(((s["lat"], s["lng"]) for s in r["spots"] if s["kind"] == "entry"), None)
            if t["entries"] and t["fits"] == "likely":
                n_e += 1
                if e is None:
                    none += 1
                else:
                    d = min(_m(e, x) for x in t["entries"])
                    dists.append(d)
                    hit += d <= ENTRY_HIT_M
                    wrong += d > ENTRY_HIT_M_LOOSE
            pk = next(((s["lat"], s["lng"]) for s in r["spots"] if s["kind"] == "park"), None)
            if t["park"] and t["fits"] == "likely":
                n_p += 1
                p_hit += pk is not None and min(_m(pk, x) for x in t["park"]) <= PARK_HIT_M
        dists.sort()
        med = dists[len(dists) // 2] if dists else float("nan")
        print(f"\n== {arm}  ({len(rows)} places, {errors} errors)")
        print(f"verdict        {verdict}/{n_v} right, {confident_wrong} confidently wrong (likely<->unlikely)")
        print(f"entry          {hit}/{n_e} within {ENTRY_HIT_M:.0f} m, {wrong} wrong (> {ENTRY_HIT_M_LOOSE:.0f} m), "
              f"{none} no point; "
              f"median miss {med:.0f} m")
        print(f"park           {p_hit}/{n_p} within {PARK_HIT_M:.0f} m of the truck area")
        print(f"cost           ${cost:.2f} total, ${cost / max(1, len(rows) - errors):.3f} per place; "
              f"median {sorted(secs)[len(secs) // 2] if secs else 0:.0f} s")
        a, b = rows.get("ra-elkhorn-s", {}), rows.get("ra-elkhorn-n", {})
        print(f"same place     Elkhorn S/N: {a.get('fits')} / {b.get('fits')}; {a.get('entry_tip')!r} / "
              f"{b.get('entry_tip')!r}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="old,new")
    ap.add_argument("--only", default="")
    ap.add_argument("--score")
    ap.add_argument("--remerge", nargs="+", help="saved run [rerun of some places]")
    a = ap.parse_args()
    if a.remerge:
        out = remerge(Path(a.remerge[0]), Path(a.remerge[1]) if len(a.remerge) > 1 else None)
        print(out)
        score(out)
    elif a.score:
        score(Path(a.score))
    else:
        p = asyncio.run(run(a.arms.split(","), set(filter(None, a.only.split(",")))))
        print(p)
        score(p)
