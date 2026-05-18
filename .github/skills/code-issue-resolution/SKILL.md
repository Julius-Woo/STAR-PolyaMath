---
name: code-issue-resolution
description: >-
  Strategies for handling code execution issues during mathematical problem solving.
  Covers timeout resolution (reduce scale, optimize algorithm, change approach, parallelize), runtime error diagnosis, wrong output debugging, retry escalation, and Python optimization tips.
  WHEN: code timeout, runtime error, wrong output, code optimization, execution failed, MemoryError, RecursionError, code too slow, brute force timeout, when to abandon code verification.
user-invocable: false
---

# Code Execution Issue Resolution

This skill contains strategies for handling code execution issues during problem solving.
Use it when code times out, errors, or produces wrong output.

---

## Timeout Strategies

### Level 1: Reduce Scale
**Description:** Focus on smaller cases and less input.

**Rationale:**
- Smaller cases can reveal the pattern without waiting for large computation
- If working on multiple cases, try just one or two first. For example, if testing n=6:10, try just n=6 first.

### Level 2: Optimize Algorithm
**Description:** Improve time complexity

**Techniques:**
- **Memoization / Dynamic Programming:** Cache repeated subproblem results
- **Pruning search space:** Add early termination conditions, skip impossible branches
- **Better data structures:** Use sets for O(1) lookup instead of lists for O(n)
- **Early termination:** Stop when answer is found, don't continue unnecessarily
- **Mathematical shortcuts:** Use formulas instead of iteration where possible

**Common optimizations:**
```
O(n³) → O(n²): Precompute partial results
O(n²) → O(n log n): Sorting-based approach
O(n²) → O(n): Hash tables, two-pointer technique
O(2^n) → O(n²): Dynamic programming
```

### Level 3: Change Approach
**Description:** Brute force may not be the right path

**Guidance:**
- Maybe the pattern is visible from smaller cases already computed
- Maybe the answer has a closed-form formula
- Maybe the proof doesn't need exhaustive search
- Maybe there's a mathematical insight that avoids computation

**Questions to ask:**
1. What did smaller cases reveal?
2. Is there a pattern or recurrence?
3. Can we prove it mathematically instead of verifying computationally?

### Level 4: Use distributed computation
**Description:** Split the work across multiple processes

If you have to run large computations, consider using multiprocessing or distributed computing frameworks to parallelize the workload. For example, Python's `multiprocessing` module can help run multiple instances of your code simultaneously on different CPU cores.

---

## Error Strategies

### Runtime Errors

| Error Type | Common Causes | Solutions |
|------------|---------------|-----------|
| IndexError | Index out of bounds | Check array sizes, use len() |
| ZeroDivisionError | Division by zero | Add guards for denominator |
| RecursionError | Recursion depth exceeded | Use iteration or sys.setrecursionlimit |
| MemoryError | Too much memory used | Use generators, reduce storage |
| TypeError | Wrong type operations | Verify variable types |

### Wrong Output

**Debugging steps:**
1. Check small cases manually first
2. Print intermediate values to trace execution
3. Verify edge cases: n=0, n=1, empty input, single element
4. Compare with expected values
5. Check for off-by-one errors in loops and ranges

**Common issues:**
- Integer vs float division: Use `//` for integer division
- Modular arithmetic mistakes: Apply mod at each step to avoid overflow
- Boundary conditions: Inclusive vs exclusive ranges
- Initialization errors: Wrong starting values

---

## Retry Progression Strategy

When code fails repeatedly, escalate through these levels:

| Retry | Strategy | Action |
|-------|----------|--------|
| 1st | Quick fix | Fix obvious error, reduce input slightly |
| 2nd | Optimize | Add memoization, improve algorithm |
| 3rd | Rethink | Consider if computation is even necessary |
| Beyond | Alternative | Try completely different approach, maybe prove analytically |

---

## When to Abandon Code Verification

Sometimes code verification is not the right path:

1. **Infinite or very large search space:** Use mathematical proof instead
2. **Problem requires exact symbolic computation:** Use reasoning over computation
3. **Pattern is already clear from small cases:** State and prove the pattern
4. **Computational verification would take too long:** Accept [hard-verify] tag

**Remember:** Code is a tool for verification, not the only way to solve problems.

---

## Performance Tips

### Python-Specific Optimizations

```python
# Use sets for membership testing
bad:  if x in my_list:    # O(n)
good: if x in my_set:     # O(1)

# Use list comprehensions
bad:  result = []; for x in data: result.append(f(x))
good: result = [f(x) for x in data]

# Avoid repeated string concatenation
bad:  s = ""; for x in data: s += str(x)
good: s = "".join(str(x) for x in data)

# Use enumerate instead of range(len())
bad:  for i in range(len(data)): item = data[i]
good: for i, item in enumerate(data):

# Cache function calls
from functools import lru_cache
@lru_cache(maxsize=None)
def expensive_function(n): ...
```
