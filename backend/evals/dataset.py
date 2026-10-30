"""Loading and validating the eval dataset (one JSON object per line).

Line schema:
  {"id", "text", "context_preset", "expected": {...}, "tags": [...], "source": "hand"|"legacy"|"gen:<rule>", "parent"?}
expected fields: intent (exact), category (exact or null), query/near/when/kind (exact string, null, or
{"contains": "lowercase substring"}), place_index (list of accepted ints), reply ({"contains": ...} only),
actions (list of action types that must all be present).
"""
import json
import re
from pathlib import Path
from typing import Iterable, Optional

from evals import GENERATED_FILE, HAND_FILE, TAGS

STRING_FIELDS = ("query", "near", "when", "kind")
EXPECTED_FIELDS = ("intent", "category") + STRING_FIELDS + ("place_index", "reply", "actions")


def read_jsonl(path: Path) -> list[dict]:
    out = []
    if not path.exists():
        return out
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise ValueError(f"{path.name}:{n}: bad JSON ({e})") from e
    return out


def dumps_jsonl(cases: Iterable[dict]) -> str:
    return "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases)


def load_cases(hand: Path = HAND_FILE, generated: Optional[Path] = GENERATED_FILE) -> list[dict]:
    """Hand + legacy cases first, then the generated variants, in file order."""
    cases = read_jsonl(hand)
    if generated is not None:
        cases += read_jsonl(generated)
    return cases


def intents_from_prompts() -> set[str]:
    """Every intent the prompts know: the enum line in agent.SYSTEM plus the "- name:" bullets of the
    system prompt and the three sub-prompts (breakdown only exists as a bullet)."""
    from services import agent, approach, breakdown, controls
    found = set()
    m = re.search(r'"intent":\s*((?:"[a-z_]+"\|?)+)', agent.SYSTEM)
    if m:
        found.update(re.findall(r'"([a-z_]+)"', m.group(1)))
    for text in (agent.SYSTEM, controls.PROMPT, approach.PROMPT, breakdown.PROMPT):
        found.update(re.findall(r"^- ([a-z_]+):", text, re.M))
    return found


def validate(cases: list[dict], presets: Iterable[str], intents: set[str], categories: Iterable[str]) -> list[str]:
    """Problems with the dataset (empty when it is well-formed)."""
    problems: list[str] = []
    seen: set[str] = set()
    presets = set(presets)
    categories = set(categories)
    for c in cases:
        cid = c.get("id")
        where = f"case {cid!r}"
        if not isinstance(cid, str) or not cid:
            problems.append(f"{where}: missing id")
            continue
        if cid in seen:
            problems.append(f"{where}: duplicate id")
        seen.add(cid)
        if not isinstance(c.get("text"), str) or not c["text"].strip():
            problems.append(f"{where}: missing text")
        if c.get("context_preset") not in presets:
            problems.append(f"{where}: unknown preset {c.get('context_preset')!r}")
        src = c.get("source", "")
        if not (src in ("hand", "legacy") or src.startswith("gen:")):
            problems.append(f"{where}: bad source {src!r}")
        if src.startswith("gen:") and not c.get("parent"):
            problems.append(f"{where}: generated case without parent")
        tags = c.get("tags")
        if not isinstance(tags, list) or not set(tags) <= TAGS:
            problems.append(f"{where}: tags {tags!r} not within TAGS")
        exp = c.get("expected")
        if not isinstance(exp, dict):
            problems.append(f"{where}: expected must be an object")
            continue
        for k in exp:
            if k not in EXPECTED_FIELDS:
                problems.append(f"{where}: unknown expected field {k!r}")
        if exp.get("intent") not in intents:
            problems.append(f"{where}: intent {exp.get('intent')!r} not in the prompts")
        if "category" in exp and exp["category"] is not None and exp["category"] not in categories:
            problems.append(f"{where}: category {exp['category']!r} not in CATEGORIES")
        for k in STRING_FIELDS:
            v = exp.get(k)
            if k in exp and not (v is None or isinstance(v, str) or _is_contains(v)):
                problems.append(f"{where}: {k} must be a string, null or {{\"contains\": ...}}")
        pi = exp.get("place_index")
        if "place_index" in exp and not (isinstance(pi, list) and pi and all(isinstance(i, int) for i in pi)):
            problems.append(f"{where}: place_index must be a non-empty list of ints")
        if "reply" in exp and not _is_contains(exp["reply"]):
            problems.append(f"{where}: reply must be {{\"contains\": ...}}")
        acts = exp.get("actions")
        if "actions" in exp and not (isinstance(acts, list) and all(isinstance(a, str) for a in acts)):
            problems.append(f"{where}: actions must be a list of action types")
    return problems


def _is_contains(v) -> bool:
    return (isinstance(v, dict) and set(v) == {"contains"} and isinstance(v["contains"], str)
            and v["contains"] == v["contains"].lower())
