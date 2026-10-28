#!/usr/bin/env python3
"""Latency and quality stats from the backend's `turn ...` log lines.

    railway logs > turns.log   (or docker logs, or journalctl)
    python scripts/turn_stats.py turns.log
    python scripts/turn_stats.py < turns.log

Prints p50/p95 per stage (stt, llm_round_1, tools, tts, first_audio, total), turns by status and
intent, LLM timeouts/errors, cached-token ratio, and the re-ask rate (a turn followed within 30 s by
another turn with the same intent, a "no / not that / wrong", or a not_understood). Timestamps are
taken from the start of each line when present (uvicorn/railway formats); without them re-ask uses
line adjacency.
"""
import re
import sys
from collections import Counter
from datetime import datetime
from typing import Iterable, Optional

TURN = re.compile(r"\bturn ((?:\w+=\S+ ?)+)$")
STAMP = re.compile(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)")
STAGES = ("stt", "llm_round_1", "tools", "tts", "first_audio", "total")
REASK_RE = re.compile(r"\b(no|nope|not that|wrong|that's not|i said)\b", re.I)
REASK_WINDOW_S = 30


def parse_line(line: str) -> Optional[dict]:
    m = TURN.search(line.strip())
    if not m:
        return None
    out: dict = {}
    for pair in m.group(1).split():
        k, _, v = pair.partition("=")
        if v.lstrip("-").isdigit():
            out[k] = int(v)
        else:
            out[k] = v
    s = STAMP.search(line)
    if s:
        try:
            out["_t"] = datetime.fromisoformat(s.group(1).replace(",", "."))
        except ValueError:
            pass
    return out


def parse(lines: Iterable[str]) -> list[dict]:
    return [t for t in (parse_line(ln) for ln in lines) if t]


def pct(values: list[int], p: float) -> Optional[int]:
    if not values:
        return None
    vs = sorted(values)
    k = max(0, min(len(vs) - 1, round(p * (len(vs) - 1))))
    return vs[k]


def reask_rate(turns: list[dict]) -> Optional[float]:
    """Share of turns followed (within 30 s, same session when known) by a re-ask."""
    if len(turns) < 2:
        return None
    flagged = 0
    for a, b in zip(turns, turns[1:]):
        if "_t" in a and "_t" in b and (b["_t"] - a["_t"]).total_seconds() > REASK_WINDOW_S:
            continue
        same_intent = a.get("intent") not in (None, "-", "none") and a.get("intent") == b.get("intent") \
            and a.get("status") not in ("chat", "confirm")
        if same_intent or b.get("status") == "not_understood" or REASK_RE.search(str(b.get("text", ""))):
            flagged += 1
    return flagged / (len(turns) - 1)


def summarize(turns: list[dict]) -> dict:
    stages = {s: [t[s] for t in turns if isinstance(t.get(s), int)] for s in STAGES}
    prompt = sum(t.get("n_prompt_tokens", 0) for t in turns)
    cached = sum(t.get("n_cached_tokens", 0) for t in turns)
    return {
        "turns": len(turns),
        "stages": {s: {"n": len(v), "p50": pct(v, 0.5), "p95": pct(v, 0.95)} for s, v in stages.items() if v},
        "status": Counter(t.get("status", "?") for t in turns),
        "intent": Counter(t.get("intent", "-") for t in turns),
        "errors": Counter(t["error"] for t in turns if t.get("error")),
        "cached_ratio": (cached / prompt) if prompt else None,
        "reask_rate": reask_rate(turns),
    }


def render(s: dict) -> str:
    lines = [f"turns: {s['turns']}"]
    if s["stages"]:
        lines.append("stage            n    p50    p95")
        for name, v in s["stages"].items():
            lines.append(f"{name:<14} {v['n']:>4} {v['p50']:>6} {v['p95']:>6}")
    for label in ("status", "intent", "errors"):
        c = s[label]
        if c:
            lines.append(f"{label}: " + ", ".join(f"{k}={n}" for k, n in c.most_common()))
    if s["cached_ratio"] is not None:
        lines.append(f"cached tokens: {s['cached_ratio']:.0%} of prompt tokens")
    if s["reask_rate"] is not None:
        lines.append(f"re-ask rate: {s['reask_rate']:.1%}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    src = open(argv[1], encoding="utf-8", errors="replace") if len(argv) > 1 else sys.stdin
    with src:
        turns = parse(src)
    if not turns:
        print("no `turn ...` lines found", file=sys.stderr)
        return 1
    print(render(summarize(turns)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
