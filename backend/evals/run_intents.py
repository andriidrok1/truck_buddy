"""Run the intent dataset against the real classifier and write a report.

    python -m evals.run_intents --model gpt-5.4-mini --concurrency 8 [--tags a,b] [--preset x] [--repeat N]
                                [--compare] [--fail-below 1.0] [--promote] [--limit N] [--adapter chat_json]

Writes evals/results/<YYYY-MM-DD>-<model>.json and .md and prints the markdown. `--compare` grades against
evals/baseline.json; `--promote` copies this run there. Exit 1 when the gate fails (see --fail-below).
Needs OPENAI_API_KEY (backend/.env is loaded when present); each run costs roughly $0.5 at ~250 cases.
"""
import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from datetime import date
from pathlib import Path
from typing import Optional

import evals
from evals import adapters
from evals.dataset import load_cases
from evals.presets import PRESETS, build_context
from evals.score import Report, compare, score, to_markdown
from services import timing

RESULTS_DIR: Path = evals.RESULTS_DIR
BASELINE_FILE: Path = evals.BASELINE_FILE


def select(cases: list[dict], tags: Optional[str] = None, preset: Optional[str] = None,
           limit: Optional[int] = None) -> list[dict]:
    if tags:
        want = {t.strip() for t in tags.split(",") if t.strip()}
        cases = [c for c in cases if want & set(c.get("tags", []))]
    if preset:
        cases = [c for c in cases if c["context_preset"] == preset]
    if limit is not None:
        cases = cases[:limit]
    return cases


async def run_cases(cases: list[dict], adapter: adapters.Adapter, concurrency: int = 8,
                    repeat: int = 1) -> list[dict]:
    """One classifier call per case per repeat, `concurrency` at a time; timings read from the request
    context (timing.begin() runs inside each task, so tasks never share counters)."""
    contexts = {name: build_context(name) for name in {c["context_preset"] for c in cases}}
    sem = asyncio.Semaphore(max(1, concurrency))

    async def one(case: dict) -> dict:
        async with sem:
            timing.begin()
            _req, context, truck = contexts[case["context_preset"]]
            t0 = time.perf_counter()
            error = None
            try:
                got = await adapter(case["text"], context, truck)
            except Exception as e:  # noqa: BLE001 - one bad call must not sink the run
                got, error = {}, f"{type(e).__name__}: {e}"
            elapsed = (time.perf_counter() - t0) * 1000
            t = timing.current()
            return {"id": case["id"], "got": got if isinstance(got, dict) else {},
                    "ms": t.get("llm_round_1", round(elapsed)),
                    "prompt_tokens": t.get("n_prompt_tokens", 0), "cached_tokens": t.get("n_cached_tokens", 0),
                    "completion_tokens": t.get("n_completion_tokens", 0), "error": error}

    return list(await asyncio.gather(*(one(c) for _ in range(max(1, repeat)) for c in cases)))


def gate(report: Report, comparison: Optional[dict], fail_below: Optional[float]) -> tuple[bool, str]:
    """(passed, why). With a baseline the threshold is baseline intent accuracy × fail_below, and any
    regression fails; without a baseline fail_below is an absolute accuracy, or no gate under --compare."""
    if fail_below is None or report.intent_acc is None:
        return True, "no gate"
    if comparison is not None:
        if not comparison.get("has_baseline"):
            return True, "no baseline yet: gate skipped"
        threshold = (comparison["baseline_intent_acc"] or 0.0) * fail_below
        if report.intent_acc < threshold:
            return False, f"intent accuracy {report.intent_acc:.4f} < {threshold:.4f} (baseline × {fail_below})"
        if comparison["regressions"]:
            return False, f"{len(comparison['regressions'])} regression(s): {', '.join(comparison['regressions'])}"
        return True, f"intent accuracy {report.intent_acc:.4f} >= {threshold:.4f}, no regressions"
    if report.intent_acc < fail_below:
        return False, f"intent accuracy {report.intent_acc:.4f} < {fail_below}"
    return True, f"intent accuracy {report.intent_acc:.4f} >= {fail_below}"


def write_report(report: Report, comparison: Optional[dict], results_dir: Path) -> tuple[Path, Path]:
    results_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{report.date}-{report.model}"
    json_path, md_path = results_dir / f"{stem}.json", results_dir / f"{stem}.md"
    data = report.to_dict()
    if comparison is not None:
        data["comparison"] = comparison
    json_path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    md_path.write_text(to_markdown(report, comparison))
    return json_path, md_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="gpt-5.4-mini")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--tags", help="comma-separated; a case runs when it has any of them")
    ap.add_argument("--preset", choices=sorted(PRESETS))
    ap.add_argument("--repeat", type=int, default=1, help="run every case N times (flaky ids in the report)")
    ap.add_argument("--compare", action="store_true", help="grade against evals/baseline.json")
    ap.add_argument("--fail-below", type=float, default=None,
                    help="with --compare: exit 1 below baseline × this (1.0 = no drop); else an absolute accuracy")
    ap.add_argument("--promote", action="store_true", help="copy this report to evals/baseline.json")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--adapter", choices=sorted(adapters.ADAPTERS), default="chat_json")
    args = ap.parse_args(argv)

    if not os.environ.get("OPENAI_API_KEY"):  # local runs: the key from backend/.env (never under pytest)
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set", file=sys.stderr)
        return 2

    cases = select(load_cases(), args.tags, args.preset, args.limit)
    if not cases:
        print("no cases selected", file=sys.stderr)
        return 2
    from services import agent
    agent.MODEL = ("openai", args.model)
    adapter = adapters.ADAPTERS[args.adapter]
    t0 = time.perf_counter()
    results = asyncio.run(run_cases(cases, adapter, args.concurrency, args.repeat))
    wall = time.perf_counter() - t0
    report = score(cases, results, model=args.model, date=date.today().isoformat(), adapter=args.adapter)

    comparison = None
    if args.compare:
        baseline = json.loads(BASELINE_FILE.read_text()) if BASELINE_FILE.exists() else None
        comparison = compare(report, baseline)
    json_path, md_path = write_report(report, comparison, RESULTS_DIR)
    md = md_path.read_text()
    sys.stdout.write(md)
    print(f"wall {wall:.1f}s, report: {json_path} and {md_path}")
    if args.promote:
        shutil.copyfile(json_path, BASELINE_FILE)
        print(f"promoted to {BASELINE_FILE}")
    passed, why = gate(report, comparison, args.fail_below)
    print(("gate passed: " if passed else "gate FAILED: ") + why)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
