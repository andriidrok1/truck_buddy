#!/usr/bin/env python3
"""Trip log summary: trips per day, reroutes by cause, the far-stop and destination questions and their answers,
failed turns by status with the first texts.

    python scripts/trip_stats.py                      # backend/data/memory.sqlite (or MEMORY_SQLITE_PATH)
    python scripts/trip_stats.py --db path/to.sqlite --days 14
    python scripts/trip_stats.py --json               # machine-readable

Postgres: set DATABASE_URL and run with --pg (asyncpg must be installed, as in the backend venv).
"""
import argparse
import asyncio
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _rows_sqlite(path: str, since: str) -> list[tuple]:
    con = sqlite3.connect(path)
    try:
        return con.execute("SELECT driver, trip_id, kind, at, source, data FROM trip_events WHERE at >= ? ORDER BY at",
                           (since,)).fetchall()
    finally:
        con.close()


async def _rows_pg(since: datetime) -> list[tuple]:
    import asyncpg
    con = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        got = await con.fetch("SELECT driver, trip_id, kind, at, source, data FROM trip_events WHERE at >= $1 ORDER BY at", since)
        return [(r["driver"], r["trip_id"], r["kind"], r["at"].isoformat(), r["source"], r["data"]) for r in got]
    finally:
        await con.close()


def summarize(rows: list[tuple]) -> dict:
    kinds = Counter(k for _, _, k, *_ in rows)
    trips_by_day: dict[str, set] = defaultdict(set)
    reroute_causes = Counter()
    reroute_fallback = Counter()
    questions: dict[str, Counter] = defaultdict(Counter)
    failed = Counter()
    texts: list[str] = []
    for driver, trip_id, kind, at, source, data in rows:
        d = json.loads(data) if isinstance(data, str) else (data or {})
        if kind == "route_started" and trip_id:
            trips_by_day[str(at)[:10]].add(trip_id)
        elif kind == "reroute":
            reroute_causes[str(d.get("cause") or "?")] += 1
            if d.get("fallback"):
                reroute_fallback["fallback"] += 1
            if d.get("ok") is False:
                reroute_fallback["failed"] += 1
        elif kind in ("stop_guard", "dest_check", "stt_confirm"):
            questions[kind][str(d.get("result") or "?")] += 1
        elif kind in ("not_understood", "turn_error", "turn_failed"):
            failed[f"{kind}:{d.get('status') or d.get('error') or ''}".rstrip(":")] += 1
            if d.get("text") and len(texts) < 20:
                texts.append(str(d["text"]))
    return {
        "events": sum(kinds.values()), "kinds": dict(kinds.most_common()),
        "trips_per_day": {day: len(t) for day, t in sorted(trips_by_day.items())},
        "reroutes": {"by_cause": dict(reroute_causes), **dict(reroute_fallback)},
        "questions": {k: dict(v) for k, v in questions.items()},
        "failed_turns": dict(failed.most_common()), "failed_texts": texts,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.environ.get("MEMORY_SQLITE_PATH") or str(Path(__file__).resolve().parents[1] / "data" / "memory.sqlite"))
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--pg", action="store_true", help="read DATABASE_URL instead of the sqlite file")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    rows = asyncio.run(_rows_pg(since)) if args.pg else _rows_sqlite(args.db, since.isoformat())
    s = summarize(rows)
    if args.json:
        print(json.dumps(s, indent=2, ensure_ascii=False))
        return 0
    print(f"{s['events']} events in the last {args.days} days")
    print("trips per day:", ", ".join(f"{d} {n}" for d, n in s["trips_per_day"].items()) or "none")
    print("reroutes:", s["reroutes"])
    for k, v in s["questions"].items():
        print(f"{k}:", v)
    print("failed turns:", s["failed_turns"])
    for t in s["failed_texts"]:
        print("   ", t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
