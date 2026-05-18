---
name: verification-tag-protocol
description: >-
  Unified verification tag definitions ([verified], [easy-verify], [hard-verify]). Defines tag semantics, Reasoner usage rules, Verifier review checklists, and Orchestrator routing logic.
  WHEN: writing step reports with verification tags, reviewing tagged claims, routing reports based on tag types, understanding verification tag semantics.
user-invocable: false
---

# Verification Tag Protocol

This skill defines the three verification tags used across the math reasoning multi-agent system.
All agents (Reasoner, Verifier, Meta-Strategist) and the Python Orchestrator share these definitions as a single source of truth.

---

## Tag Definitions

### `[verified]` — Code Actually Executed ✅ PREFERRED

The Reasoner has ACTUALLY RUN code and reports the real output.

**Reasoner usage:**
1. Write the Python code
2. Execute it using runCommands or Python tools
3. Copy the actual output
4. Tag as `[verified]`

**Example:**
```markdown
[verified] By executing this code:
\`\`\`python
import math
result = math.sqrt(2)
print(f"sqrt(2) = {result}")
\`\`\`
Output: sqrt(2) = 1.4142135623730951
Therefore, √2 ≈ 1.414.
```

**Verifier checklist for `[verified]` claims:**
- **Code Faithfulness**: Was the code actually run? Is the output genuine, not fabricated? Check logs or evidence of execution.
- **Algorithm Match**: Does the algorithm match the intended computation? Does it test enough cases or just cherry-picked ones?
- **Code Correctness**: Syntactically correct? Off-by-one errors? Boundary issues? Edge cases handled?
- **Output Verification**: Does stated output match what the code would produce? Does the conclusion follow from the output?

**Verifier action:** Review code logic and correctness (no need to re-run, especially for long computations). Check that the algorithm correctly implements the claim.

### `[easy-verify]` — Could Be Code Verified

Use ONLY when the Reasoner cannot run the code (e.g., requires special libraries).
Verifier will run this code.

**Reasoner usage:**
```markdown
[easy-verify] The sum 1 + 2 + ... + 100 = 5050.
\`\`\`python
result = sum(range(1, 101))
print(result)  # Should output: 5050
\`\`\`
```

**Verifier checklist for `[easy-verify]` claims:**
- Run the provided code yourself, or write code yourself to verify the claim
- **Review the code logic** before running (use `[verified]` checklist above)
- Compare output to the claimed result
- Report any discrepancies
- Check for potential bugs even if output matches

**Verifier action:** Must run the provided code, check output matches claim, and review code logic.

### `[hard-verify]` — Requires Logical Review

For logical arguments, proofs, or claims that cannot be computationally verified.

**Reasoner usage:**
```markdown
[hard-verify] By the Pigeonhole Principle, if we place n+1 objects into n boxes,
at least one box must contain more than one object.
```

**Verifier checklist for `[hard-verify]` claims:**
- Is the logical argument sound?
- Are there any hidden assumptions?
- Are there gaps in the reasoning?
- Is the conclusion properly supported?

**Verifier action:** Deep logical review — check assumptions, reasoning gaps, and conclusion validity.

---

## Reasoner Rules

1. **Tag EVERY conclusion** with one of the three tags
2. **Prefer `[verified]`** — run code yourself whenever possible. Do NOT use `[easy-verify]` when you can run it yourself.
3. **Do not re-run** previous steps' `[verified]` code. Use the provided results to derive conclusions.
4. **`[easy-verify]`** is only for code you genuinely cannot execute
5. **`[hard-verify]`** is for logical/proof arguments that cannot be computationally verified

### Non-coding mode (reason-noncoding)
When code execution tools are unavailable:
- Use primarily `[hard-verify]` for all mathematical reasoning and proofs
- Use `[easy-verify]` for calculations that the Verifier could verify with simple code

---

## Verifier Code Review Checklist

When reviewing any code (from `[verified]` or `[easy-verify]` claims):

- [ ] **Logic errors**: Does the algorithm do what it claims?
- [ ] **Off-by-one errors**: Range boundaries, array indices
- [ ] **Edge cases**: Empty input, n=0, n=1, single element
- [ ] **Faithfulness**: Was the code actually executed? Or is the output fabricated/hallucinated? Or does the code only search a very small subset of cases and claim generality?
- [ ] **Correctness of the claim**: Does output actually prove the claim?

**Important:** If Reasoner's codes have been run, especially for long-time computations, **do not** ask the Reasoner to re-run them. Verify arguments based on the results provided.

---

## Orchestrator Routing Logic

All reports are sent to Verifier regardless of tag composition.
The Orchestrator (Python control layer) only checks report format validity (tags exist), then forwards to Verifier.

The tag type tells Verifier what kind of review to perform:

| Tag | Verifier Review Type |
|-----|---------------------|
| `[verified]` | Code logic review (don't re-run, especially long computations) |
| `[easy-verify]` | Run code + logic review |
| `[hard-verify]` | Deep logical/proof review |
