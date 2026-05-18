---
name: verifier-review-protocol
description: >-
  Verifier's review process for a single step report or a challenge response. Defines the Two-gate evaluation (Goal Gate + Logic Gate), required output format, ACCEPT/CHALLENGE/TRACE_BACK verdict criteria, and the Verified Results summary block.
  WHEN: when reviewing a Reasoner step report, evaluating a Reasoner response in the challenge loop, or producing a verdict. Used by the Verifier agent.
user-invocable: false
---

# Verifier Review Protocol

You are evaluating a Reasoner's report or response. Your output **must** follow the Two-gate model and the required structure below.

## FIRST: Verify problem understanding

Did Reasoner interpret the problem statement and ALL conditions correctly?

- Check if any conditions were misread
- Check if any constraints were overlooked or wrongly added
- If problem understanding is wrong, CHALLENGE immediately - don't verify flawed reasoning!

## Two-Gate Evaluation

Every verdict must pass **two independent gates**. Failure of either gate means **CHALLENGE** (or TRACE_BACK if the root cause is in an earlier step).

### Gate 1 — Goal Gate

> "What was Step N supposed to achieve, and did the Reasoner achieve it?"

The current step's goal is in the injected `PROBLEM_STATE` context (look for "Current Step"). Compare it to what the Reasoner actually produced.

| Goal Met | Action |
|---|---|
| **YES** — full target achieved | Goal Gate passes; proceed to Logic Gate |
| **NO** — target not met (Reasoner only got partial result, only one direction of an inequality, only special cases, etc.) | **Goal Gate fails → CHALLENGE.** Do **not** ACCEPT a partial result regardless of how correct the partial reasoning is. |
| **PARTIAL** — substantive progress but incomplete | **Goal Gate fails → CHALLENGE.** Specify exactly what is still missing. |

**Examples of common Goal Gate failures**:
- Step asks for "the answer" but Reasoner gave "a lower bound"
- Step asks to "prove for all n" but Reasoner verified for n ≤ 100
- Step asks for "smallest such x" but Reasoner gave "an x that satisfies the property"
- Step asks for "necessary and sufficient" but Reasoner only proved sufficient

If Goal Gate fails, **state explicitly**: "Goal Gate FAILED: step asks <X>, Reasoner produced <Y>. Gap: <Z>."

### Gate 2 — Logic Gate

> "Is the Reasoner's reasoning correct on its own terms?"

For each tagged claim, check:
- `[verified]` — code review (no re-run for long computations); does code match the claim; does conclusion follow from output. See `verification-tag-protocol` skill.
- `[easy-verify]` — run code yourself; review logic; compare output.
- `[hard-verify]` — logical soundness; hidden assumptions; gaps.

Also check:
- Consistency with previous steps' verified results
- For conjectures/pattern claims: Are enough test cases checked? Could alternative formulas also fit the data? Is the pattern extrapolated from too few examples?
- Counterexamples that disprove the claim? If you suspect one, test it yourself or CHALLENGE the Reasoner to prove no counterexample exists.
- Code faithfulness (was code actually run? was algorithm right?)

If Logic Gate fails, specify the flaw precisely.

### Adversarial sub-check for sharpness/optimality claims

Whenever the Reasoner's report contains an **answer-bearing** claim — a specific final value, a "sharp" / "tight" / "optimal" / "largest" / "smallest" / "necessary and sufficient" claim, or a "no better X exists" claim, and the report **does not provide both directions** (e.g. only an upper bound, only a construction, only a sufficient condition), Logic Gate is **not yet decided**. You must run an active falsification check before issuing the verdict:

1. Spawn a sub-agent with `task(agent="reason-noncoding", ...)` and
   instruct it to follow the **`construct-counterexamples`** skill.
2. The sub-agent has **no automatic context** — it does not see the injected `[Live problem state]`, the Reasoner's report, or this verification. The prompt **must** therefore include, verbatim:
   - The **problem statement** (so the sub-agent understands the domain).
   - The **exact claim under test** (the one-line sharpness/optimality statement, including all qualifiers like "sharp", "for every", "no better").
   - The **assumptions** the claim is supposed to hold under.
   - The **Reasoner's approach / construction** (this becomes the `forbidden direction` in the sub-agent skill — the sub-agent must attack from a different angle).
   - Any prior `[Conjecture]`-tagged sharp claims from `Verified Results` (so the sub-agent does not waste time re-testing already-suspected values).
3. Wait for the sub-agent to finish. Read its `### COUNTEREXAMPLE_VERDICT`.
4. Apply the verdict:
   - `FOUND_COUNTEREXAMPLE` → Logic Gate **fails**. Verdict is CHALLENGE (or PROPOSE_REPLAN if the failed claim is the plan's central conjecture). Cite the counterexample in your `## Reason`.
   - `NOT_FOUND` → Logic Gate may pass on this point, but the claim is **not promoted to `[Lemma]`** in your Verified Results. Record the achieved direction as `[Lemma]` and the unachieved direction as `[Conjecture]`, and note the adversarial check in `## Reason`.

You **may skip** this sub-check only when both directions are explicitly proved in the report (e.g. matching upper-bound proof + lower-bound construction with the same value). When in doubt, run it.

## Verdict Rule

```
both gates pass             → ACCEPT
Logic Gate fails            → CHALLENGE
Goal Gate fails             → CHALLENGE
root cause in earlier step  → TRACE_BACK to Step M
plan itself is unsound      → PROPOSE_REPLAN
```

A flaw originating in an earlier step is **TRACE_BACK to Step M**, not CHALLENGE — even if the current step technically follows from it.

**PROPOSE_REPLAN** is reserved for the case where no choice of step output could rescue the current plan: the plan as a whole asks the wrong question, conflates concepts, or chains steps that cannot logically reach the answer. Use this sparingly — TRACE_BACK is almost always the right call. Issuing PROPOSE_REPLAN escalates to the Meta-Strategist, who decides whether to actually re-plan, trace back, or override your suggestion.

## Required Output Structure

Output **must** start with this header and **must** include all sections in this order. Sections may be empty (`(none)`) but must be present.

```
# Verification: Step N

## Goal Gate
- Step Goal: <quote from PROBLEM_STATE current step>
- Reasoner Achieved: <one-line description>
- Goal Met: YES | NO | PARTIAL
- (If NO/PARTIAL) Gap: <what's missing>

## Logic Gate
- [verified] claims:
  - <each claim status, OK or specific issue>
- [easy-verify] claims:
  - <each claim status>
- [hard-verify] claims:
  - <each claim status>
- Consistency with prior steps: <OK | issue>
- Code review issues: <none | list>

## Verdict
**ACCEPT** | **CHALLENGE** | **TRACE_BACK** | **PROPOSE_REPLAN**

## TRACE_BACK_TO
<integer M, only when verdict is TRACE_BACK; otherwise omit this section>

## Reason
<First paragraph: a brief, single-paragraph summary that names the core issue in plain language. The orchestrator extracts this paragraph as the record-keeping reason — keep it self-contained, no bullet lists, no headings.>

<Optional further paragraphs with longer detail.>

## Verified Results
- [Lemma] <statement>
- [Definition] <statement>
- [Computation] <statement>
- [Conjecture] <statement, if any>
- [Answer] <value, if a final answer is stated>

(Use `(none)` if no items.)
```

## Critical Rules

1. **Goal Gate failure → CHALLENGE, not ACCEPT.** Even if all logic is correct, do not accept partial achievement of the step's goal.
2. **Quote the step goal from the injected PROBLEM_STATE context** in the Goal Gate section. Don't paraphrase.
3. **The `Verified Results` summary is mandatory** even on CHALLENGE/TRACE_BACK/PROPOSE_REPLAN — it captures what *did* get established (lemmas/computations/etc.) so future steps can use them.
4. **For TRACE_BACK**, format the verdict as exactly `**TRACE_BACK**` and emit a separate `## TRACE_BACK_TO` section whose body is a single integer `M` on its own line. `M` is the **earliest step containing the root-cause error**: the orchestrator will discard step `M` and every step after it, then re-execute starting from `M`. Choose `M` carefully — it must satisfy `1 <= M <= current_step`. If you believe the error is local to the current step (no earlier step is wrong), use **CHALLENGE**, not TRACE_BACK. If you cannot identify a specific earlier step, use **PROPOSE_REPLAN**, not a guessed `M`.
5. **For PROPOSE_REPLAN**, format the verdict as exactly: `**PROPOSE_REPLAN**` and use the `## Reason` paragraph to state why no plan-internal fix would work. The Meta-Strategist, not you, makes the final call.
6. **The `## Reason` first paragraph is treated as the record-keeping reason summary** — keep it brief, self-contained, no bullets/headings.
7. **Do not re-paste problem statement, plan, or completed-step content** in your response — they are already in the injected context.
8. **For long computations**, do not re-run or ask the Reasoner to re-run; verify based on results provided.

## Lemma vs Conjecture: tagging discipline in `## Verified Results`

The tags in your `Verified Results` block are **your judgment**, not the Reasoner's. They become the seed for every future step's reasoning, so mis-tagging a conjecture as a lemma can sink the entire plan.

- **`[Lemma]`** = a fully proved general claim. The Reasoner's report contains a complete deductive proof (or rigorous reduction to earlier `[Lemma]`s) that the claim holds for *all* objects in scope, not merely for tested cases.
- **`[Conjecture]`** = a claim supported by evidence but not fully proved.
  This is the correct tag for: pattern-extrapolation claims, "no counterexample found" outcomes, sharpness/optimality claims with only one direction proved, claims that survived the adversarial sub-check but lack a deductive proof.
- **`[Computation]`** = a specific numeric or symbolic result that was actually computed by code or short manual deduction (e.g. "f(7) = 42").
- **`[Definition]`** = notation, modeling choices, or definitional
  reformulations.
- **`[Answer]`** = a final numeric/symbolic answer to the original
  problem, only used at the very last accepting step.

### Sharpness/optimality require both directions for `[Lemma]`

A claim of the form "X is the sharp / tight / largest / smallest / optimal value such that ..." has two halves:

- **(a)** an inequality establishing the bound (e.g. an explicit family showing the quantity reaches X, or a structural argument showing the quantity cannot exceed X);
- **(b)** a matching argument showing X cannot be improved on the other side (e.g. "no smaller value also satisfies the constraint", or "for any value tighter than X there is a witness violating it").

If only **one** half is proved, you may tag the proved half as `[Lemma]`, but the *sharpness claim itself* is `[Conjecture]`. Example:

```
- [Lemma] <The proved direction, in problem-specific terms>.
- [Conjecture] X is sharp / optimal. (Adversarial sub-check ran; counterexample search via task(reason-noncoding, construct-counterexamples) returned NOT_FOUND after trying <list>; falsification not exhausted.)
```

This discipline is **mandatory** — do not promote a one-sided claim to `[Lemma]` even if it is "obviously" sharp. If you do, the next plan attempt will treat it as bedrock and waste rounds proving an unproved target.
