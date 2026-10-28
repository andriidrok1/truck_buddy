"""Grading and reporting, pure functions: no I/O, no model.

A result is {"id", "got": dict, "ms": float|None, "prompt_tokens", "cached_tokens", "completion_tokens",
"error": str|None}. Several results may share an id (--repeat); a case is flaky when its runs disagree.
"""
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Optional

# USD per 1M tokens. PLACEHOLDER rates for gpt-5.4-mini: verify against the OpenAI pricing page before
# trusting cost_usd. Cached input tokens are billed at CACHED_INPUT_FACTOR of the input rate.
PRICES: dict[str, dict[str, float]] = {
    "gpt-5.4-mini": {"input": 0.40, "output": 1.60},
    "gpt-5.5": {"input": 2.50, "output": 10.00},
}
CACHED_INPUT_FACTOR = 0.1
MAX_FAILURES_IN_MARKDOWN = 40


@dataclass
class Report:
    model: str
    date: str
    adapter: str = "chat_json"
    n_cases: int = 0
    n_results: int = 0
    intent_acc: Optional[float] = None
    case_acc: Optional[float] = None
    by_tag: dict = field(default_factory=dict)
    by_preset: dict = field(default_factory=dict)
    by_intent: dict = field(default_factory=dict)
    confusions: dict = field(default_factory=dict)  # "find→chat": 3
    p50_ms: Optional[float] = None
    p95_ms: Optional[float] = None
    cached_ratio: Optional[float] = None
    cost_usd: float = 0.0
    failures: list = field(default_factory=list)
    flaky: list = field(default_factory=list)
    per_case: dict = field(default_factory=dict)  # id -> {"ok_intent", "ok_all"} (all runs must pass)
    errors: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# ---- matching ---------------------------------------------------------------------------------

def _norm(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _match_string(name: str, want, got, mismatches: list[str]) -> None:
    g = _norm(got)
    if isinstance(want, dict):
        sub = want["contains"]
        if g is None or sub not in g.lower():
            mismatches.append(f"{name}: expected to contain {sub!r}, got {g!r}")
    elif want is None:
        if g is not None:
            mismatches.append(f"{name}: expected null, got {g!r}")
    elif g is None or g.lower() != str(want).lower():
        mismatches.append(f"{name}: expected {want!r}, got {g!r}")


def match_case(expected: dict, got: dict) -> tuple[bool, bool, list[str]]:
    """(intent matched, every expected field matched, what differed). Field rules are in dataset.py."""
    got = got if isinstance(got, dict) else {}
    mismatches: list[str] = []
    ok_intent = _norm(got.get("intent")) == expected.get("intent")
    if not ok_intent:
        mismatches.append(f"intent: expected {expected.get('intent')!r}, got {_norm(got.get('intent'))!r}")
    if "category" in expected:
        want, g = expected["category"], _norm(got.get("category"))
        if (want is None and g is not None) or (want is not None and g != want):
            mismatches.append(f"category: expected {want!r}, got {g!r}")
    for name in ("query", "near", "when", "kind"):
        if name in expected:
            _match_string(name, expected[name], got.get(name), mismatches)
    if "place_index" in expected:
        try:
            g = int(got.get("place_index"))
        except (TypeError, ValueError):
            g = None
        if g not in expected["place_index"]:
            mismatches.append(f"place_index: expected one of {expected['place_index']}, got {g!r}")
    if "reply" in expected:
        _match_string("reply", expected["reply"], got.get("reply"), mismatches)
    if "actions" in expected:
        acts = got.get("actions") if isinstance(got.get("actions"), list) else []
        types = {str(a.get("type")) for a in acts if isinstance(a, dict)}
        missing = [a for a in expected["actions"] if a not in types]
        if missing:
            mismatches.append(f"actions: missing {missing}, got {sorted(types)}")
    return ok_intent, not mismatches, mismatches


# ---- scoring ----------------------------------------------------------------------------------

def _percentile(values: list[float], p: float) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, round((len(s) - 1) * p)))
    return round(s[k], 1)


def _acc(rows: list[tuple[bool, bool]]) -> dict:
    n = len(rows)
    return {"n": n,
            "intent_acc": round(sum(a for a, _ in rows) / n, 4) if n else None,
            "case_acc": round(sum(b for _, b in rows) / n, 4) if n else None}


def cost_of(model: str, prompt_tokens: int, cached_tokens: int, completion_tokens: int) -> float:
    rates = PRICES.get(model) or PRICES["gpt-5.4-mini"]
    cached = min(cached_tokens, prompt_tokens)
    return ((prompt_tokens - cached) * rates["input"] + cached * rates["input"] * CACHED_INPUT_FACTOR
            + completion_tokens * rates["output"]) / 1e6


def score(cases: list[dict], results: list[dict], model: str = "gpt-5.4-mini", date: str = "",
          adapter: str = "chat_json") -> Report:
    by_id = {c["id"]: c for c in cases}
    rep = Report(model=model, date=date, adapter=adapter, n_cases=len(cases), n_results=len(results))
    rows: list[tuple[bool, bool]] = []
    tag_rows: dict[str, list] = defaultdict(list)
    preset_rows: dict[str, list] = defaultdict(list)
    intent_rows: dict[str, list] = defaultdict(list)
    confusions: Counter = Counter()
    runs: dict[str, list[tuple[bool, bool]]] = defaultdict(list)
    times: list[float] = []
    prompt = cached = completion = 0
    for r in results:
        c = by_id.get(r["id"])
        if c is None:
            continue
        got = r.get("got") or {}
        ok_intent, ok_all, mism = match_case(c["expected"], got)
        if r.get("error"):
            ok_intent = ok_all = False
            mism = [f"error: {r['error']}"] + mism
            rep.errors += 1
        row = (ok_intent, ok_all)
        rows.append(row)
        runs[c["id"]].append(row)
        for t in c.get("tags", []):
            tag_rows[t].append(row)
        preset_rows[c["context_preset"]].append(row)
        want = c["expected"]["intent"]
        intent_rows[want].append(row)
        if not ok_intent:
            confusions[f"{want}→{_norm(got.get('intent'))}"] += 1
        if r.get("ms") is not None:
            times.append(float(r["ms"]))
        prompt += int(r.get("prompt_tokens") or 0)
        cached += int(r.get("cached_tokens") or 0)
        completion += int(r.get("completion_tokens") or 0)
        if not ok_all:
            rep.failures.append({"id": c["id"], "text": c["text"], "preset": c["context_preset"],
                                 "expected": c["expected"], "got": got, "mismatches": mism})
    n = len(rows)
    rep.intent_acc = round(sum(a for a, _ in rows) / n, 4) if n else None
    rep.case_acc = round(sum(b for _, b in rows) / n, 4) if n else None
    rep.by_tag = {t: _acc(v) for t, v in sorted(tag_rows.items())}
    rep.by_preset = {p: _acc(v) for p, v in sorted(preset_rows.items())}
    rep.by_intent = {}
    for intent, v in sorted(intent_rows.items()):
        d = _acc(v)
        d["confusions"] = {k.split("→", 1)[1]: cnt for k, cnt in confusions.items() if k.startswith(intent + "→")}
        rep.by_intent[intent] = d
    rep.confusions = dict(confusions.most_common())
    rep.p50_ms = _percentile(times, 0.5)
    rep.p95_ms = _percentile(times, 0.95)
    rep.cached_ratio = round(min(cached, prompt) / prompt, 4) if prompt else None
    rep.cost_usd = round(cost_of(model, prompt, cached, completion), 4)
    rep.flaky = sorted(cid for cid, rs in runs.items() if len({r[1] for r in rs}) > 1)
    rep.per_case = {cid: {"ok_intent": all(a for a, _ in rs), "ok_all": all(b for _, b in rs)}
                    for cid, rs in runs.items()}
    return rep


# ---- comparison -------------------------------------------------------------------------------

def compare(report: Report, baseline: Optional[dict]) -> dict:
    """Deltas against a baseline report dict; regressions = cases that passed there and fail now."""
    if not baseline or baseline.get("intent_acc") is None:
        return {"has_baseline": False, "deltas": {}, "regressions": [], "fixes": [],
                "note": "no baseline yet (promote a run with --promote)"}
    deltas = {}
    for k in ("intent_acc", "case_acc", "p50_ms", "p95_ms", "cached_ratio", "cost_usd"):
        a, b = getattr(report, k), baseline.get(k)
        deltas[k] = round(a - b, 4) if a is not None and b is not None else None
    base_cases = baseline.get("per_case") or {}
    regressions = sorted(cid for cid, v in report.per_case.items()
                         if not v["ok_all"] and base_cases.get(cid, {}).get("ok_all"))
    fixes = sorted(cid for cid, v in report.per_case.items()
                   if v["ok_all"] and cid in base_cases and not base_cases[cid].get("ok_all"))
    return {"has_baseline": True, "baseline_model": baseline.get("model"), "baseline_date": baseline.get("date"),
            "baseline_intent_acc": baseline.get("intent_acc"), "deltas": deltas,
            "regressions": regressions, "fixes": fixes}


# ---- markdown ---------------------------------------------------------------------------------

def _pct(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def _delta(v: Optional[float], pct: bool = False) -> str:
    if v is None:
        return ""
    s = f"{v * 100:+.1f} pp" if pct else f"{v:+.4g}"
    return f" ({s})"


def to_markdown(report: Report, comparison: Optional[dict] = None) -> str:
    d = comparison["deltas"] if comparison and comparison.get("has_baseline") else {}
    lines = [f"# Intent eval: {report.model} ({report.adapter}), {report.date}", ""]
    lines += [
        "| metric | value |", "|---|---|",
        f"| cases / runs | {report.n_cases} / {report.n_results} |",
        f"| intent accuracy | {_pct(report.intent_acc)}{_delta(d.get('intent_acc'), pct=True)} |",
        f"| case accuracy (all fields) | {_pct(report.case_acc)}{_delta(d.get('case_acc'), pct=True)} |",
        f"| latency p50 / p95 | {report.p50_ms} / {report.p95_ms} ms{_delta(d.get('p50_ms'))} |",
        f"| prompt cache hit ratio | {_pct(report.cached_ratio)} |",
        f"| cost | ${report.cost_usd:.4f}{_delta(d.get('cost_usd'))} |",
        f"| errors | {report.errors} |",
        "",
    ]
    if comparison:
        lines.append("## Compared with the baseline")
        if not comparison.get("has_baseline"):
            lines += [comparison.get("note", "no baseline"), ""]
        else:
            lines.append(f"Baseline: {comparison.get('baseline_model')} {comparison.get('baseline_date')}, "
                         f"intent accuracy {_pct(comparison.get('baseline_intent_acc'))}.")
            lines.append(f"Regressions ({len(comparison['regressions'])}): "
                         + (", ".join(comparison["regressions"]) or "none"))
            lines.append(f"Fixes ({len(comparison['fixes'])}): " + (", ".join(comparison["fixes"]) or "none"))
            lines.append("")
    lines += ["## By intent", "", "| intent | n | intent acc | case acc | confused with |", "|---|---|---|---|---|"]
    for intent, v in report.by_intent.items():
        conf = ", ".join(f"{k}: {n}" for k, n in sorted(v["confusions"].items(), key=lambda kv: -kv[1])) or ""
        lines.append(f"| {intent} | {v['n']} | {_pct(v['intent_acc'])} | {_pct(v['case_acc'])} | {conf} |")
    lines += ["", "## By preset", "", "| preset | n | intent acc | case acc |", "|---|---|---|---|"]
    for p, v in report.by_preset.items():
        lines.append(f"| {p} | {v['n']} | {_pct(v['intent_acc'])} | {_pct(v['case_acc'])} |")
    lines += ["", "## By tag", "", "| tag | n | intent acc | case acc |", "|---|---|---|---|"]
    for t, v in report.by_tag.items():
        lines.append(f"| {t} | {v['n']} | {_pct(v['intent_acc'])} | {_pct(v['case_acc'])} |")
    if report.flaky:
        lines += ["", f"## Flaky ({len(report.flaky)})", "", ", ".join(report.flaky)]
    lines += ["", f"## Failures ({len(report.failures)})", ""]
    if not report.failures:
        lines.append("none")
    for f in report.failures[:MAX_FAILURES_IN_MARKDOWN]:
        got = json.dumps({k: v for k, v in f["got"].items() if v not in (None, [], "")}, ensure_ascii=False)
        lines.append(f"- `{f['id']}` [{f['preset']}] \"{f['text']}\": {'; '.join(f['mismatches'])}")
        lines.append(f"  got `{got}`")
    if len(report.failures) > MAX_FAILURES_IN_MARKDOWN:
        lines.append(f"- ... {len(report.failures) - MAX_FAILURES_IN_MARKDOWN} more in the JSON report")
    return "\n".join(lines) + "\n"
