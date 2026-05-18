---
name: math-solving-strategies
description: >-
  Reference catalog of proof methods, problem-solving tactics, and domain techniques for competition math (induction, contradiction, extremal principle, invariants, etc.).
  WHEN: only load when strategic guidance is needed, such as pre-planning analysis of a new problem, selecting applicable methods, deadlock mediation, calibration failure diagnosis, choosing a fundamentally different approach after repeated failure.
  DO NOT USE WHEN: reasoning is executing normally, writing proofs, or running code.
user-invocable: false
---

# Mathematical Problem Solving Strategies

This skill contains general strategies and methods for solving mathematical problems.
Use it for strategic analysis, planning guidance, and method selection.

---

## Proof Methods

### Mathematical Induction
**Description:** Prove base case, assume for n, prove for n+1

**When useful:**
- Statements involving "for all n ≥ k"
- Recursive sequences or definitions
- Divisibility properties
- Inequalities that grow with n

**Variants:**
- Simple induction
- Strong induction (assume for all k ≤ n)
- Backwards induction
- Transfinite induction

### Proof by Contradiction
**Description:** Assume the opposite, derive a contradiction

**When useful:**
- Proving something is impossible
- Proving uniqueness
- Proving irrationality
- "There is no..." statements

### Proof by Contrapositive
**Description:** Instead of P → Q, prove ¬Q → ¬P

**When useful:**
- When direct proof is awkward
- Statements with "if... then..."
- When negation simplifies the problem

### Extremal Principle
**Description:** Consider minimal/maximal element with the property

**When useful:**
- Existence proofs
- Optimization problems
- When structure has ordering

### Invariants and Monovariants
**Description:** Quantity that never changes (invariant) or only changes one direction (monovariant)

**When useful:**
- Process/game problems
- "Can we reach state B from state A?"
- Coloring and tiling problems

### Double Counting
**Description:** Count the same quantity two different ways

**When useful:**
- Combinatorial identities
- Graph theory (counting edges)
- Proving equalities

### Constructive Proof
**Description:** Explicitly construct the object/algorithm

**When useful:**
- Existence proofs
- "Find an example..." problems
- Algorithm design

---

## Problem-Solving Tactics

### Try Small Cases / Exploratory Analysis
**Description:** Compute specific cases to find patterns and build intuition

**When useful:**
- Almost always useful in combinatorics
- Finding the answer before proving

**Tips:**
- Go far enough to see the pattern. Be aware of small-case anomalies
- When the search space becomes too large, avoid random sampling; instead, **observe structural properties from smaller cases** to guide exploration (sampling with heuristics, not pure randomness!)
- If problem specifies n with special property (perfect square, prime, etc.), try numbers with similar property. Also try to explore only cases that share relevant properties with the target n.

### Special Number Analysis
**Description:** Competition problems often choose specific numbers because their properties are relevant to the solution. When problem specifies a particular number, analyze its properties

**For any number n appearing in the problem, check:**
1. Is it a perfect square?
2. Is it a perfect cube or higher power?
3. Is it prime or has special prime factorization?
4. Is it Powers of 2?
5. Can it be expressed as a sum? (e.g., 2016 = 1+2+...+63)

### Work Backwards
**Description:** Start from the goal and work towards given conditions

**When useful:**
- Construction problems
- When end state is well-defined
- "Reach configuration X" problems

### Symmetry
**Description:** Exploit symmetry to simplify or count

**When useful:**
- Geometric problems
- Counting problems with symmetric objects
- Function equations

### Greedy Algorithm Analysis
**Description:** Consider what greedy approach gives, then adjust

**When useful:**
- Optimization problems
- Game theory
- Resource allocation

### Case Analysis
**Description:** Split into exhaustive cases

**When useful:**
- When problem has natural divisions
- Parity arguments (odd/even)
- Modular arithmetic

### Generalize then Specialize
**Description:** Prove a more general statement, then apply to specific case

**When useful:**
- When specific case is awkward
- Induction-friendly reformulation

### Add Auxiliary Elements
**Description:** Add points, lines, variables to reveal structure

**When useful:**
- Geometry problems
- Algebraic manipulation
- Creating useful equations

### Construction Search
**Description:** When asked for a sharp / largest / smallest / optimal value, the *correct* answer is determined by the most extreme valid construction. Searching only one family (e.g. "the obvious linear / chain / repeating one / identity") can lock in a wrong target. Before committing to a value, try construction families *orthogonal* to the first one you found.

**Generic axes to vary:**
- **Dimension / density**: 1-D chain → 2-D filling → 3-D / layered. Denser packings often shift the extremal ratio sharply. For symmetric properties, also consider from 1D (left/right) to 2D (North/East/South/West) and even higher dimensions.
- **Recursive / fractal / self-similar**: iterated substitution rules; their limits frequently exceed any constant from a fixed family.
- **Asymmetric / symmetry-broken**: non-symmetric extremals can beat symmetric ones in combinatorial problems.
- **Witness set**: construct a set that must satisfy some property. Try elements adjacent to special objects.
- **Boundary / degenerate parameters**: drive a free parameter to 0, infinity, or a critical threshold; "interior" intuition can fail there.
- **Concatenation / gluing**: combining two valid objects can produce a ratio neither piece achieves alone.
- **Random / probabilistic**: averaging over random samples may reveal that the typical case differs from the obvious extremal candidate.

**When useful:**
- Sharp inequality / tight bound problems
- "Largest k such that ..." / "smallest n such that ..."
- Whenever your first candidate construction is "obvious" — that is exactly when a different family is most likely to beat it
- After repeated proof failure on a sharp claim — strongly consider the claim itself is wrong before patching the proof again

**Discipline:** A construction is only an extremal *candidate* until it has been stress-tested against at least one orthogonal family. Tag a one-sided value as `[Conjecture]`, never `[Lemma]`, until both directions are proved.

---

## Number Theory

### Modular Arithmetic
**Description:** Work modulo small primes or relevant moduli

**Tips:**
- Try mod 2 (parity), mod 3, mod 4, mod small primes
- Chinese Remainder Theorem for composite moduli
- Fermat's Little Theorem for prime moduli

### Prime Factorization
**Description:** Factor numbers and work with exponents

**Tips:**
- Unique factorization is powerful
- Count prime factors (with/without multiplicity)
- Legendre's formula for factorials

### Divisibility Analysis
**Description:** Study what divides what

**Tips:**
- gcd and lcm properties
- Euclidean algorithm
- Bézout's identity

### Diophantine Equations
**Description:** Integer solutions to polynomial equations

**Tips:**
- Factorization methods
- Descent arguments
- Modular constraints

---

## Combinatorics

### Bijection
**Description:** Establish 1-1 correspondence with easier-to-count set

**When useful:**
- Counting problems
- Proving two quantities equal

### Generating Functions
**Description:** Encode sequence as power series

**When useful:**
- Recurrence relations
- Counting with constraints
- Partition problems

### Inclusion-Exclusion
**Description:** |A ∪ B| = |A| + |B| - |A ∩ B|, generalized

**When useful:**
- Counting with "or" conditions
- Derangements, surjections

### Recursion
**Description:** Express answer for n in terms of smaller cases

**When useful:**
- Almost all counting problems
- Dynamic programming

### Graph Theory Translation
**Description:** Model problem as a graph

**When useful:**
- Relationship problems
- Matching, coloring
- Connectivity questions

### Pigeonhole Principle
**Description:** n+1 objects in n boxes → some box has ≥2

**When useful:**
- "At least one..." statements
- Existence proofs
- Coloring/assignment problems
- Infinite sequences with finite states

**Variants:**
- Simple pigeonhole
- Generalized (kn+1 objects, n boxes → some has ≥k+1)
- Infinite pigeonhole

---

## Algebra

### Polynomial Analysis
**Description:** Roots, coefficients, factorization

**Tips:**
- Vieta's formulas
- Symmetric polynomials
- Rational root theorem

### Inequalities
**Description:** AM-GM, Cauchy-Schwarz, Jensen, etc.

**Tips:**
- AM-GM for products/sums
- Cauchy-Schwarz for inner products
- Jensen for convex/concave functions
- Rearrangement inequality

### Functional Equations
**Description:** Find all functions satisfying given condition

**Tips:**
- Try special values (0, 1, -x)
- Injectivity/surjectivity
- Continuity assumptions

---

## Common Pitfalls

| Pitfall | Context |
|---------|---------|
| Off-by-One Errors | Counting, indexing, boundaries |
| Missing Edge Cases | n=0, n=1, empty set, single element |
| Assuming More Than Given | Integers vs reals, positive vs non-negative |
| Circular Reasoning | Using what you're trying to prove |
| Incomplete Case Analysis | Forgetting cases in exhaustive proofs |
| Ignoring Constraints | Bounds, divisibility requirements |

---

## Conjecturing Discipline

### Avoid Premature Pattern Extrapolation
**Description:** Multiple formulas can fit a small number of cases equally well, then diverge dramatically at larger values.

**When to be careful:**
- The conjecture was derived from fewer than 8-10 data points
- The problem involves a number with special structure
- Different candidate formulas (e.g., linear vs sublinear) both fit small cases

**Tips:**
- Always test at least two plausible candidate formulas
- Use constructions (not just exhaustive search) to verify larger cases
- If small-case formulas seem too simple, seek structural insight from the problem
- Exploit special structure in problem parameters (factorizations, divisibility, geometric properties)
- Verify both construction AND lower bound before committing to a conjecture
