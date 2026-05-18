# Project Instructions

This repository solves competition mathematics problems through a multi-agent reasoning system built on GitHub Copilot CLI. These instructions describe ·system architecture, per-problem directory layout, file ownership, and shared rules. **Per-problem dynamic state** (current plan, verified results, failures) lives in `scratch/<problem_id>/PROBLEM_STATE.md`, injected into agents at runtime by hooks (not via this file).

## System Architecture

A Python orchestrator coordinates four AI agents, each defined in [.github/agents/](../.github/agents/):

- Orchestrator: yes/no flow control, decisions, dispatch (no math reasoning, Python only)
- Reasoner: problem solving by generating and executing plans, response to challenges
- Verifier: reviews reports, challenges flaws, accepts/rejects
- Meta-Strategist: strategic advisor for re-plan / timeout / calibration / final-check

## Per-Problem Directory Layout

When solving a problem, the orchestrator creates `scratch/<problem_id>/` and maintains the following structure.

```
scratch/<problem_id>/
├── problem.md              # original problem statement (orchestrator writes once)
├── plan.md                 # current plan
├── PROBLEM_STATE.md        # canonical per-problem state for agents
├── state.json              # machine-readable state mirror (orchestrator writes)
├── steps/
│   ├── step-NN-report.md   # Reasoner output for step NN
│   └── step-NN-verify.md   # Verifier verdict for step NN
├── code/                   # all verification scripts
│   ├── step{NN}_*.py        # Reasoner verification scripts
│   └── verify_step{NN}_*.py # Verifier verification scripts
├── archive/                # archived attempts on re-plan / trace-back
│   └── plan-vK/
├── solution.md             # final solution (Reasoner writes once verified)
```

### Hard rules for agents

- **DO NOT read files outside your own `scratch/<problem_id>/` directory.**
- **DO NOT write to `state.json`, `plan.md`, or `PROBLEM_STATE.md`.** These are orchestrator-managed. Read for context if needed.
- **Reasoner verification scripts go in `code/`** as `step{NN}_<short_name>.py`; **Verifier verification scripts go in `code/`** as `verify_step{NN}_<short_name>.py`.
- The problem statement is in `problem.md`. The original `problems/<set>/<id>.md` files are read-only references — do not modify.

## Sessions

Each agent role uses a deterministic, persistent session name per problem:

- Reasoner: `reason-<problem_id>` (cross-step, persistent)
- Verifier: `verify-<problem_id>-step{NN}` (per-step, fresh each step)
- Meta-Strategist: `meta-<problem_id>` (cross-problem-lifecycle, persistent)

The orchestrator manages first-spawn (`--name <NAME>`) vs resume (`--resume <NAME>`) automatically; agents do not need to manage session identity themselves.

## Skills

Operational protocols are loaded on demand from [.github/skills/](../.github/skills/); do not paraphrase or rewrite protocols inline.

## Code Execution Guidelines

For computation-heavy steps, especially exploratory search:

- **Long-running code is acceptable** when necessary (up to 600 seconds).
- **Always set a timeout** in your script to prevent runaway execution.
- If the computation can be optimized analytically first, **optimize before running**.
- For brute-force search, include progress output (e.g. `print` every N iterations).

## Reasoning Output Conventions

- Tag every nontrivial claim with `[verified]`, `[easy-verify]`, or `[hard-verify]` — see the `verification-tag-protocol` skill for semantics.
- After running any code, **immediately restate**: (1) the script path, (2) the key results in plain text. This preserves results across context loss.
- Do **not** re-run code already marked `[verified]` in earlier steps; trust the saved result.
- Use rigorous mathematical reasoning before committing to brute-force computation.
