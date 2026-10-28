"""Intent evaluation harness for the copilot classifier (services/agent.classify).

Dataset: intents.jsonl (hand-written + migrated legacy cases) and intents.generated.jsonl (deterministic
STT-noise variants, see gen_variants.py). Presets build the dialog context exactly like the live agent
does (presets.py). score.py grades and reports, run_intents.py is the CLI that hits the real model.
"""
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent
HAND_FILE = EVALS_DIR / "intents.jsonl"
GENERATED_FILE = EVALS_DIR / "intents.generated.jsonl"
RESULTS_DIR = EVALS_DIR / "results"
BASELINE_FILE = EVALS_DIR / "baseline.json"

# Every tag a case may carry. Closed on purpose: a typo in the dataset fails the offline test.
TAGS = frozenset({
    "core",        # eligible for generated variants (gen_variants.py)
    "noisy_stt",   # generated: one homophone substitution
    "disfluency",  # generated: "uh, ...", "... please", "hey buddy ..."
    "near",        # a find with a near point
    "follow_up",   # depends on the conversation so far
    "memory",      # long-term memory in the context that must not be acted on
    "ambiguous",   # more than one answer is defensible; expected is the preferred one
    "address",     # a street address in the text
    "edge",        # odd phrasing or a trap for the classifier
})
