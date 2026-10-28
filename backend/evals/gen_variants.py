"""Deterministic STT-noise variants of the core cases.

For every case tagged `core` in intents.jsonl:
- noisy_stt: one homophone substitution per variant (HOMOPHONES, in table order), at most 2 variants;
- disfluency: one wrapper ("uh, ...", "... please", "hey buddy ..."), chosen by a hash of the case id.
The expected answer is inherited from the parent: the prompt tells the model to read misheard words by
what makes sense for a trucker, so the variant must still land on the parent's intent and fields.

    python -m evals.gen_variants --write   # regenerate intents.generated.jsonl
    python -m evals.gen_variants --check   # exit 1 when the committed file is stale
"""
import argparse
import hashlib
import re
import sys
from pathlib import Path

from evals import GENERATED_FILE, HAND_FILE
from evals.dataset import dumps_jsonl, read_jsonl

HOMOPHONES: dict[str, str] = {
    "fuel": "few",
    "rest area": "rest a ride",
    "Lodi": "lady",
    "Love's": "loves",
    "exit": "exist",
    "diesel": "these all",
    "route": "ride",
    "Pilot": "pile it",
    "weigh": "way",
    "scale": "skill",
    "DEF": "deaf",
    "Reno": "Rina",
    "truck stop": "truck stock",
    "parking": "barking",
    "shower": "sour",
}
MAX_NOISY_PER_CASE = 2
WRAPPERS: list[tuple[str, str]] = [  # (name, template)
    ("uh", "uh, {text}"),
    ("please", "{text} please"),
    ("hey", "hey buddy {text}"),
]


def _slug(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")


def _substitute(text: str, key: str, repl: str) -> str:
    """First whole-word occurrence of key (case-insensitive), keeping a leading capital."""
    m = re.search(rf"(?<![A-Za-z]){re.escape(key)}(?![A-Za-z])", text, re.I)
    if not m:
        return text
    word = m.group(0)
    out = repl[0].upper() + repl[1:] if word[0].isupper() and not repl[0].isupper() else repl
    return text[:m.start()] + out + text[m.end():]


def _wrapper_index(case_id: str) -> int:
    return int(hashlib.sha1(case_id.encode()).hexdigest(), 16) % len(WRAPPERS)


def _child(parent: dict, suffix: str, text: str, tag: str, rule: str) -> dict:
    tags = [t for t in parent.get("tags", []) if t != "core"] + [tag]
    return {"id": f"{parent['id']}__{suffix}", "text": text, "context_preset": parent["context_preset"],
            "expected": parent["expected"], "tags": tags, "source": f"gen:{rule}", "parent": parent["id"]}


def variants_of(case: dict) -> list[dict]:
    if "core" not in case.get("tags", []) or str(case.get("source", "")).startswith("gen:"):
        return []
    out = []
    text = case["text"]
    hits = [k for k in HOMOPHONES if re.search(rf"(?<![A-Za-z]){re.escape(k)}(?![A-Za-z])", text, re.I)]
    for key in hits[:MAX_NOISY_PER_CASE]:
        noisy = _substitute(text, key, HOMOPHONES[key])
        if noisy != text:
            out.append(_child(case, f"stt_{_slug(key)}", noisy, "noisy_stt", f"homophone:{_slug(key)}"))
    name, template = WRAPPERS[_wrapper_index(case["id"])]
    base = text.rstrip("?.! ") if name == "please" else text
    out.append(_child(case, f"dis_{name}", template.format(text=base), "disfluency", f"wrapper:{name}"))
    return out


def generate(cases: list[dict]) -> list[dict]:
    out: list[dict] = []
    for c in cases:
        out += variants_of(c)
    return out


def render(hand_file: Path = HAND_FILE) -> str:
    return dumps_jsonl(generate(read_jsonl(hand_file)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="write intents.generated.jsonl")
    ap.add_argument("--check", action="store_true", help="exit 1 if the committed file differs")
    args = ap.parse_args(argv)
    text = render()
    n = text.count("\n")
    if args.write:
        GENERATED_FILE.write_text(text)
        print(f"wrote {n} generated cases to {GENERATED_FILE}")
        return 0
    current = GENERATED_FILE.read_text() if GENERATED_FILE.exists() else ""
    if args.check:
        if current == text:
            print(f"{GENERATED_FILE.name} is up to date ({n} cases)")
            return 0
        print(f"{GENERATED_FILE.name} is stale: run python -m evals.gen_variants --write")
        return 1
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
