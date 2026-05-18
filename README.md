# STAR-PólyaMath

A multi-agent framework for solving competition mathematics problems with persistent meta-strategic supervision. STAR-PólyaMath couples a Python orchestrator with three LLM agents — a **Reasoner**, a **Verifier**, and a persistent **Meta-Strategist** — running on top of the GitHub Copilot CLI.

For the system design, evaluation, and ablations, see the paper:
> *STAR-PólyaMath: Multi-Agent Reasoning under Persistent Meta-Strategic Supervision.*

---

## Requirements

- **Python ≥ 3.10**
- **GitHub Copilot CLI** v1.0.40+, authenticated (`copilot --version` should work)
- No external Python dependencies beyond the standard library

Optional, for routing models through your own API endpoint instead of Copilot's defaults:
- An Azure OpenAI / OpenAI / Anthropic API key (see [BYOK](#byok-bring-your-own-key))

---

## Quick start

```bash
# Solve one problem
python -m src.runner --problem-file problems/AIME2025/aimeI1.txt

# Inline problem statement
python -m src.runner --problem "Prove that sqrt(2) is irrational"

# Give the run a custom identifier (otherwise: <filename>_<timestamp>)
python -m src.runner --problem-file problems/IMO2025/imo2025_6.txt \
                    --problem-id imo2025_6_run1

# Run Meta-Strategist pre-planning analysis before the Reasoner plans
python -m src.runner --problem-file problems/IMO2025/imo2025_6.txt --hint

# Inject a human-written hint instead
python -m src.runner --problem-file problem.md --hint "Try induction on n"
```

Each run materializes a self-contained working directory under `scratch/<problem_id>/` containing the plan, per-step reports, debate transcripts, verification scripts, and the final `solution.md`.

### CLI options

| Flag | Description |
|---|---|
| `--problem-file FILE` | Problem in markdown/text format (one of `--problem-file` / `--problem` is required) |
| `--problem TEXT` | Inline problem statement |
| `--problem-id ID` | Custom run identifier; controls the `scratch/<problem_id>/` directory name (default: `<filename>_<timestamp>`) |
| `--hint` | Run Meta-Strategist pre-planning analysis before the Reasoner plans |
| `--hint "text"` | Inject a custom human-written hint instead of running pre-planning analysis |
| `--hint-file FILE` | Read the hint from a file |
| `--quiet` | Suppress verbose progress output |

---

## Output layout

```
scratch/<problem_id>/
├── problem.md              # input statement (orchestrator writes once)
├── plan.md                 # current plan
├── PROBLEM_STATE.md        # canonical per-problem state injected to agents
├── state.json              # machine-readable mirror (orchestrator writes)
├── steps/
│   ├── step-NN-report.md   # Reasoner output for step NN
│   ├── step-NN-verify.md   # Verifier verdict for step NN
│   └── step-NN-debate.md   # challenge-loop transcript (if any)
├── code/                   # Reasoner + Verifier verification scripts
├── archive/                # archived plan/step ranges from re-plans
├── solution.md             # accepted final solution
└── solution_stats.md       # per-run wall-clock, agent calls, tokens
```

---

## Configuration

All runtime parameters live in [`src/config.py`](src/config.py). Notable defaults:

| Parameter | Default | Meaning |
|---|---|---|
| `REASONER_MODEL` / `VERIFIER_MODEL` / `META_STRATEGIST_MODEL` | `claude-opus-4.7` | Model used per role |
| `REASONER_TIMEOUT` | `1800` s | Per-call timeout for a Reasoner step |
| `VERIFIER_TIMEOUT` | `1200` s | Per-call timeout for a Verifier review |
| `META_STRATEGIST_TIMEOUT` | `600` s | Per-call timeout for a Meta-Strategist intervention |
| `CODE_EXECUTION_TIMEOUT` | `600` s | Per-script execution timeout |
| `MAX_CHALLENGE_ROUNDS` | `5` | Max Reasoner ↔ Verifier debate rounds per step |
| `MAX_REPLANS` | `3` | Max plan replacements per problem |

The same configuration is used for every benchmark problem reported in the paper.

---

## BYOK (bring your own key)

By default, the Copilot CLI routes every model call. To route specific models through your own provider (Azure OpenAI / OpenAI / Anthropic):

1. `cp config.toml.example config.toml` and fill in your endpoint, API key, and per-model deployment mappings under `[provider.wire_models]`. See the comments in [`config.toml.example`](config.toml.example) for the full schema.
2. Update the model IDs in `src/config.py` (e.g. `REASONER_MODEL`) to reference the BYOK-routed names.

Models not listed in `wire_models` continue to use Copilot's default routing. `config.toml` is gitignored.

---

## Repository layout

```
src/                       # Python orchestrator (no LLM logic)
  runner.py                # entry point; advances the state machine
  orchestrator.py            # control flow
  orchestrator_mixins/       # per-phase helpers
  agents/                  # per-role CLI dispatch
  hooks/                   # sessionStart state-injection hook
  state.py                 # ProblemState + state.json schema
  config.py                # timeouts and loop bounds
  utils/                   # CLI wrapper, parsing, session naming

.github/agents/            # agent role definitions (.agent.md)
.github/skills/            # load-on-demand operational protocols

problems/                  # benchmark problem statements
scratch/                   # per-problem working directories (auto-created)
```
