# TruckBuddy: rules for agents

Voice copilot for truck drivers. `backend/` is FastAPI (agent, routing, places, weather, parking), `frontend/` is Expo + expo-router in TypeScript (screens in `app/`, the rest in `src/`). README.md has the layout, every env variable and the frontend conventions; read it before your first change. `docs/QA.md` explains the three test layers.

## Git

- Never push to `main`. Andrii (or Abdul, `zhadyz`) merges. "Merge it" from Andrii in the current conversation is the only exception, and it covers the PRs he named.
- One PR per feature or fix. Branch name `claude/<area>-<slug>`, area is `agent`, `backend`, `app` or `docs`.
- Work in a separate worktree: `git worktree add ../truck_buddy-<slug> -b claude/<area>-<slug> origin/main`. The main checkout `~/WebstormProjects/truck_buddy` feeds Metro and the emulator stand and often holds uncommitted copies; don't switch its branch or reset it.
- Base on `origin/main` unless the work needs an open PR, then stack on that branch and write the merge order in the PR body.
- Merging a stack: retarget the child PR to `main` first, then merge the parent, then delete the parent branch. Deleting a base branch closes every PR stacked on it.
- Commit messages and PR bodies in English, plain and specific: what changed for the driver, how it was checked.

## Commands

A fresh worktree has no dependencies. Before running anything in it: `source ~/.venvs/truckbuddy/bin/activate` for the backend (`python` and `flake8` are not on the system PATH) and `cd frontend && yarn install --frozen-lockfile` for the app.

```bash
# backend, offline (what CI runs, no keys, no network)
cd backend && python -m pytest -n 0 -m "not live" -q tests
cd backend && flake8 services server.py models.py tests/offline tests/test_*.py scripts

# app
cd frontend && yarn typecheck && yarn lint --max-warnings 0 && yarn test --ci

# intent eval: paid, needs OPENAI_API_KEY with credits; on a PR add the label `eval`
cd backend && python -m evals.run_intents --compare --fail-below 1.0
```

Local backend: `cd backend && uvicorn server:app --port 8001 --reload` (keys in `backend/.env`, never commit it). The emulator stand is the `/test-app` skill (`~/.claude/skills/test-app/`).

## Definition of done

A change is done when all of these hold:

1. The offline suite for every side you touched is green, plus flake8 or lint with zero warnings.
2. New behaviour has a test. Spoken lines are asserted verbatim in backend tests, so a feature that silently stops talking fails.
3. A change to the classifier prompt or intents went through the eval gate (`--compare`) or the PR says why it could not (no credits) and is labelled `eval`.
4. A new backend feature sits behind an env flag read at call time, off by default unless Andrii said otherwise, and the flag is in the README table.
5. A visible UI change has screenshots from the emulator in light and dark theme, with no Expo dev menu or overlay on top, and the `ui-reviewer` agent passed them.
6. A second agent reviewed the diff. The author never reviews its own work.

## Frontend rules (in-cab use)

The driver looks at the screen for two seconds at 65 mph. Everything follows from that.

- Every tappable element is at least 56 pt in both directions (use `hitSlop` only to reach 56 on an icon that has to look smaller).
- One primary action per screen state. Secondary actions are visually quieter.
- Text the driver reads while moving (the answer, the next turn, the destination) at least 17; the 12 and 14 steps of the type scale are for secondary labels only. Sizes and spacing come from `design_guidelines.json` and `src/theme.ts`.
- Colors only from `useTheme()`; no hex literals in screens or components. Check both themes; contrast WCAG AA at least.
- The answer stays on screen until the driver dismisses it or a new one replaces it; never tie visible text to the lifetime of audio.
- Every interactive element gets a `testID` (the emulator stand taps by it).
- Animations: `ReducedMotionConfig mode=Never` is set in `app/_layout.tsx` on purpose. Reanimated `entering` on the first mount is invisible when the animator scale is 0 (the emulator stand), so don't hang content on it.
- A screen file over ~300 lines gets split into hooks (`src/hooks`) and components (`src/components`) before new features land in it. `app/index.tsx` is the known offender.
- Logic that can be a pure function goes to `src/geo`, `src/voice` or `src/state` with a jest test.

## Backend rules

- Offline tests never touch the network: provider responses come from `tests/fixtures/<provider>/*.json`, an unmocked call fails the test.
- Flags are read at call time (`os.getenv` inside the function or `services/flags.py`), so a flag flip on prod needs no deploy.
- Prod runs `main` on Lightsail (`api.truckbuddy.io`), deployed by `.github/workflows/deploy-backend.yml` after merge. Don't ssh in and change things by hand.

## Agents in `.claude/agents/`

The loop is QA report → issue → spec → code → cleanup → review → QA again. The main session is the conductor: it hands work to these agents, reads what comes back and opens the PR.

| Agent | Input | Output | Touches code |
|---|---|---|---|
| `triage` | a QA report path in the vault | GitHub issues with labels, no duplicates | no |
| `planner` | an issue number or a bug description | a spec with acceptance criteria, posted on the issue | no |
| `implementer` | a spec | a branch in its own worktree, green tests, commits | yes |
| `cleaner` | a branch after the feature works | simplification commits with identical behaviour | yes, no behaviour change |
| `ui-reviewer` | screenshot paths | pass or a list of violations per screenshot | no |

Code review of the diff goes to `/code-review` or a fresh `general-purpose` agent, never to the implementer. Driving the app as a driver is the `/test-app` skill; its screenshots go to `ui-reviewer`.

## Secrets

Keys live in `backend/.env` and on the server only. Never print them, commit them or put them in a PR, issue or spec.
