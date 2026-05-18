---
name: meta-intervention-protocol
description: >-
  Unified intervention protocol for the Meta-Strategist agent.
  Defines output formats, critical rules, and scenario-specific guidance for diagnosing failures and providing actionable tasks to the Reasoner.
  WHEN: Meta-Strategist is diagnosing a failure (calibration mismatch, timeout, code error, stuck), mediating a deadlock, advising on code issues, or providing pre-planning analysis.
  DO NOT USE WHEN: Reasoner or Verifier is executing normally.
user-invocable: false
---

# Meta-Strategist Intervention Protocol

This skill defines the structured protocol the Meta-Strategist MUST follow when intervening in the problem-solving process. It covers output formats, critical rules, and per-scenario guidance.

---

## Intervention Scenarios

The Meta-Strategist intervenes in these situations:

| Scenario | Trigger | Goal |
|----------|---------|------|
| **Pre-Planning Analysis** | `--hint` flag or first-time problem analysis | Select applicable methods, identify pitfalls |
| **Code Issue Guidance** | Reasoner's code times out, errors, or gives wrong output | Suggest optimization or alternative approach |
| **Timeout Guidance** | Reasoner times out on a step | Provide strategic redirection |
| **Calibration Failure** | Reasoner's answer doesn't match expected answer | Guide error discovery without revealing answer |
| **Re-plan Decision** | Verifier `PROPOSE_REPLAN`, Reasoner `[plan-blocked]` tag, max-rounds debate stalemate, repeated step timeouts, or a `diagnose_and_guide` `replan` action | Decide APPROVE_REPLAN / CONTINUE / TRACE_BACK_TO N / ABORT and produce forbidden directions |
| **Unified Diagnose & Guide** | Any failure requiring structured intervention | Produce a SPECIFIC_TASK for Reasoner |

---

## Unified Diagnose & Guide Protocol

This is the main structured output format for `diagnose_and_guide` interventions.

### Required Output Sections

The response MUST contain these sections in order:

#### DIAGNOSIS
1-2 sentences: What is the root cause of this failure?

#### ACTION_TYPE
Exactly one of: `explore_more` | `retry_different_method` | `trace_back` | `replan`

**Naming note.** `replan` here is the same action as `APPROVE_REPLAN` in the Re-plan Decision protocol — it directs the orchestrator to archive the current plan, increment the replan counter, and start a new plan attempt. Returning `replan` from `diagnose_and_guide` short-circuits the second `decide_replan` call, so use it only when you are certain the plan itself (not just this step) is unsound. Use it carefully, as it is the most expensive option and causes the most disruption to the Reasoner's workflow.

#### TRACE_BACK_TO
Only if ACTION_TYPE is `trace_back`: which step number N to trace back to.

**SEMANTICS**: trace_back to N means Step N will be RE-EXECUTED from scratch.
Usually set N = CURRENT step to re-execute with new guidance while preserving earlier steps.
Only set N to an earlier step if that step's LOGIC was fundamentally wrong.

#### USE_PURE_REASONING
YES or NO — Should the Reasoner ANALYZE EXISTING computational results without running new code?

**USE_PURE_REASONING=YES means:**
- Reasoner should STUDY the code outputs/data that were ALREADY computed in previous runs
- Reasoner should REASON about patterns, derive bounds, prove theorems based on EXISTING evidence
- Reasoner should NOT manually recalculate what code already computed (LLM text math is unreliable!)
- Do not suggest the exact answer like "Prove the answer is xxx", because you may be wrong and lead the Reasoner astray. Let the Reasoner reason about existing data instead

**Say YES when:**
- Previous code runs produced correct data, but the wrong CONCLUSION was drawn from it
- A mathematical proof or bound derivation is needed, informed by existing computational evidence
- The Reasoner needs to THINK about structure rather than compute more cases

**Say NO when:**
- More computational exploration is genuinely needed
- The code itself needs to be fixed or optimized

#### SPECIFIC_TASK
The exact task Reasoner MUST execute. Be concrete. Include any hints about execution time, optimization, or structure to exploit.

#### EXECUTION_HINTS
Optional: timeout suggestions, code optimization tips, mathematical shortcuts.

---

## Critical Rules (for all Diagnose & Guide interventions)

1. **DO NOT suggest "start proving" if the conjecture is not fully explored**
   - If data for small n is incomplete, the TASK should be to explore more cases
   - Pay attention to SPECIAL STRUCTURE numbers

2. **DO NOT suggest exhaustive brute-force if previous attempts timed out**
   - Instead, suggest observing and exploiting existing patterns from smaller data
   - Develop as many hypotheses as possible for larger cases
   - Focus on more important/relevant cases that relate to the final problem-wanted case

3. **BE SPECIFIC about what to compute/explore**
   - BAD: "Try more test cases"
   - GOOD: "Compute the exact minimum for xxx."

4. **Consider if the PLAN itself needs adjustment**
   - If the current step's approach is fundamentally flawed, say so

5. **PRESERVE COMPUTED RESULTS — CRITICAL FOR TIMEOUT CASES**
   - Extract results from the Reasoner's Report and include them in SPECIFIC_TASK
   - Example: "The previous Reasoner already computed: xxx. DO NOT recompute these. Start from xxx."

6. **Be an Encouraging Coach — FOR CALIBRATION CASES**
   - Guide without telling explicitly. Help Reasoner and Verifier discover the error themselves.

7. **NEVER suggest "manually verify" or "hand-calculate" what code already computed**
   - LLM text-based arithmetic is LESS reliable than code execution
   - If you want to verify code results, suggest writing VERIFICATION code, not manual calculation
   - USE_PURE_REASONING means "analyze existing data", NOT "recalculate by hand"

---

## Pre-Planning Analysis Protocol

When analyzing a problem before planning:

### Output Constraints
- Problem type: one line
- Hints of given conditions: 1-2 lines
- Selected methods: bullet list, method names only (from `math-solving-strategies` skill)
- One key pitfall: one line
- Total output: MAX 10-15 lines

### Rules
- DO NOT explain methods, just name them
- DO NOT analyze the problem deeply
- DO NOT suggest specific approaches or solutions
- ONLY select from methods in the `math-solving-strategies` skill

### Output Format

```
Problem type: <one line>
Conditions: <1-2 lines>
Methods (from math-solving-strategies):
- <method name>
- <method name>
Pitfall: <one line>
```

---

## Code Issue Guidance Protocol

When advising on code issues:

1. Review the plan and previous steps to understand the current solving approach
2. Assess what went wrong in the code
3. Provide ONE clear primary recommendation
4. Provide ONE alternative approach if primary fails
5. Consider whether to continue or try a completely different approach based on retry count

Rules:
- BE SPECIFIC — give concrete suggestions
- DO NOT write code yourself
- Reference `code-issue-resolution` skill for optimization techniques

### Output Format

```
### Situation
(1-2 sentences: timeout / error / wrong output, and why)

### Primary
(One concrete fix — e.g. "reduce n from 1000 to 50", "add memoization on f(i,j)")

### Alternative
(Backup approach if primary fails)
```

---

## Timeout Guidance Protocol

When the Reasoner times out, analyze the partial output and provide **3-5 KEY insights (error analysis or strategic guidance)**.

CRITICAL RULES:

1. DO NOT recommend exhaustive/brute-force search again if that's what caused the timeout.
   
2. INSTEAD, recommend **reasoning-based approaches**:
   - Observe patterns in the data already computed
   - Propose structural hypotheses
   - Suggest targeted sampling with specific constructions (not random/exhaustive)
   - Look for mathematical structure that reduces the search space
   
3. **Be CONCISE - 3 bullet points maximum, each 1-2 sentences.**

Output Format:

```
### Key Observations
(What was Reasoner attempting? Why did it fail?)

### Recommendations
(If you are not sure, suggest general strategies, not specific conjectures.)
Do not suggest the exact answer like "Prove the answer is xxx", because you may be wrong and lead the Reasoner astray.
```

---

## Calibration Failure Protocol

Both the Reasoner and Verifier accepted this answer but it is WRONG. Analyze:
1. What reasoning led to this incorrect conclusion?
2. Where did the Reasoner or Verifier make a mistake?
3. What specific checks would reveal the error?

CRITICAL RULES:

1. **DO NOT suggest exhaustive computation for larger n** - if small n is already slow, larger n won't work.
2. **Emphasize REASONING over computation** - observe patterns, propose structural hypotheses.
3. **Focus on the SMALLEST unexplored case** - analyze smaller deeply before jumping to larger. Choose the right / most informative case that is relevant to the final problem-wanted case.
4. Do not suggest the exact answer like "Prove the answer is xxx", because you may be wrong and lead the Reasoner astray. Just help them find the error themselves.

OUTPUT FORMAT:

```
### Failure Mode
(1-2 sentences: what error type? Why did both Reasoner and Verifier miss it?)

### Key Checks
1. [First specific check - 1 sentence]
2. [Second specific check - 1 sentence]
3. [Optional third check - 1 sentence]
```

Be concise. DO NOT write more than this.

---

## Re-plan Decision Protocol

You are deciding what to do when a re-plan has been **proposed** by one of:
- the **Verifier** (`PROPOSE_REPLAN` verdict),
- the **Reasoner** (a `[plan-blocked: <reason>]` tag in a step report),
- the **orchestrator** (max-rounds debate stalemate, repeated step-execute timeouts, or a `diagnose_and_guide` `replan` action).

You are the only one who can authorize a re-plan. The proposer's view is advisory; consider also the prior `Confirmed Failures` injected in your context — if the same direction has already been tried and abandoned, prefer an `ABORT` decision over yet another doomed re-plan.

### Required Output Sections (verbatim section headers)

The response MUST contain these sections in order. The orchestrator will grep for `### FIELD\n...` blocks; do not nest extra `###` inside a field.

#### REPLAN_DECISION
Exactly one of: `APPROVE_REPLAN` | `CONTINUE` | `TRACE_BACK_TO` | `ABORT`

- `APPROVE_REPLAN`: throw out the current plan and start a new one
- `CONTINUE`: proposer is wrong; current plan is still viable
- `TRACE_BACK_TO`: do not re-plan; just revisit an earlier step
- `ABORT`: this problem has exhausted reasonable approaches; surface failure

#### TRACE_BACK_TO
Only required when REPLAN_DECISION is `TRACE_BACK_TO`. A single integer (the step number to re-execute). Otherwise omit or write `(n/a)`.

#### REASON_SUMMARY
A brief, single-paragraph summary that names the core issue. The orchestrator records this paragraph as the failure record's reason — keep it self-contained: no bullet lists, no headings, no leading `###`.

#### PLAN_SUMMARY
Only required when REPLAN_DECISION is `APPROVE_REPLAN`. A one-paragraph summary of the abandoned plan's overall approach, written from the outside ("Plan vK pursued <strategy>; this fails because ..."). Plain prose.

#### FORBIDDEN_DIRECTIONS
Only required when REPLAN_DECISION is `APPROVE_REPLAN`. A bullet list (3–6 items) of strategies the next plan MUST NOT pursue, each on its own line starting with `-`. Be concrete ("induction on n", not "the previous approach").

#### REUSABLE_RESULTS
Optional bullet list of verified intermediate results from the abandoned attempt that the next plan can build on. The orchestrator already auto-rescues verified step outputs; only add items here that the auto-rescue might miss (e.g. observations or partial lemmas that did not appear in a `Verified Results` block).

### Critical Rules for Re-plan Decisions

1. **Default to TRACE_BACK_TO over APPROVE_REPLAN.** Re-planning is expensive and most failures are step-level, not plan-level. *Exception: see rule 1b for orchestrator-triggered stalemate proposals.*
1b. **Orchestrator-triggered stalemate proposals are different.** If the proposer is `orchestrator` and the proposer reason indicates the Reasoner/Verifier exchanged the maximum allowed rounds of debate without converging, the working hypothesis is that the plan's conjectured target is wrong. Strongly prefer `APPROVE_REPLAN` (or `ABORT` if re-plan budget is exhausted) over `TRACE_BACK_TO` or `CONTINUE`. In particular, when the disputed step's title or report contains an "answer-bearing" claim (a specific numeric answer, a "sharp"/"tight"/"largest"/"smallest"/"optimal" claim, or a necessary-and-sufficient claim), **add that specific value or claim to `FORBIDDEN_DIRECTIONS`** so the next plan does not re-anchor on it.
2. **Inspect prior `Confirmed Failures` before approving.** If the same strategy has already been tried, choose `ABORT` rather than re-plan into a known dead-end.
3. **`FORBIDDEN_DIRECTIONS` must rule out the failed approach itself**, not just superficial variants of it.
4. **`REUSABLE_RESULTS` is a strict whitelist, not a default.** Most failed-plan results should NOT be carried over. Carry over a result only when it is a *purely factual* statement that is robust to the plan having been wrong — i.e. concrete `[Computation]` outputs from actually-run code, or `[Definition]` introductions of notation. Do **not** carry over `[Lemma]` claims tied to the failed conjecture, do not carry over anything tagged `[Conjecture]`, and do not carry over step-level "sharp/optimal/largest/smallest" claims.
5. **`REASON_SUMMARY` is the canonical record.** Keep it brief and concrete. It is what the next reasoner attempt will see in `PROBLEM_STATE`.
6. **Do not reveal the expected answer** even if you suspect it; the same confidentiality rule from Calibration Failure applies.
7. **Do not advocate for the current target.** If you have just been told the debate is deadlocked over whether the answer is X, do not say "the answer is probably still X, try harder". Re-plan or abort.
8. **Detect answer-anchoring across plans.** Inspect prior `Confirmed Failures`: if **two or more** abandoned plans were trying to prove the *same* candidate answer or the *same* sharp value, the failure mode is no longer "wrong proof technique" but "wrong target". The new plan you authorize **must** explicitly require the next attempt to refute or replace that target before re-attempting any inequality. Add to `FORBIDDEN_DIRECTIONS`:
   - `Do not assume the candidate value <X> is the answer; the next round must first try to construct a witness with a different value.`
   - `Do not start by proving any inequality whose RHS contains <X>; first establish the answer afresh via construction search.`
9. **When authorizing a re-plan, also authorize a re-explore.** A re-plan without re-explore lands the next reasoner attempt in the same evidence pool that produced the wrong target. The orchestrator wires a call to `meta_strategist_orchestrate_reexplore` automatically after APPROVE_REPLAN; your `FORBIDDEN_DIRECTIONS` list is what that re-explore uses, so be specific.

## Spawning Sub-Agents (re-explore orchestration and ad-hoc tasks)

When you spawn sub-agents (typically during re-explore orchestration, or for any independent task) with `task(agent="reason-noncoding", ...)`:

- **The sub-agent has no automatic context.** It does not see the injected `[Live problem state]`, the failed plans, your own session history, or any prior findings. The sub-agent only sees what you put in its prompt.
- **Therefore the prompt you write for each sub-agent must include**:
  the **problem statement** verbatim, the **specific task / hint / angle of attack**, all **forbidden directions** (verbatim, especially the current candidate answer that should be refuted), and a pointer to the appropriate skill if relevant (e.g. "follow the `construct-counterexamples` skill" or "follow the `exploration-protocol` skill").
- **You are the supervisor**: do not write the math yourself in the sub-agent prompt; describe the *task* and let the sub-agent reason. But do include enough domain detail (problem, prior attempts, the value being challenged) for the sub-agent to start work cold.