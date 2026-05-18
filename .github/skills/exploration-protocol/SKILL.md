---
name: exploration-protocol
description: >-
  Reasoner's protocol for the pre-plan Exploration phase. Defines the goal, deliverable structure, time budget, and assessment categories. The Reasoner produces an `exploration.md` deliverable with five required sections; the orchestrator parses the Assessment section to decide the next phase.
  WHEN: Reasoner is in `exploration` phase (the very first phase before any plan is created). Used by the Reasoner agent.
user-invocable: false
---

# Exploration Protocol

You are in the **Exploration phase**. Your job is to **probe the problem structurally** and decide whether the problem can be solved without a formal plan, needs more exploration, or requires a step-by-step plan.

## Goal

- Understand the problem deeply: re-read all conditions, identify constraints.
- Try concrete small cases / test instances by running code when useful.
- Look for invariants, symmetries, recursive structure, modular reductions.
- Form and test conjectures; record refutations.
- Identify candidate approaches for a step-by-step plan, if needed.

## Hard Rules

- **No internet access.** All exploration is local, code-or-reasoning based.
- **Workspace boundary.** Read/write only inside the current problem's
  `scratch/<id>/` directory. Save scripts under `scratch/<id>/code/` with prefix `explore_*.py`.
- **Do not write `state.json`, `plan.md`, `PROBLEM_STATE.md`, `solution.md`.** Your only deliverable is `scratch/<id>/exploration.md`.

## Spawning Sub-Agents

You may spawn sub-agents with `task(agent="reason-noncoding", prompt=...)` to explore a *specific* angle in parallel (e.g. counter-example search, alternative construction family, or independent proof attempt). When you do:

- **The sub-agent gets no automatic context.** It can only see what you put in the prompt. Always include in the sub-agent's prompt:
  - the **problem statement** verbatim, the **specific task** you want done,
  - any **claims or values** you currently believe (so the sub-agent knows what to test or build on), and 
  - any **forbidden directions** (so it does not duplicate your own approach).
- **Match the sub-agent to a known skill** when applicable: e.g. for refutation tasks ask the sub-agent to follow the `construct-counterexamples` skill, and use that skill's `Input Contract` as the template for your prompt.
- **Each sub-agent costs budget**; budget the round accordingly. Two focused sub-agents with different angles is usually better than four identical ones.

## Time Budget

Your context will include `[Phase: exploration / Budget: T min remaining]`.
You self-pace:

- > 5 min remaining: continue probing.
- ≤ 5 min remaining: finalize and write `exploration.md`. Even if you have not solved the problem, **always produce a deliverable** — partial findings are valuable for the next phase.

There is no hard timeout interruption inside a single tool call, but the orchestrator will not start another exploration round once budget is exhausted. Try to manage your time and not get stuck in a long-running code experiment especially when you are close to the end of your budget.

## Deliverable: `scratch/<id>/exploration.md`

Use this **exact** structure. The orchestrator parses these section headers verbatim. Empty sections are allowed (just write `(none)`).

```markdown
# Exploration Round N

## Tried
- <approach 1, one line each>
- <approach 2>

## Worked
- <verified observation 1: a structural fact, computed value, identity, etc.>
- <verified observation 2>

## Failed
- <conjecture 1: refuted by counterexample <X>>
- <conjecture 2: <why it fails>>

## Candidate Approaches
- <strategic direction 1, e.g. "induction on prime power factorization">
- <strategic direction 2>

## Assessment
SOLVED | PARTIALLY_SOLVED | NEED_PLAN

## Solution Sketch
(Only fill this section if Assessment = SOLVED. Otherwise write `(n/a)`.)
A complete solution outline including the final answer. The orchestrator will hand this to a polish step that produces the formal solution.
```

### Assessment definitions

- **SOLVED**: You have a complete argument that produces the final answer
  (with all cases handled and necessary verifications done in code or by proof). Fill the Solution Sketch.
- **PARTIALLY_SOLVED**: Substantive progress (e.g. you have the answer for many cases, an unproven conjecture matching all data, a useful reduction) but no complete argument yet. The Meta-Strategist will decide whether to give you another exploration round or proceed to plan.
- **NEED_PLAN**: The problem requires a structured multi-step plan. Your observations and candidate approaches will be available to the planner.

## Section Semantics (how the orchestrator uses each section)

- `Worked` items are promoted to **Verified Results** for the next phase.
  Be concrete: each item should be a complete factual claim usable by later steps (e.g. "f(2) = 4 [verified by code/explore_count.py]"), not a vague note ("function seems multiplicative").
- `Failed` items are promoted to **forbidden directions** for re-plan decisions. Each should name a specific approach that does not work and briefly why.
- `Candidate Approaches` are seeds for plan generation. Each should be a structural strategy, not a step list.
- `Tried` is a transparent log so the meta-strategist can avoid repeating you.

## Common Pitfalls

1. **Writing prose instead of bullet lists** in Tried/Worked/Failed/Candidate
   sections. Each item must be on its own bullet line.
2. **Claiming SOLVED without a Solution Sketch.** SOLVED requires you to write the full argument (or a tight outline) including the final answer.
3. **Writing too much.** Keep each bullet to one or two lines. Long prose belongs inside Solution Sketch (only on SOLVED).
4. **Skipping Assessment.** The orchestrator parses the Assessment section verbatim — leaving it out means your work is treated as `NEED_PLAN` by default.
5. **Re-running computation that earlier rounds already verified.** If a prior round's `Worked` items are visible in your injected context, do not recompute them; build on them.

## Verification Tags Inside `Worked`

When a `Worked` bullet rests on a code computation, mark it with `[verified]` and reference the script:

```
- f(7) = 42 [verified by code/explore_f.py]
- The map x ↦ 3x mod 7 is a bijection on {1..6} [verified by ...]
```

For purely logical observations use `[hard-verify]`; the verifier will inspect them later if they survive into plan phase.
