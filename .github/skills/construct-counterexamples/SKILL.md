---
name: construct-counterexamples
description: >-
  Active falsification: try to refute a proposed conjecture, lemma, or intermediate claim by finding examples that satisfy its assumptions but violate its claimed conclusion. Used by the Reasoner to stress-test fragile conjectures before promoting them, and by the Verifier (via a sub-agent task) to challenge sharpness/optimality claims before tagging them `[Lemma]` in Verified Results.
  WHEN: a claim is conjectural rather than fully proved; a sharpness/extremal claim is on the verge of being recorded; a verifier sees an "answer-bearing" step claim with only one-sided evidence.
user-invocable: false
---

# Construct Counterexamples

You are trying to **refute** a specific claim. Your default belief is that the claim is wrong; your job is to find evidence that it is. Only after a deliberate effort may you report `NOT_FOUND`.

## Input Contract

The invoking prompt should provide:

- the **specific claim under test** (verbatim, including any sharp / optimal / largest / smallest / "no better" wording)
- the **assumptions** under which the claim is supposed to hold
- the **approach the claimer used**, treated as a forbidden direction (attack from a different angle)
- a soft time budget

## Procedure

1. **Read the claim adversarially.** Restate it as "the assumptions hold AND the conclusion fails" — your job is to find such a witness.
2. **List candidate obstruction families** the claimer did not try, then make at least one concrete attempt at each. Use code when the claim is computational; save scripts under `scratch/<problem_id>/code/` with prefix `cx_*.py`.
3. **Search broadly, not deeply.** Several simple attempts beat one elaborate failure.
4. **Stop on first refutation.** Report `FOUND_COUNTEREXAMPLE` as soon as one candidate shows the assumptions hold while the conclusion fails.

### Construction families to try when nothing comes to mind

Use these as orthogonal attack directions when the claimer's approach is "obvious" and you are stuck for a fresh angle. They are deliberately generic — pick the one(s) most natural for the problem's combinatorial structure.

- **Higher-dimensional / denser packing**: if the claimer used a 1-D / linear / chain-like construction, try a 2-D / planar / lattice-filling construction in the same domain. 1-D chain → 2-D filling → 3-D / layered. Denser packings often shift the extremal ratio sharply. For symmetric properties, also consider from 1D (left/right) to 2D (North/East/South/West) and even higher dimensions.
- **Recursive / fractal / self-similar**: build a sequence of objects where each iteration applies a fixed substitution rule. Limits of such sequences frequently exceed any constant the claimer extracted from a fixed family.
- **Randomized / probabilistic**: average over a large random sample of valid objects. Even a single bad sample is a counterexample; the *typical* value can also disagree with the claimer's extremal target.
- **Boundary / extremal / degenerate**: drive a parameter to 0, infinity, or a critical threshold. Many "obvious" extremal claims break at the boundary of the domain because the claimer implicitly assumed an interior case.
- **Symmetry-broken**: if the claimer's construction has a symmetry
  (cyclic, reflective, rotational), break the symmetry — non-symmetric optima sometimes beat symmetric ones in combinatorial problems.
- **Concatenation / gluing**: take two valid objects of the type the claim concerns and glue them along a boundary. The combined object's ratio can be very different from each piece's.

If the hint specifies a forbidden direction, treat **its complement** along one of these axes as your first attempt.

## Critical Rules

1. **Do not anchor on the claim.** Famous or "obviously true" claims still must be tested.
2. **Honor the forbidden direction.** Do not re-run the claimer's own approach — your value is in attacking from somewhere else.
3. **Be honest about scope.** A counterexample must satisfy *all* stated assumptions; if it violates one, it is a different problem.
4. **No internet access; workspace boundary.** All work stays inside the current problem's `scratch/<id>/`.

## Output Format

Return exactly one of these two blocks (parsed by the caller).

### Counterexample found

```
### COUNTEREXAMPLE_VERDICT
FOUND_COUNTEREXAMPLE

### CLAIM_REFUTED
<verbatim claim, with the failing part noted>

### COUNTEREXAMPLE
<concise description: object, parameter values, computed quantities>

### WHY_IT_REFUTES
<one paragraph: assumptions satisfied; conclusion fails because <quantity> = <observed>, not <claimed>>

### EVIDENCE
<script path or short table of computed values>
```

### Counterexample not found

```
### COUNTEREXAMPLE_VERDICT
NOT_FOUND

### CLAIM_TESTED
<verbatim claim>

### TRIED
- <approach 1>
- <approach 2>
- <approach 3>

### REMAINING_DOUBT
<one paragraph: what you did not explore; whether you believe the claim is likely correct or merely uncontradicted by your bounded search>
```
