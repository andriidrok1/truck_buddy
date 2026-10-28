"""The intent eval harness (backend/evals) without the model: dataset shape, presets, generated variants,
scoring, comparison and the runner against the canned LLM."""
import json
import re
from pathlib import Path

import pytest

import evals
from evals import gen_variants
from evals.dataset import intents_from_prompts, load_cases, read_jsonl, validate
from evals.presets import PRESETS, build_context
from evals.score import Report, compare, match_case, score, to_markdown
from services import agent

TESTS_DIR = Path(__file__).resolve().parents[1]
KNOWN_INTENTS = {"find", "go", "pick", "alternative", "add_stop", "clear_stops", "reviews", "weather", "hos",
                 "parking", "control", "breakdown", "chat", "bye", "unclear"}


# ---- dataset ----------------------------------------------------------------------------------

def test_prompts_name_every_known_intent():
    assert intents_from_prompts() == KNOWN_INTENTS


def test_dataset_is_well_formed():
    cases = load_cases()
    assert validate(cases, PRESETS, intents_from_prompts(), agent.CATEGORIES) == []
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))
    hand = read_jsonl(evals.HAND_FILE)
    assert sum(c["source"] == "hand" for c in hand) >= 80
    assert sum(c["source"] == "legacy" for c in hand) == 22
    assert all(c["source"] in ("hand", "legacy") for c in hand)
    assert all(c["source"].startswith("gen:") for c in read_jsonl(evals.GENERATED_FILE))


def test_dataset_covers_every_intent_and_category():
    hand = read_jsonl(evals.HAND_FILE)
    assert {c["expected"]["intent"] for c in hand} == KNOWN_INTENTS
    assert {c["expected"].get("category") for c in hand if c["expected"]["intent"] == "find"} == set(agent.CATEGORIES)
    assert {c["context_preset"] for c in hand} == set(PRESETS)


def test_validate_flags_bad_lines():
    good = read_jsonl(evals.HAND_FILE)[0]
    bad = [dict(good, id="x1", context_preset="nowhere"),
           dict(good, id="x2", expected={"intent": "teleport"}),
           dict(good, id="x3", tags=["nope"]),
           dict(good, id="x4", expected={"intent": "find", "category": "spa"}),
           dict(good, id="x5", expected={"intent": "find", "near": {"contains": "Lodi"}}),
           dict(good, id="x6", expected={"intent": "pick", "place_index": 2}),
           dict(good, id="x6", expected={"intent": "chat", "reply": "hi"})]
    problems = validate(bad, PRESETS, intents_from_prompts(), agent.CATEGORIES)
    assert len(problems) == 8 and sum("duplicate id" in p for p in problems) == 1


def test_legacy_eval_script_is_gone():
    assert not (TESTS_DIR / "eval_intent_models.py").exists()


# ---- presets ----------------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(PRESETS))
def test_every_preset_builds_a_context_offline(agent_sandbox, fake_http, name):
    req, context, truck = build_context(name)
    assert context.startswith("Driver location: 38.58160, -121.49440.")
    assert truck.label in agent.SYSTEM.format(truck=truck.label, prefs=truck.prefs_label, controls="", parking="",
                                              breakdown="")
    assert fake_http.calls == []  # no provider, no satellite, no DB


def test_preset_contexts_carry_their_state(agent_sandbox):
    assert "No route is active" in build_context("idle")[1]
    route = build_context("route_active")[1]
    assert "Current route: Pilot Travel Center" in route and "4.7 miles, about 9 minutes" in route
    options = build_context("options_shown")[1]
    assert "1=Pilot Travel Center (truck_stop)" in options and "diesel $5.39" in options
    assert "2=Love's Travel Stop" in options and "3=Flying J Travel Center" in options
    assert "[current route]" in options and "Driver: find me a truck stop" in options
    assert "Stops on the way: Love's Travel Stop" in build_context("stops")[1]
    near = build_context("near_follow_up")[1]
    assert "Stockton" in near and "No route is active" in near
    past = build_context("past_memory")[1]
    assert "Long-term memory" in past and "take me to Reno" in past


# ---- generated variants -----------------------------------------------------------------------

def test_generated_file_matches_the_generator():
    assert gen_variants.render() == evals.GENERATED_FILE.read_text()
    assert gen_variants.render() == gen_variants.render()
    assert gen_variants.main(["--check"]) == 0


def test_generated_variants_follow_the_rules():
    hand = {c["id"]: c for c in read_jsonl(evals.HAND_FILE)}
    gen = read_jsonl(evals.GENERATED_FILE)
    assert gen
    per_parent: dict[str, list] = {}
    for g in gen:
        parent = hand[g["parent"]]
        assert "core" in parent["tags"] and "core" not in g["tags"]
        assert g["expected"] == parent["expected"] and g["context_preset"] == parent["context_preset"]
        assert g["text"] != parent["text"]
        per_parent.setdefault(g["parent"], []).append(g)
    for parent_id, kids in per_parent.items():
        assert sum("noisy_stt" in k["tags"] for k in kids) <= gen_variants.MAX_NOISY_PER_CASE
        assert sum("disfluency" in k["tags"] for k in kids) == 1
    core = [c for c in hand.values() if "core" in c["tags"]]
    assert set(per_parent) == {c["id"] for c in core}
    noisy = [g for g in gen if "noisy_stt" in g["tags"]]
    assert any(g["text"] == "I need few" for g in noisy)  # l_02 "I need fuel", fuel -> few
    assert any(g["text"].endswith(" please") for g in gen) and any(g["text"].startswith("uh, ") for g in gen)


def test_variants_of_one_case():
    case = {"id": "t_x", "text": "Fuel near the Pilot on route 50", "context_preset": "idle",
            "expected": {"intent": "find"}, "tags": ["core", "near"], "source": "hand"}
    kids = gen_variants.variants_of(case)
    texts = [k["text"] for k in kids]
    assert texts[:2] == ["Few near the Pilot on route 50", "Fuel near the Pilot on ride 50"]  # table order, max 2
    assert len(kids) == 3 and kids[2]["tags"] == ["near", "disfluency"] and kids[2]["source"].startswith("gen:wrapper")
    assert gen_variants.variants_of(dict(case, tags=["near"])) == []
    assert gen_variants.variants_of(dict(case, source="gen:wrapper:uh", parent="p")) == []


# ---- matching and scoring ---------------------------------------------------------------------

@pytest.mark.parametrize("expected, got, ok_intent, ok_all", [
    ({"intent": "find", "category": "hotel"}, {"intent": "find", "category": "hotel"}, True, True),
    ({"intent": "find", "category": "hotel"}, {"intent": "find", "category": "truck_stop"}, True, False),
    ({"intent": "find", "category": None}, {"intent": "find", "category": "hotel"}, True, False),
    ({"intent": "find", "category": None}, {"intent": "find"}, True, True),
    ({"intent": "find"}, {"intent": "chat"}, False, False),
    ({"intent": "go", "query": {"contains": "reno"}}, {"intent": "go", "query": "Reno, NV"}, True, True),
    ({"intent": "go", "query": {"contains": "reno"}}, {"intent": "go", "query": None}, True, False),
    ({"intent": "go", "query": "Reno, NV"}, {"intent": "go", "query": "reno, nv"}, True, True),
    ({"intent": "weather", "query": None}, {"intent": "weather", "query": ""}, True, True),
    ({"intent": "weather", "query": None}, {"intent": "weather", "query": "Reno"}, True, False),
    ({"intent": "pick", "place_index": [2, 3]}, {"intent": "pick", "place_index": 3}, True, True),
    ({"intent": "pick", "place_index": [2, 3]}, {"intent": "pick", "place_index": "2"}, True, True),
    ({"intent": "pick", "place_index": [2]}, {"intent": "pick", "place_index": None}, True, False),
    ({"intent": "chat", "reply": {"contains": "4.7"}}, {"intent": "chat", "reply": "About 4.7 miles."}, True, True),
    ({"intent": "chat", "reply": {"contains": "4.7"}}, {"intent": "chat", "reply": "Not far."}, True, False),
    ({"intent": "control", "actions": ["zoom_in"]}, {"intent": "control", "actions": [{"type": "zoom_in"}]},
     True, True),
    ({"intent": "control", "actions": ["zoom_in"]}, {"intent": "control", "actions": []}, True, False),
    ({"intent": "breakdown", "kind": "tire"}, {"intent": "breakdown", "kind": "tire"}, True, True),
    ({"intent": "unclear"}, {}, False, False),
    ({"intent": "unclear"}, "not a dict", False, False),
])
def test_match_case_table(expected, got, ok_intent, ok_all):
    a, b, mismatches = match_case(expected, got)
    assert (a, b) == (ok_intent, ok_all)
    assert bool(mismatches) == (not ok_all)


CANNED_CASES = [
    {"id": "a", "text": "I need fuel", "context_preset": "idle", "tags": ["core"], "source": "hand",
     "expected": {"intent": "find", "category": "truck_stop"}},
    {"id": "b", "text": "take me to Reno", "context_preset": "idle", "tags": ["core"], "source": "hand",
     "expected": {"intent": "go", "query": {"contains": "reno"}}},
    {"id": "c", "text": "thanks", "context_preset": "route_active", "tags": ["edge"], "source": "hand",
     "expected": {"intent": "chat"}},
    {"id": "d", "text": "bye", "context_preset": "route_active", "tags": [], "source": "legacy",
     "expected": {"intent": "bye"}},
]


def _result(cid, got, ms=100.0, error=None):
    return {"id": cid, "got": got, "ms": ms, "prompt_tokens": 2000, "cached_tokens": 1500, "completion_tokens": 40,
            "error": error}


def test_score_on_canned_results():
    results = [_result("a", {"intent": "find", "category": "truck_stop"}, 80),
               _result("b", {"intent": "go", "query": "Rina, NV"}, 120),      # right intent, wrong field
               _result("c", {"intent": "bye", "reply": "Anytime."}, 90),     # wrong intent
               _result("d", {}, 500, error="TimeoutError: slow")]            # error
    rep = score(CANNED_CASES, results, model="gpt-5.4-mini", date="2026-10-27")
    assert rep.n_cases == 4 and rep.n_results == 4 and rep.errors == 1
    assert rep.intent_acc == 0.5 and rep.case_acc == 0.25
    assert rep.confusions == {"chat→bye": 1, "bye→None": 1}
    assert rep.by_intent["chat"]["confusions"] == {"bye": 1} and rep.by_intent["find"]["case_acc"] == 1.0
    assert rep.by_tag["core"] == {"n": 2, "intent_acc": 1.0, "case_acc": 0.5}
    assert rep.by_preset["route_active"]["intent_acc"] == 0.0
    assert rep.p50_ms == 120.0 and rep.p95_ms == 500.0  # nearest rank over [80, 90, 120, 500]
    assert rep.cached_ratio == 0.75 and rep.cost_usd > 0
    assert [f["id"] for f in rep.failures] == ["b", "c", "d"]
    assert rep.failures[2]["mismatches"][0].startswith("error:")
    assert rep.flaky == [] and rep.per_case["a"] == {"ok_intent": True, "ok_all": True}
    md = to_markdown(rep)
    assert "| intent accuracy | 50.0% |" in md and "chat→bye" not in md and "bye: 1" in md and "`b`" in md


def test_score_marks_flaky_cases_across_repeats():
    results = [_result("a", {"intent": "find", "category": "truck_stop"}),
               _result("a", {"intent": "chat"}),
               _result("d", {"intent": "bye"}), _result("d", {"intent": "bye"})]
    rep = score(CANNED_CASES[:1] + CANNED_CASES[3:], results)
    assert rep.flaky == ["a"] and rep.per_case["a"]["ok_all"] is False and rep.per_case["d"]["ok_all"] is True
    assert rep.n_results == 4 and rep.intent_acc == 0.75
    assert "## Flaky (1)" in to_markdown(rep)


def test_compare_finds_regressions_and_fixes():
    now = score(CANNED_CASES, [_result("a", {"intent": "find", "category": "truck_stop"}),
                               _result("b", {"intent": "chat"}),
                               _result("c", {"intent": "chat"}),
                               _result("d", {"intent": "bye"})], date="2026-10-27")
    baseline = {"model": "gpt-5.4-mini", "date": "2026-10-20", "intent_acc": 0.75, "case_acc": 0.75,
                "p50_ms": 90.0, "p95_ms": 100.0, "cached_ratio": 0.5, "cost_usd": 0.001,
                "per_case": {"a": {"ok_intent": True, "ok_all": True}, "b": {"ok_intent": True, "ok_all": True},
                             "c": {"ok_intent": False, "ok_all": False}, "d": {"ok_intent": True, "ok_all": True}}}
    cmp = compare(now, baseline)
    assert cmp["has_baseline"] and cmp["regressions"] == ["b"] and cmp["fixes"] == ["c"]
    assert cmp["deltas"]["intent_acc"] == 0.0 and cmp["deltas"]["cached_ratio"] == 0.25
    md = to_markdown(now, cmp)
    assert "Regressions (1): b" in md and "Fixes (1): c" in md and "(+0.0 pp)" in md


def test_compare_with_the_empty_placeholder_baseline():
    rep = Report(model="m", date="d", intent_acc=1.0)
    placeholder = json.loads(evals.BASELINE_FILE.read_text())
    assert placeholder == {"intent_acc": None}
    for baseline in (placeholder, None, {}):
        cmp = compare(rep, baseline)
        assert cmp["has_baseline"] is False and cmp["regressions"] == [] and cmp["fixes"] == []
    assert "no baseline yet" in to_markdown(rep, compare(rep, placeholder))


# ---- runner -----------------------------------------------------------------------------------

def _canned_reply(expected: dict) -> dict:
    """A classifier answer that satisfies every expected field."""
    out = {"intent": expected["intent"], "category": expected.get("category")}
    for k in ("query", "near", "when", "kind", "reply"):
        v = expected.get(k)
        out[k] = v["contains"] if isinstance(v, dict) else v
    if "place_index" in expected:
        out["place_index"] = expected["place_index"][0]
    if "actions" in expected:
        out["actions"] = [{"type": t, "value": None} for t in expected["actions"]]
    return out


def test_runner_smoke_with_the_fake_llm(agent_sandbox, monkeypatch, tmp_path, capsys):
    from evals import run_intents
    monkeypatch.setattr(agent, "MODEL", agent.MODEL)  # main() points it at --model; restore after
    results_dir, baseline = tmp_path / "results", tmp_path / "baseline.json"
    monkeypatch.setattr(run_intents, "RESULTS_DIR", results_dir)
    monkeypatch.setattr(run_intents, "BASELINE_FILE", baseline)
    cases = load_cases()[:3]
    for c in cases:
        agent_sandbox.llm.on("^" + re.escape(c["text"]) + "$", _canned_reply(c["expected"]))

    rc = run_intents.main(["--limit", "3", "--model", "fake-mini", "--concurrency", "2", "--compare",
                           "--fail-below", "1.0", "--promote"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "# Intent eval: fake-mini (chat_json)" in out and "| intent accuracy | 100.0% |" in out
    assert "no baseline yet" in out and "gate passed: no baseline yet" in out
    assert agent.MODEL == ("openai", "fake-mini")
    written = sorted(p.name for p in results_dir.iterdir())
    assert len(written) == 2 and written[0].endswith("-fake-mini.json") and written[1].endswith("-fake-mini.md")
    data = json.loads((results_dir / written[0]).read_text())
    assert data["n_cases"] == 3 and data["intent_acc"] == 1.0 and data["cost_usd"] > 0
    assert set(data["per_case"]) == {c["id"] for c in cases}
    assert data["p50_ms"] is not None and data["cached_ratio"] == 0.0
    assert json.loads(baseline.read_text())["intent_acc"] == 1.0  # --promote
    assert len(agent_sandbox.llm.calls) == 3
    assert "[Driver location: 38.58160, -121.49440." in agent_sandbox.llm.calls[0][1]

    # Second run against the promoted baseline: one answer changes, the gate must catch the regression.
    agent_sandbox.llm.rules.insert(0, (re.compile("^" + re.escape(cases[1]["text"]) + "$"), {"intent": "chat"}))
    rc = run_intents.main(["--limit", "3", "--model", "fake-mini", "--compare", "--fail-below", "1.0"])
    out = capsys.readouterr().out
    assert rc == 1 and f"Regressions (1): {cases[1]['id']}" in out and "gate FAILED" in out


def test_runner_selection_and_errors(agent_sandbox, monkeypatch, tmp_path, capsys):
    from evals import run_intents
    monkeypatch.setattr(agent, "MODEL", agent.MODEL)
    monkeypatch.setattr(run_intents, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(run_intents, "BASELINE_FILE", tmp_path / "none.json")
    assert run_intents.main(["--tags", "no_such_tag"]) == 2  # nothing selected
    cases = run_intents.select(load_cases(), tags="memory", preset="past_memory", limit=2)
    assert len(cases) == 2 and all(c["context_preset"] == "past_memory" for c in cases)
    # No canned reply: the FakeLlm raises, the runner records an error and the absolute gate fails.
    rc = run_intents.main(["--limit", "1", "--fail-below", "0.5"])
    out = capsys.readouterr().out
    assert rc == 1 and "| errors | 1 |" in out and "error: AssertionError" in out


def test_tools_adapter_is_a_stub():
    import asyncio
    from evals import adapters
    from models import TruckProfile
    with pytest.raises(NotImplementedError):
        asyncio.run(adapters.tools("hi", "ctx", TruckProfile()))
    assert set(adapters.ADAPTERS) == {"chat_json", "tools"}
