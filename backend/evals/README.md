# Intent evals

Measures `services/agent.classify` (the one LLM call per copilot turn) on a fixed set of driver phrases:
does the model land on the right intent, category, place, destination and control, and how fast and how
cached is it. The offline tests in `tests/offline/test_evals.py` guard the harness itself; grading the real
model is a separate, paid run.

## Run

```
cd backend
python -m evals.run_intents --model gpt-5.4-mini --concurrency 8
python -m evals.run_intents --tags core,near --preset options_shown --limit 20   # a slice
python -m evals.run_intents --repeat 3                                           # flaky ids
python -m evals.run_intents --compare --fail-below 1.0                           # the CI gate
python -m evals.run_intents --compare --promote                                  # new baseline
```

Needs `OPENAI_API_KEY` (read from `backend/.env` when present). Every run writes
`evals/results/<date>-<model>.json` and `.md` (git-ignored) and prints the markdown. One full run is
about 285 classifier calls on a ~2.5k-token prompt: roughly $0.5 at the gpt-5.4-mini list price, less once
the prompt cache warms up (`cached_ratio` in the report). The dollar figure in the report uses the
placeholder rates in `score.PRICES`; check them against the OpenAI pricing page before quoting it.

The gate: with `--compare`, the run must reach `baseline intent accuracy × --fail-below` and must not fail
any case the baseline passed (a regression). With the empty placeholder baseline the gate is skipped, so
the first PR after a prompt change has to promote. Without `--compare`, `--fail-below` is an absolute
intent accuracy.

CI: `.github/workflows/intent-eval.yml` runs every Monday 06:00 UTC, on `workflow_dispatch` (model, tags),
and on pull requests labelled `eval` (add the label, or push again while it is on). It posts the markdown
as the job summary and as a PR comment and uploads `evals/results` as an artifact.

## Dataset

`intents.jsonl` holds the hand-written and the migrated legacy cases, `intents.generated.jsonl` the
deterministic STT-noise variants. One JSON object per line:

```
{"id": "h_find_near_lodi", "text": "get me a truck stop near Lodi", "context_preset": "idle",
 "expected": {"intent": "find", "category": "truck_stop", "near": {"contains": "lodi"}},
 "tags": ["core", "near"], "source": "hand"}
```

`expected` fields and how they are matched (`score.match_case`):

| field | rule |
|---|---|
| `intent` | exact (always required) |
| `category` | exact, or `null` = the model must not name one |
| `query`, `near`, `when`, `kind` | exact string (case-insensitive), `null`, or `{"contains": "lowercase substring"}` |
| `place_index` | list of accepted 1-based indexes |
| `reply` | `{"contains": ...}` only |
| `actions` | list of action types that must all be present (control) |

`context_preset` names a dialog state from `presets.py`: `idle`, `route_active` (route to the Pilot, 4.7 mi /
9 min), `options_shown` (three places on the list), `route_active_alts` (the same with one alternative route cached, as a live dialog has), `stops` (one stop on the way), `near_follow_up` (last
search was near Stockton), `past_memory` (earlier dialogs in long-term memory; must not be acted on). The
context string is built by the real `LlmAgentProvider._context`, so the model sees exactly what it sees in
production. Places come from `tests/fixtures/google/truck_stop_sac.json`.

Tags are a closed set (`evals.TAGS`); `core` marks the cases that get generated variants.

### Adding cases

1. Append a line to `intents.jsonl` with a new id (`h_<intent>_<slug>`), a preset and the expected fields.
   Tag it `core` when it is a clean, representative phrasing worth stress-testing with noise.
2. `python -m evals.gen_variants --write` to refresh `intents.generated.jsonl` (the offline test fails when
   it is stale: `python -m evals.gen_variants --check`).
3. `python -m pytest -n 0 tests/offline/test_evals.py -q` validates the schema, ids, presets and tags.
4. Run the eval on the new slice before committing: `python -m evals.run_intents --tags <tag>` or
   `--limit`; a case the current model cannot pass belongs in the PR only together with the prompt change
   that makes it pass, or with the `ambiguous` tag and a note.

Generated variants: `gen_variants.HOMOPHONES` (one substitution per variant, at most two per core case,
tag `noisy_stt`) and the three wrappers (`uh, ...`, `... please`, `hey buddy ...`, one per core case chosen
by a hash of the id, tag `disfluency`). They inherit the parent's expected fields.

### Changing the prompt and promoting the baseline

The baseline is `evals/baseline.json`, a full report of the last accepted run. In a PR that changes
`agent.SYSTEM`, a sub-prompt or the dataset:

1. Run `python -m evals.run_intents --compare` and read the regressions and fixes.
2. When the new numbers are the ones to keep, run `python -m evals.run_intents --compare --promote` and
   commit the updated `evals/baseline.json` in the same PR, so the CI gate on that PR (and the weekly run
   after merge) compares against the prompt as changed. Mention the before/after intent accuracy in the PR.
3. Never promote a run with `--limit`, `--tags` or `--preset`: a partial report would make every skipped
   case look like a regression on the next full run.
