"""
Reasoner agent prompts.
"""

from __future__ import annotations

from typing import Any

from src.config import CONFIG
from src.agent_runner import _call_agent

# ============================================================
# Shared prompt fragments
# ============================================================

_CHALLENGE_RESPONSE_INSTRUCTIONS = """Address their concerns:
1. If you made an error, acknowledge it
2. If you stand by your reasoning, provide stronger evidence
3. Use code verification where possible (long-running code is OK if necessary, but always set a timeout in your code)
4. If the issue is from a PREVIOUS step, say so explicitly
5. **CRITICAL: After any code execution, IMMEDIATELY restate 1) the code script name and location 2) key results in plain text**

Note: If previous steps involved code execution results marked as `[verified]`, do not re-run them for verification. Instead, use the results provided to derive your conclusions. This ensures efficiency and consistency in the reasoning process."""


def _build_meta_retry_prompt(
    meta_task: str,
    *,
    resume: bool,
    problem_summary: str | None = None,
    step_number: int | None = None,
) -> str:
    """Build a Reasoner retry prompt that delivers Meta-Strategist guidance.

    Used at the three timeout-retry call sites (step execution resume,
    step execution fresh, challenge-loop debate).

    Args:
        meta_task: Guidance text from Meta-Strategist.
        resume: True for resuming an existing reasoner session (terse), False
            for a fresh session (includes a short problem summary if given).
        problem_summary: Optional short problem context (used only when
            resume=False).
        step_number: Optional current step number for the header.
    """
    step_tag = f" Step {step_number}" if step_number is not None else ""
    header = (
        f"## MANDATORY RETRY{step_tag}: PREVIOUS ATTEMPT TIMED OUT"
    )
    body = f"""{header}

Your previous attempt timed out. You MUST change your approach — do not
repeat the same brute-force computation that just failed.

## MANDATORY GUIDANCE FROM META-STRATEGIST
---
{meta_task}
---

## MUST NOT
- Continue the same brute-force / exhaustive-search approach.
- Repeat any computation that already timed out.

## MUST
- Follow the Meta-Strategist guidance above.
- Prefer mathematical reasoning over computation.
- If you do run code, keep it bounded and set an explicit timeout."""
    if not resume and problem_summary:
        body += f"\n\n## PROBLEM CONTEXT\n---\n{problem_summary}\n---"
    body += "\n\nProceed now with a different, simpler approach."
    return body


# ============================================================
# Reasoner Functions
# ============================================================

def reasoner_create_plan(
    problem: str,
    previous_plans: list[str] | None = None,
    meta_suggestions: str | None = None,
    transcript_path: str | None = None,
    problem_id: str | None = None,  # Enable hook injection.
    session_id: str | None = None,  # Deterministic session name shared with later step/solution calls.
) -> dict[str, Any]:
    """
    Ask Reasoner to create a step-by-step plan.
    
    Args:
        problem: The problem statement
        previous_plans: List of previously failed plan approaches to avoid
        meta_suggestions: Strategic suggestions from Meta-Strategist
        transcript_path: JSONL transcript tee path
    """
    previous_context = ""
    if previous_plans:
        previous_context = f"""
## IMPORTANT: Previous Approaches That Failed

The following approaches have been tried and FAILED. You MUST use a SIGNIFICANTLY DIFFERENT approach.

{chr(10).join(f'### Failed Plan {i+1}:{chr(10)}{p[:1500]}...' for i, p in enumerate(previous_plans))}

---

DO NOT repeat these approaches. Try a fundamentally different strategy.
"""
    
    meta_context = ""
    if meta_suggestions:
        meta_context = f"""
## Strategic Suggestions from Meta-Strategist

The Meta-Strategist has analyzed this problem and provides the following guidance.
**You should carefully consider these suggestions** when creating your plan.

---
{meta_suggestions}
---

Make sure your plan addresses the relevant suggestions above.
"""
    
    prompt = f"""Create a step-by-step plan to solve this problem.

PROBLEM:
---
{problem}
---
{meta_context}{previous_context}
## CRITICAL: READ THE PROBLEM CAREFULLY

Before planning, verify you understand EVERY condition:
1. **Read each condition word-by-word** - subtle differences matter!
2. **Identify ALL constraints** - don't miss hidden conditions
3. **Note what is NOT specified** - avoid adding assumptions
4. If the problem has multiple interpretations, address the ambiguity explicitly

Requirements:
1. Break down into 3-10 clear steps
2. Each step should be independently verifiable
3. Number your steps clearly (Step 1, Step 2, etc.)
4. Consider the Meta-Strategist's suggestions if provided

Write your plan now."""
    
    # Context summary for timeout retry
    context_summary = f"""PLANNING PHASE for problem:
{problem[:500]}...

TASK: Create step-by-step plan (3-10 steps, numbered)."""
    
    result = _call_agent(
        prompt=prompt,
        agent="reason",
        model=CONFIG.REASONER_MODEL,
        timeout=CONFIG.REASONER_TIMEOUT,
        transcript_path=transcript_path,
        output_label="Reasoner",
        context_summary=context_summary,  # Prevent context loss on retry
        reasoning_effort=CONFIG.REASONER_EFFORT,
        problem_id=problem_id,  # Enable hook injection.
        session_id=session_id,  # Shared reasoner session.
    )

    # Plans are sometimes followed by a short trailing chatter turn (e.g. a
    # "Noted." reply). The default `response` is only the FINAL assistant
    # message, which can drop the actual numbered plan. Join all assistant
    # messages so the full plan reaches `_check_plan` and `plan.md`.
    if result.get("success"):
        msgs = result.get("all_assistant_messages") or []
        if len(msgs) > 1:
            result["response"] = "\n\n".join(m for m in msgs if m and m.strip())
    return result


def reasoner_execute_step(
    problem: str,
    plan: str,
    completed_steps: str,
    step_number: int,
    step_title: str,
    code_dir: str | None = None,
    session_id: str | None = None,  # Now a deterministic session NAME (was UUID).
    transcript_path: str | None = None,
    use_pure_reasoning: bool = False,
    name: str | None = None,  # Deterministic name for spawning a NEW session.
) -> dict[str, Any]:
    """
    Ask Reasoner to execute a specific step.
    """
    code_instruction = ""
    if code_dir:
        code_instruction = f"""
CODE SAVING:
- Save all verification Python scripts to: {code_dir}/
- Use descriptive filenames: step{step_number:02d}_*.py (e.g., step01_verify_bound.py)
"""
    
    if session_id:
        # Continuing conversation - shorter prompt
        prompt = f"""Now execute Step {step_number}: {step_title}

Remember to use verification tags: [verified], [easy-verify], [hard-verify]
**CRITICAL: After running any code, IMMEDIATELY restate 1) the code script name and location 2) key results in plain text to preserve them.**

Note: If previous steps involved code execution results marked as `[verified]`, do not re-run them for verification. Instead, use the results provided to derive your conclusions. This ensures efficiency and consistency in the reasoning process.
{code_instruction}"""
    else:
        # New conversation - full context
        prompt = f"""Execute Step {step_number} of the solution plan.

PROBLEM:
---
{problem}
---

PLAN:
---
{plan}
---

COMPLETED STEPS:
---
{completed_steps if completed_steps else "(none yet)"}
---

CURRENT TASK: Execute **Step {step_number}: {step_title}**

Requirements:
1. Show detailed reasoning
2. Tag ALL conclusions with: [verified], [easy-verify], or [hard-verify]
3. For [verified]: include actual code and output
4. For [easy-verify]: include code that can verify it
5. For [hard-verify]: provide thorough logical argument

**CRITICAL: After running any code, IMMEDIATELY restate the  1) the code script name and location 2) key results in plain text.**
- This ensures results are preserved even if you timeout later.

Note: If previous steps involved code execution results marked as `[verified]`, do not re-run them for verification. Instead, use the results provided to derive your conclusions. This ensures efficiency and consistency in the reasoning process.
{code_instruction}
Write your Step {step_number} report now."""
    
    # Build context summary for timeout retry
    context_summary = f"""PROBLEM: {problem}...

TASK: Step {step_number}: {step_title}"""
    
    # Select agent based on pure reasoning mode
    agent = "reason-noncoding" if use_pure_reasoning else "reason"
    
    return _call_agent(
        prompt=prompt,
        agent=agent,
        model=CONFIG.REASONER_MODEL,
        timeout=CONFIG.REASONER_TIMEOUT,
        session_id=session_id,
        name=name,
        transcript_path=transcript_path,
        output_label="Reasoner" if not use_pure_reasoning else "Reasoner (non-coding)",
        context_summary=context_summary,  # Prevent context loss on retry
        reasoning_effort=CONFIG.REASONER_EFFORT,
        # Note: Long timeouts (>= 300s) are NOT retried internally - Orchestrator handles with Meta-Strategist
    )


def reasoner_continue_with_code_guidance(
    guidance: str,
    retry_count: int,
    max_retries: int,
    problem: str,
    current_step: int,
    step_title: str,
    step_context: str,
    session_id: str | None = None,
    transcript_path: str | None = None,
    use_pure_reasoning: bool = False,
) -> dict[str, Any]:
    """
    Continue Reasoner's session with guidance from Meta-Strategist about code issues.
    
    This is used when code times out or errors, allowing Reasoner to try again
    with strategic advice while maintaining conversation context.
    
    Session continuity: session_id is always set at the call site, so the
    problem statement and step context already live in the session
    transcript and the injected `[Live problem state]` block. The prompt
    here only delivers the Meta-Strategist's new guidance.
    """
    prompt = f"""Your code encountered an issue on Step {current_step} - {step_title}.

META-STRATEGIST GUIDANCE:
---
{guidance}
---

Retry attempt: {retry_count} / {max_retries}

Please:
1. Consider the guidance above
2. Adjust your approach accordingly
3. Try again with the suggested modifications
4. Remember your goal for Step {current_step}

Note: If previous steps involved code execution results marked as `[verified]`, do not re-run them for verification. Instead, use the results provided to derive your conclusions. This ensures efficiency and consistency in the reasoning process.

Continue now."""
    
    # Select agent based on pure reasoning mode
    agent = "reason-noncoding" if use_pure_reasoning else "reason"
    
    return _call_agent(
        prompt=prompt,
        agent=agent,
        model=CONFIG.REASONER_MODEL,
        timeout=CONFIG.REASONER_TIMEOUT,
        session_id=session_id,
        transcript_path=transcript_path,
        output_label="Reasoner" if not use_pure_reasoning else "Reasoner (non-coding)",
        reasoning_effort=CONFIG.REASONER_EFFORT,
    )


def reasoner_respond_challenge(
    challenge: str,
    session_id: str | None = None,  # Now a deterministic session NAME (was UUID).
    transcript_path: str | None = None,
    # Context parameters for new session (when session_id is None or lost)
    problem: str | None = None,
    plan: str | None = None,
    completed_steps: str | None = None,
    step_number: int | None = None,
    step_title: str | None = None,
    code_dir: str | None = None,
    use_pure_reasoning: bool = False,
    name: str | None = None,  # Deterministic name for spawning a NEW session.
) -> dict[str, Any]:
    """
    Ask Reasoner to respond to Verifier's challenge.
    
    If session_id is None (new session), full context is included.
    If session_id is provided (continuing session), only the challenge is sent.
    """
    code_instruction = ""
    if code_dir:
        code_instruction = f"""

CODE SAVING:
- Save all verification Python scripts to: {code_dir}/
"""

    if session_id:
        # Continuing conversation - shorter prompt
        prompt = f"""The Verifier challenges your reasoning:

---
{challenge}
---

{_CHALLENGE_RESPONSE_INSTRUCTIONS}

Respond now."""
    else:
        # New conversation - full context needed
        prompt = f"""The Verifier challenges your reasoning. Here is the full context:

PROBLEM:
---
{problem if problem else "(not provided)"}
---

PLAN:
---
{plan if plan else "(not provided)"}
---

COMPLETED STEPS:
---
{completed_steps if completed_steps else "(none yet)"}
---

CURRENT STEP: Step {step_number}: {step_title}

VERIFIER'S CHALLENGE:
---
{challenge}
---
{code_instruction}
{_CHALLENGE_RESPONSE_INSTRUCTIONS}

**IMPORTANT**: After completing your analysis, you MUST provide a clear written report/proof. Do not end your response with only file reads - synthesize your findings into a coherent mathematical argument.

Respond now."""

    # Select agent based on pure reasoning mode
    agent = "reason-noncoding" if use_pure_reasoning else "reason"
    
    return _call_agent(
        prompt=prompt,
        agent=agent,
        model=CONFIG.REASONER_MODEL,
        timeout=CONFIG.REASONER_TIMEOUT,
        session_id=session_id,
        name=name,
        transcript_path=transcript_path,
        output_label="Reasoner",
        reasoning_effort=CONFIG.REASONER_EFFORT,
    )


def reasoner_write_solution(
    problem: str | None = None,
    completed_steps: str | None = None,
    problem_id: str | None = None,
    session_id: str | None = None,
    transcript_path: str | None = None,
) -> dict[str, Any]:
    """Ask Reasoner to write the final solution.

    Joins the shared reasoner session and also pastes the problem statement
    and verified-results context into the prompt as a hard fallback against
    context loss (hook failures, transcript truncation, model not retaining
    earlier turns).
    """
    problem_block = f"""## Problem (verbatim)

The problem you are solving — and have already verified every step of in
this session — is:

---
{problem}
---""" if problem else ""

    completed_block = f"""## Verified Results from your previous step reports (this session)

The orchestrator has accumulated the verified results below from each
step's verifier-accepted Verified Results block. Use these as the
authoritative content for your final solution. The `[Answer]` entry
(if any) is the answer your solution must reach.

---
{completed_steps}
---""" if completed_steps and completed_steps.strip() else ""

    prompt = f"""# TASK: Write the final solution for the CURRENT problem

You have just finished verifying every step of the current problem in this
same session. The problem statement, plan, and verified results are
available to you in **three** ways (any one is sufficient):

1. The injected `[Live problem state]` block at the top of this
   message (read its `## Problem Statement` and `## Verified Results`
   sections).
2. Your own session transcript above — every Step N report and the
   verifier's accepted Verified Results blocks.
3. The verbatim copies pasted directly into this prompt (see below).

If sources (1) and (2) are unclear or appear empty, **trust source (3)
and proceed**. Do not stop and ask for context; the prompt is
self-contained.

{problem_block}

{completed_block}

## Strict Rules

1. **Do not browse OTHER problems' `scratch/` directories.** Do not
   search the repo at large with `find`, `ls`, or `grep`. Stick to
   writing your own `solution.md` and reading the injected state.
   Cross-problem contamination is a hard error.
2. **Do NOT read any other `scratch/<id>/` directory.** You are working
   on exactly one problem.
3. **Do NOT re-solve.** All steps are done; integrate them from the
   sources above.
4. **Do NOT execute new code.** All computations are verified.
5. **Do NOT preamble.** Start directly with math content (e.g.
   "Let \\(x = \\dots\\)" or "We claim that \\(\\dots\\)").
6. **Do NOT refuse.** If you genuinely cannot find content in sources
   (1) or (2), use source (3) above. The prompt always contains the
   problem statement and the verified results.

## Format

- Combine the verified results into a coherent proof / answer for the
  problem stated above.
- Strip ALL verification tags (`[verified]` / `[easy-verify]` / `[hard-verify]`).
- Strip internal notes, debug output, and code blocks.
- Rigorous but readable, as for a competition submission.
- End with `\\boxed{{answer}}` matching the `[Answer]` entry in the
  verified results — if your boxed value disagrees, you are solving the
  wrong problem; restart.

## Sanity check before submitting

Before finalizing, re-read the first sentence of your solution and
confirm it is about the same problem stated in `## Problem (verbatim)`
above. If your solution is about a different topic (different geometry,
different objects, different answer), discard it and start over using
the verified results pasted in this prompt.

Write the final solution now."""

    return _call_agent(
        prompt=prompt,
        agent="reason",
        model=CONFIG.REASONER_MODEL,
        timeout=CONFIG.REASONER_TIMEOUT,
        session_id=session_id,
        transcript_path=transcript_path,
        output_label="Reasoner",
        reasoning_effort=CONFIG.REASONER_EFFORT,
        problem_id=problem_id,
    )


def reasoner_execute_meta_task(
    task: str,
    problem: str,
    plan: str,
    completed_steps: str,
    step_number: int,
    step_title: str,
    execution_hints: str | None = None,
    code_dir: str | None = None,
    session_id: str | None = None,
    transcript_path: str | None = None,
    agent: str = "reason",
) -> dict[str, Any]:
    """
    Execute a specific task assigned by Meta-Strategist.
    
    This is used when Meta-Strategist diagnoses a problem (calibration failure,
    timeout, etc.) and prescribes a specific action. The Reasoner MUST execute
    this task - it is not a suggestion but a directive.
    
    The task is framed as mandatory to ensure Reasoner compliance.
    
    Args:
        agent: Agent to use - "reason" for full capabilities, "reason-noncoding" for pure math mode
    """
    code_instruction = ""
    if code_dir:
        code_instruction = f"""

CODE SAVING:
- Save all Python scripts to: {code_dir}/
- Use descriptive filenames: step{step_number:02d}_meta_task_*.py
"""
    
    hints_section = ""
    if execution_hints:
        hints_section = f"""
## EXECUTION HINTS (from Meta-Strategist)
{execution_hints}
"""
    
    noncoding_notice = ""
    if agent == "reason-noncoding":
        noncoding_notice = """
---
** NON-CODING MODE ACTIVE **
You are now in PURE REASONING mode with NO access to code execution or terminal tools.
You can ONLY read files and write mathematical proofs.
Focus on theoretical arguments, algebraic manipulation, and logical deduction.
---
"""

    prompt = f"""## MANDATORY TASK FROM META-STRATEGIST

You MUST execute the following task. This is not a suggestion - it is a required action based on strategic analysis.

---

## TASK (You MUST complete this)
{task}

---
{hints_section}
## CONTEXT

PROBLEM:
---
{problem}
---

PLAN:
---
{plan}
---

COMPLETED STEPS:
---
{completed_steps if completed_steps else "(none yet)"}
---

CURRENT STEP: Step {step_number} - {step_title}

## CODE EXECUTION GUIDELINES

- Long-running code is acceptable (up to 600 seconds) if necessary
- ALWAYS set a timeout in your code to prevent infinite execution
- If optimization is possible, optimize BEFORE running
- Include progress output for long computations
- **CRITICAL: After EACH code execution, IMMEDIATELY restate  1) the code script name and location 2) key results in plain text**
  - This ensures results survive even if you timeout later
- Note: If previous steps involved code execution results marked as `[verified]`, do not re-run them for verification. Instead, use the results provided to derive your conclusions. This ensures efficiency and consistency in the reasoning process.

{code_instruction}{noncoding_notice}
## YOUR RESPONSE

1. Execute the SPECIFIC TASK above
2. Report your findings clearly
3. If the task reveals new information, update your conclusions
4. Tag conclusions: [verified], [easy-verify], or [hard-verify]

Begin now."""
    
    # Determine output label based on agent type
    output_label = "Reasoner (non-coding)" if agent == "reason-noncoding" else "Reasoner"
    
    return _call_agent(
        prompt=prompt,
        agent=agent,
        model=CONFIG.REASONER_MODEL,
        timeout=CONFIG.REASONER_TIMEOUT,
        session_id=session_id,
        transcript_path=transcript_path,
        output_label=output_label,
        reasoning_effort=CONFIG.REASONER_EFFORT,
    )


# ============================================================
# Pre-plan Exploration
# ============================================================

def reasoner_explore(
    *,
    problem: str,
    attempt: int,
    budget_min: int,
    code_dir: str | None = None,
    problem_id: str | None = None,
    session_id: str | None = None,
    name: str | None = None,
    transcript_path: str | None = None,
) -> dict:
    """Run a Reasoner exploration round.

    Reasoner is asked to follow the `exploration-protocol` skill and emit
    `scratch/<problem_id>/exploration.md` with the required sections.
    Each call uses a *fresh* explore session (one per round), so prev-round
    findings should already be in the injected `[Live problem state]`
    via `## Exploration Findings`.
    """
    code_block = ""
    if code_dir:
        code_block = (
            f"\nCODE SAVING: save any verification scripts to `{code_dir}/`"
            f" with prefix `explore_*.py` (e.g. `explore_small_cases.py`)."
        )

    prompt = f"""You are in the **Exploration phase** for this problem (round {attempt}).

PROBLEM:
---
{problem}
---

[Phase: exploration / Budget: {budget_min} min remaining for this round]

Follow the **`exploration-protocol`** skill exactly. Probe the problem
structurally, run small cases when useful, identify candidate approaches,
and **write your deliverable to `scratch/<problem_id>/exploration.md`**
with the required sections (Tried / Worked / Failed / Candidate Approaches
/ Assessment / Solution Sketch).

If the injected `[Live problem state]` already contains
`## Exploration Findings` from a previous round, **build on those**: do
not redo verified observations, and pursue different candidate approaches
than the previous round suggested.{code_block}

Begin exploration now. End by writing the deliverable file."""

    return _call_agent(
        prompt=prompt,
        agent="reason",
        model=CONFIG.REASONER_MODEL,
        timeout=CONFIG.REASONER_TIMEOUT,
        session_id=session_id,
        name=name,
        transcript_path=transcript_path,
        output_label=f"Reasoner (explore r{attempt})",
        reasoning_effort=CONFIG.REASONER_EFFORT,
        problem_id=problem_id,
    )


def reasoner_polish_exploration_solution(
    *,
    problem: str | None = None,
    solution_sketch: str | None = None,
    problem_id: str | None = None,
    session_id: str | None = None,
    transcript_path: str | None = None,
) -> dict:
    """Ask the explore session to polish its SOLVED sketch into final solution.

    Continues the same explore session that produced the SOLVED assessment.
    The deliverable + injected PROBLEM_STATE are already in context, but
    we also paste the problem statement and solution sketch directly into
    the prompt as a hard fallback against context loss.
    """
    problem_block = f"""## Problem (verbatim)

---
{problem}
---""" if problem else ""

    sketch_block = f"""## Your verified solution sketch (from this session's exploration deliverable)

---
{solution_sketch}
---""" if solution_sketch and solution_sketch.strip() else ""

    prompt = f"""You produced an exploration deliverable with `Assessment: SOLVED` and a
Solution Sketch in this session. Now write the polished competition-style
**final solution** to `scratch/<problem_id>/solution.md`.

The problem statement, your exploration deliverable, and the solution
sketch are all available to you in three ways: (1) the injected
`[Live problem state]` block, (2) your session transcript, and
(3) the verbatim copies pasted below.

If sources (1) and (2) appear unclear or empty, **trust source (3) and
proceed**. Do not stop and ask for context.

{problem_block}

{sketch_block}

Rules:
1. Build directly on the verified Solution Sketch and verified observations.
2. Strip exploration scaffolding ("Tried", "Failed conjectures", etc.) —
   the final solution is a clean argument, not an exploration log.
3. Strip verification tags (`[verified]` / `[easy-verify]` / `[hard-verify]`).
4. **Do not browse OTHER problems' `scratch/` directories.** Do not
   search the repo at large. Stick to writing your own `solution.md`
   and reading the injected state. The current problem's content is in
   this prompt.
5. **Do NOT solve a different problem.** End with `\\boxed{{answer}}` that
   matches the answer in your solution sketch above.

Write the file now."""

    return _call_agent(
        prompt=prompt,
        agent="reason",
        model=CONFIG.REASONER_MODEL,
        timeout=CONFIG.REASONER_TIMEOUT,
        session_id=session_id,
        transcript_path=transcript_path,
        output_label="Reasoner (polish exploration solution)",
        reasoning_effort=CONFIG.REASONER_EFFORT,
        problem_id=problem_id,
    )
