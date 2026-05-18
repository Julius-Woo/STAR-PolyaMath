"""
Meta-Strategist agent prompts.

Protocol details (output formats, critical rules, per-scenario guidance) live in the `meta-intervention-protocol` skill and are loaded automatically by the agent.
This module only builds the dynamic context (problem, steps, failure details) and invokes the Meta-Strategist agent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.config import CONFIG
from src.agent_runner import _call_agent

# ============================================================
# Meta-Strategist Functions
# ============================================================

# Skill references — mentioned explicitly in prompts to guarantee loading.
PROTOCOL_SKILL_REF = "Follow the `meta-intervention-protocol` skill for output format and rules."
MATH_SKILL_REF = "Use the `math-solving-strategies` skill for proof methods, tactics, and domain-specific techniques."
CODE_SKILL_REF = "Use the `code-issue-resolution` skill for timeout strategies, error diagnosis, and optimization tips."


def meta_strategist_analyze_problem(
    problem: str,
    session_id: str | None = None,
    transcript_path: str | None = None,
) -> dict[str, Any]:
    """
    Ask Meta-Strategist to analyze problem before planning.
    
    This is Intervention Point 1: Pre-Planning Analysis.
    Meta-Strategist selects applicable methods from the pool.
    """
    prompt = f"""Select applicable methods for this problem.

PROBLEM:
---
{problem}
---

{PROTOCOL_SKILL_REF} (section: Pre-Planning Analysis Protocol)
{MATH_SKILL_REF}

Output now:"""
    
    return _call_agent(
        prompt=prompt,
        agent="meta-strategist",
        model=CONFIG.META_STRATEGIST_MODEL,
        timeout=CONFIG.META_STRATEGIST_TIMEOUT,
        session_id=session_id,
        transcript_path=transcript_path,
        output_label="Meta-Strategist",
        reasoning_effort=CONFIG.META_STRATEGIST_EFFORT,
    )




def meta_strategist_diagnose_and_guide(
    problem: str,
    plan: str,
    completed_steps: str,
    current_step_number: int,
    current_step_title: str,
    current_report: str,
    failure_type: str,  # "timeout" | "code_issue" | "stuck"
    failure_context: str,  # Details about what went wrong
    intervention_history: list[dict] | None = None,
    base_dir: str | None = None,  # Working directory for this problem
    session_id: str | None = None,
    transcript_path: str | None = None,
) -> dict[str, Any]:
    """
    Meta-Strategist diagnoses failure and provides actionable guidance.
    
    This is the unified intervention point for calibration failures, timeouts, and other stuck situations. Meta-Strategist analyzes the situation and provides a SPECIFIC TASK for Reasoner to execute.
    
    Uses persistent session to maintain context across interventions.
    - If session_id exists: Meta-Strategist already has context from previous calls
    - If session_id is None: Include intervention_history to provide context
    
    Returns:
        dict with keys:
        - success: bool
        - response: str (full response)
        - action_type: str (parsed from response)
        - specific_task: str (parsed from response)
        - session_id: str (for session persistence)
    """
    # Build intervention history context ONLY if no session_id
    # (session already contains the full conversation history)
    history_section = ""
    if not session_id and intervention_history:
        history_lines = []
        for i, h in enumerate(intervention_history[-3:], 1):  # Last 3 interventions
            history_lines.append(f"**Intervention {i}** ({h.get('type', 'unknown')}):")
            history_lines.append(f"  - Suggested: {h.get('task', 'N/A')[:200]}")
            history_lines.append(f"  - Outcome: {h.get('outcome', 'N/A')[:100]}")
        history_section = f"""
## Previous Interventions (Your Past Advice)
{chr(10).join(history_lines)}

**IMPORTANT**: If your previous advice was not followed or didn't work, try a DIFFERENT approach.
"""
    
    # Failure-specific context
    failure_intro = {
        "timeout": "The Reasoner **TIMED OUT** while executing this step. The computation took too long.",
        "code_issue": "The Reasoner's code encountered an **ERROR** (syntax error, runtime error, or wrong output).",
        "stuck": "The Reasoner is **STUCK** and making no progress on this step.",
    }.get(failure_type, f"The Reasoner encountered a problem: {failure_type}")

    # Skill references replace the old inline output_format and critical_rules
    skill_refs = f"""{PROTOCOL_SKILL_REF} (section: Unified Diagnose & Guide Protocol + Critical Rules)
{MATH_SKILL_REF}
{CODE_SKILL_REF}"""

    if session_id:
        # Continuing session - Meta-Strategist already knows problem, plan, etc.
        prompt = f"""## NEW FAILURE on Step {current_step_number} - {current_step_title}

**{failure_intro}**

## PROBLEM
---
{problem}
---

### Reasoner's Report (before failure):
---
{current_report}
---

### Failure Details:
---
{failure_context}
---

## YOUR TASK

Diagnose and provide a **SPECIFIC, ACTIONABLE TASK** for the Reasoner.

{skill_refs}"""
    else:
        # New session - need full context
        prompt = f"""You are the strategic advisor for a mathematical problem-solving session.

## PROBLEM
---
{problem}
---
## SITUATION
{failure_intro}

## SOLUTION PLAN
---
{plan}
---

## COMPLETED STEPS
---
{completed_steps if completed_steps else "(none yet)"}
---

## CURRENT STEP: Step {current_step_number} - {current_step_title}

### Reasoner's Report (before failure):
---
{current_report}
---

### Failure Details:
---
{failure_context}
---
{history_section}
## YOUR TASK

Diagnose the problem and provide a **SPECIFIC, ACTIONABLE TASK** for the Reasoner.

{skill_refs}"""

    result = _call_agent(
        prompt=prompt,
        agent="meta-strategist",
        model=CONFIG.META_STRATEGIST_MODEL,
        timeout=CONFIG.META_STRATEGIST_TIMEOUT,
        session_id=session_id,
        transcript_path=transcript_path,
        output_label="Meta-Strategist",
        reasoning_effort=CONFIG.META_STRATEGIST_EFFORT,
    )
    
    if result.get("success"):
        response = result.get("response", "")
        # Parse action_type, specific_task, use_pure_reasoning, and trace_back_to from response
        action_type = _parse_action_type(response)
        specific_task = _parse_specific_task(response)
        use_pure_reasoning = _parse_use_pure_reasoning(response)
        trace_back_to = _parse_trace_back_to(response, current_step_number)
        result["action_type"] = action_type
        result["specific_task"] = specific_task
        result["use_pure_reasoning"] = use_pure_reasoning
        result["trace_back_to"] = trace_back_to
    
    return result


def _parse_action_type(response: str) -> str:
    """Extract action_type from Meta-Strategist response."""
    import re
    # Look for ACTION_TYPE section
    match = re.search(r'###\s*ACTION_TYPE\s*\n+\s*`?(\w+)`?', response, re.IGNORECASE)
    if match:
        action = match.group(1).lower().strip()
        if action in ["explore_more", "retry_different_method", "trace_back", "replan"]:
            return action
    
    # Fallback: look for keywords
    text_lower = response.lower()
    if "trace_back" in text_lower or "trace back" in text_lower:
        return "trace_back"
    if "replan" in text_lower or "adjust_plan" in text_lower:
        return "replan"
    if "explore" in text_lower or "compute" in text_lower or "more cases" in text_lower:
        return "explore_more"
    
    return "retry_different_method"  # Default


def _parse_specific_task(response: str) -> str:
    """Extract specific_task from Meta-Strategist response."""
    import re
    # Look for SPECIFIC_TASK section
    match = re.search(r'###\s*SPECIFIC_TASK\s*\n+(.*?)(?=\n###|\n## |$)', response, re.IGNORECASE | re.DOTALL)
    if match:
        task = match.group(1).strip()
        if task:
            return task
    
    # Fallback: look for recommendations section
    match = re.search(r'###\s*(?:Recommendations?|Task|Action)\s*\n+(.*?)(?=\n###|\n## |$)', response, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    
    # Last resort: return a portion of the response
    return response[-1000:] if len(response) > 1000 else response


def _parse_trace_back_to(response: str, current_step: int) -> int:
    """Extract TRACE_BACK_TO step number from Meta-Strategist response.
    
    Returns the step number to trace back to, defaulting to current_step (re-execute current step).
    """
    import re
    # Look for TRACE_BACK_TO section with a number
    match = re.search(r'###?\s*TRACE_BACK_TO\s*\n+\s*(\d+)', response, re.IGNORECASE)
    if match:
        step_num = int(match.group(1))
        if 1 <= step_num <= current_step:
            return step_num
    
    # Also check for inline format like "TRACE_BACK_TO: 2" or "trace back to step 2"
    match = re.search(r'TRACE_BACK_TO[:\s]+\s*(\d+)', response, re.IGNORECASE)
    if match:
        step_num = int(match.group(1))
        if 1 <= step_num <= current_step:
            return step_num
    
    match = re.search(r'trace\s*back\s*to\s*step\s*(\d+)', response, re.IGNORECASE)
    if match:
        step_num = int(match.group(1))
        if 1 <= step_num <= current_step:
            return step_num
    
    # Default: stay at current step (don't lose work)
    return current_step


def _parse_use_pure_reasoning(response: str) -> bool:
    """Extract USE_PURE_REASONING flag from Meta-Strategist response.
    
    Returns True if Meta-Strategist recommends using non-coding mode (no code execution).
    """
    import re
    # Look for USE_PURE_REASONING section with YES/NO
    match = re.search(r'###?\s*USE_PURE_REASONING\s*\n+\*?\*?\s*(YES|NO)\b', response, re.IGNORECASE)
    if match:
        return match.group(1).upper() == "YES"
    
    # Also check for inline format like "USE_PURE_REASONING: YES"
    match = re.search(r'USE_PURE_REASONING[:\s]+\*?\*?\s*(YES|NO)\b', response, re.IGNORECASE)
    if match:
        return match.group(1).upper() == "YES"
    
    # Fallback: check for explicit keywords suggesting pure reasoning mode
    # These must be positive statements, not negations
    lower = response.lower()
    # Require more specific patterns to avoid false positives
    if ("no code execution" in lower and "do not" not in lower) or \
       "stop all computational" in lower or \
       "pure reasoning mode active" in lower:
        return True
    
    return False



# ============================================================
# Re-plan Decision
# ============================================================

REPLAN_VERDICTS = {"APPROVE_REPLAN", "CONTINUE", "TRACE_BACK_TO", "ABORT"}


def meta_strategist_decide_replan(
    *,
    problem: str,
    proposer: str,                    # "verifier" | "reasoner" | "orchestrator"
    proposer_reason: str,             # the brief reason from the proposer
    current_step_number: int,
    failed_records_summary: str,      # rendered Confirmed Failures (already in context, but include for new sessions)
    session_id: str | None = None,
    transcript_path: str | None = None,
) -> dict:
    """Ask Meta-Strategist to authorize / reject a proposed re-plan.

    The proposer's view is advisory. Meta-Strategist's verdict is one of
    APPROVE_REPLAN / CONTINUE / TRACE_BACK_TO / ABORT, and (if approving)
    must produce FORBIDDEN_DIRECTIONS so the next plan attempt avoids the
    same dead end.

    Returns the standard `_call_agent` dict, plus parsed fields:
      - replan_decision: str (one of REPLAN_VERDICTS) or "" on parse failure
      - reason_summary: str (from REASON_SUMMARY field)
      - plan_summary: str (from PLAN_SUMMARY field, may be empty)
      - forbidden_directions: list[str]
      - reusable_results: list[str]
      - trace_back_to: int | None
    """
    from src.utils.extract import extract_meta_field, extract_meta_bullet_list

    if session_id:
        # Continuing meta session — `Confirmed Failures` already injected via
        # PROBLEM_STATE; keep prompt minimal.
        prompt = f"""## Re-plan request

A re-plan has been **proposed** by the {proposer}.

Their reason:
---
{proposer_reason or "(no reason provided)"}
---

Current step: Step {current_step_number}.

Apply the **`meta-intervention-protocol`** skill (section: Re-plan Decision Protocol).
Decide REPLAN_DECISION, write the required structured fields, and remember:
default to TRACE_BACK_TO over APPROVE_REPLAN unless the plan itself is unsound."""
    else:
        prompt = f"""You are deciding whether to authorize a re-plan for the current problem.

## PROBLEM
---
{problem}
---

## Proposer
{proposer}

## Proposer's Reason
---
{proposer_reason or "(no reason provided)"}
---

## Current Step
Step {current_step_number}

## Confirmed Failures (already-tried directions)
---
{failed_records_summary or "(none — first attempt)"}
---

Apply the **`meta-intervention-protocol`** skill (section: Re-plan Decision Protocol).
Output the required structured fields. Default to TRACE_BACK_TO over APPROVE_REPLAN
unless the plan itself is unsound."""

    result = _call_agent(
        prompt=prompt,
        agent="meta-strategist",
        model=CONFIG.META_STRATEGIST_MODEL,
        timeout=CONFIG.META_STRATEGIST_TIMEOUT,
        session_id=session_id,
        transcript_path=transcript_path,
        output_label="Meta-Strategist (replan)",
        reasoning_effort=CONFIG.META_STRATEGIST_EFFORT,
    )

    if result.get("success"):
        response = result.get("response", "") or ""
        # Parse REPLAN_DECISION robustly: take first valid token in field.
        raw = extract_meta_field(response, "REPLAN_DECISION").strip()
        decision = ""
        for tok in raw.replace("`", " ").split():
            up = tok.strip("*_:.,").upper()
            if up in REPLAN_VERDICTS:
                decision = up
                break
        result["replan_decision"] = decision
        result["reason_summary"] = extract_meta_field(response, "REASON_SUMMARY")
        result["plan_summary"] = extract_meta_field(response, "PLAN_SUMMARY")
        result["forbidden_directions"] = extract_meta_bullet_list(response, "FORBIDDEN_DIRECTIONS")
        result["reusable_results"] = extract_meta_bullet_list(response, "REUSABLE_RESULTS")
        # trace_back_to: int or None
        tb_raw = extract_meta_field(response, "TRACE_BACK_TO")
        import re as _re
        tb_m = _re.search(r"\d+", tb_raw)
        result["trace_back_to"] = int(tb_m.group(0)) if tb_m else None

    return result


# ============================================================
# Exploration Review (PARTIALLY_SOLVED -> retry vs proceed)
# ============================================================

EXPLORE_REVIEW_VERDICTS = {"RETRY_EXPLORE", "PROCEED_TO_PLAN"}


def meta_strategist_review_exploration(
    *,
    problem: str,
    findings_summary: str,
    attempt: int,
    max_rounds: int,
    session_id: str | None = None,
    transcript_path: str | None = None,
) -> dict:
    """Decide whether to retry exploration or proceed to plan.

    Called when an exploration round assessment was PARTIALLY_SOLVED.
    Verdict is one of `RETRY_EXPLORE` / `PROCEED_TO_PLAN`. The orchestrator
    forces `PROCEED_TO_PLAN` if `attempt >= max_rounds`, regardless.

    Output (parsed):
      - explore_decision: str ("" on parse failure)
      - reason_summary:   str (brief paragraph)
    """
    from src.utils.extract import extract_meta_field

    prompt = f"""## Exploration round {attempt} review

The Reasoner finished an exploration round with assessment
**PARTIALLY_SOLVED**. Decide whether to:

- `RETRY_EXPLORE`: give the Reasoner another exploration round, OR
- `PROCEED_TO_PLAN`: enter the formal step-by-step plan phase.

Constraint: at most {max_rounds} exploration rounds are allowed; this is
round {attempt}. If `attempt >= max_rounds` the decision is forced to
`PROCEED_TO_PLAN` regardless of your verdict.

PROBLEM:
---
{problem}
---

EXPLORATION FINDINGS (round {attempt}):
---
{findings_summary or "(no findings)"}
---

Output the following structured fields verbatim:

### EXPLORE_DECISION
RETRY_EXPLORE | PROCEED_TO_PLAN

### REASON_SUMMARY
A brief, single-paragraph summary of why. No bullets, no headings.

Decide now."""

    result = _call_agent(
        prompt=prompt,
        agent="meta-strategist",
        model=CONFIG.META_STRATEGIST_MODEL,
        timeout=CONFIG.META_STRATEGIST_TIMEOUT,
        session_id=session_id,
        transcript_path=transcript_path,
        output_label="Meta-Strategist (explore review)",
        reasoning_effort=CONFIG.META_STRATEGIST_EFFORT,
    )

    if result.get("success"):
        response = result.get("response", "") or ""
        raw = extract_meta_field(response, "EXPLORE_DECISION").strip()
        decision = ""
        for tok in raw.replace("`", " ").split():
            up = tok.strip("*_:.,").upper()
            if up in EXPLORE_REVIEW_VERDICTS:
                decision = up
                break
        result["explore_decision"] = decision
        result["reason_summary"] = extract_meta_field(response, "REASON_SUMMARY")

    return result


# ============================================================
# Re-explore after re-plan (meta orchestrates fan-out)
# ============================================================

def meta_strategist_orchestrate_reexplore(
    *,
    problem: str,
    forbidden_directions: list[str],
    prior_findings_summary: str,
    failed_records_summary: str,
    n_hints: int = 3,
    session_id: str | None = None,
    transcript_path: str | None = None,
) -> dict:
    """Meta orchestrates a re-exploration round after a re-plan was approved.

    Meta is asked to:
      1. Synthesise N (default 3) materially-different exploration hints,
         each forbidding the strategies already tried.
      2. Spawn N parallel `task(reason-noncoding, ...)` sub-agents, each with
         a different hint. The `exploration-protocol` skill instructs the
         sub-agent to write its deliverable to a hint-specific file.
      3. Read all sub-agent results, synthesise into a single set of
         exploration findings, and return them in structured fields.

    Returned dict (in addition to `_call_agent` standard keys):
      - synth_assessment: str ("SOLVED" | "PARTIALLY_SOLVED" | "NEED_PLAN" | "")
      - synth_worked:     list[str]   verified observations from the round
      - synth_failed:     list[str]   refuted directions
      - synth_candidates: list[str]   candidate approaches for plan
      - synth_solution_sketch: str    only if SOLVED
      - reason_summary:   str
    """
    from src.utils.extract import extract_meta_field, extract_meta_bullet_list

    forbidden_block = "\n".join(f"- {d}" for d in forbidden_directions) or "(none recorded)"

    prompt = f"""## Re-explore orchestration

A re-plan has just been approved. Before we restart planning, you must run a fresh exploration round that is **materially different** from the abandoned attempt(s). You orchestrate this round by spawning parallel sub-agents.

## PROBLEM
---
{problem}
---

## CONFIRMED FAILURES (from prior plan attempts)
---
{failed_records_summary or "(none)"}
---

## FORBIDDEN DIRECTIONS (must not be re-tried)
---
{forbidden_block}
---

## PRIOR EXPLORATION FINDINGS (summary)
---
{prior_findings_summary or "(no prior exploration findings)"}
---

## YOUR JOB IS NOT TO DO THE MATH

You devise hints, fan out sub-agents, and synthesise their results. Do **not** write proofs or constructions yourself; that is
the sub-agents' job. Your output is judged on whether the sub-agents collectively explore *new* directions, not on your own reasoning depth.

## STEP 1 — Diagnose the failure mode (silently)

Before spawning anything, identify:

- (a) Did the abandoned plans share a common **target answer / sharp value** (e.g. they all tried to prove the same constant)? If yes, this target is **suspect** and the next round must include at least one sub-agent whose explicit task is to **construct a witness with a different value** — i.e. to refute the candidate answer, not extend it.
- (b) Did the abandoned plans share a common **proof technique**? If yes, every sub-agent must use a different technique.

This diagnosis informs the {n_hints} hints below.

## STEP 2 — Devise {n_hints} materially-different hints

Each hint must:

- Avoid all FORBIDDEN DIRECTIONS verbatim.
- Pick an angle **orthogonal** to the abandoned plans' axes (different
  technique AND, if applicable, different target value).
- Be a *direction*, not a target: phrase it as "try X structure / try Y invariant / try Z combinatorial reformulation", not "prove the answer is W". See the `math-solving-strategies` skill (Construction Search) and the `construct-counterexamples` skill (Construction families) for generic axes you can use as starting points.
- If diagnosis (a) above flagged a suspect target value, **at least one hint must be of refutation type**: "construct a configuration where the ratio / quantity / value differs from <suspect target>". Phrase it as a task for the sub-agent to follow the `construct-counterexamples` skill.

## STEP 3 — Spawn {n_hints} sub-agents IN PARALLEL

Issue **{n_hints} parallel** `task(agent="reason-noncoding", ...)` calls — emit them in a single response so the runtime can dispatch them concurrently. Do **not** spawn them one at a time — that defeats the orthogonality of the search.

Each sub-agent's prompt must include:

1. The full **problem statement** verbatim (sub-agents see no injected context — the prompt is the only context they get).
2. The **specific hint** for that sub-agent.
3. The **FORBIDDEN DIRECTIONS** list verbatim (so the sub-agent does not waste effort re-attempting failed approaches).
4. A pointer to the appropriate skill: typically "follow the `exploration-protocol` skill" for general exploration, or "follow the `construct-counterexamples` skill" for refutation tasks.
5. A path to write the deliverable:
   `scratch/<problem_id>/exploration-reexplore-hint{{N}}.md` (N=1..{n_hints}).
6. Soft budget: 8 min each.

## STEP 4 — Wait, read, and SYNTHESISE

Wait for **all** {n_hints} sub-agents to finish. Read each deliverable
file (or the sub-agent return text if the file is missing).

When synthesising:

- **Drop** any candidate that overlaps a FORBIDDEN DIRECTION.
- **Prefer** candidates that come from refutation sub-agents if they
  succeeded, even partially (refuting the old target is the most
  valuable outcome of this round).
- **Deduplicate** across sub-agents.

## STEP 5 — Output the structured block FIRST, then optional prose

The orchestrator parses **only** the structured fields below. Output them **before** any prose — this is mandatory. Empty fields use `(none)`.

### SYNTH_ASSESSMENT
SOLVED | PARTIALLY_SOLVED | NEED_PLAN

(SOLVED only if at least one sub-agent assembled a complete solution.
PARTIALLY_SOLVED if there is concrete structural progress including a
refutation of the prior target. NEED_PLAN otherwise.)

### REASON_SUMMARY
A brief, single-paragraph summary of the round's outcome and the
direction the next plan should pursue. No bullets, no headings.

### SYNTH_WORKED
- bullet list of verified observations / structural facts that survived
  across sub-agents (deduplicated)
- (use `(none)` if empty)

### SYNTH_FAILED
- bullet list of directions sub-agents tried and refuted (these become
  additional forbidden_directions for the next plan)
- (use `(none)` if empty)

### SYNTH_CANDIDATES
- bullet list of candidate approaches for the next plan, ordered by
  promise. The first entry should be the **most-different-from-the-old-approach** angle that still has supporting evidence.
- (use `(none)` if empty)

### SYNTH_SOLUTION_SKETCH
(Only if SYNTH_ASSESSMENT == SOLVED. Otherwise write `(n/a)`.)

After the structured block you MAY append a short prose summary of how the sub-agents diverged, but the structured block is the canonical output."""

    result = _call_agent(
        prompt=prompt,
        agent="meta-strategist",
        model=CONFIG.META_STRATEGIST_MODEL,
        timeout=CONFIG.META_STRATEGIST_TIMEOUT,
        session_id=session_id,
        transcript_path=transcript_path,
        output_label="Meta-Strategist (orchestrate re-explore)",
        reasoning_effort=CONFIG.META_STRATEGIST_EFFORT,
    )

    if result.get("success"):
        response = result.get("response", "") or ""
        raw = extract_meta_field(response, "SYNTH_ASSESSMENT").strip()
        assess = ""
        for tok in raw.replace("`", " ").split():
            up = tok.strip("*_:.,").upper()
            if up in {"SOLVED", "PARTIALLY_SOLVED", "NEED_PLAN"}:
                assess = up
                break
        result["synth_assessment"] = assess
        result["reason_summary"] = extract_meta_field(response, "REASON_SUMMARY")
        result["synth_worked"] = extract_meta_bullet_list(response, "SYNTH_WORKED")
        result["synth_failed"] = extract_meta_bullet_list(response, "SYNTH_FAILED")
        result["synth_candidates"] = extract_meta_bullet_list(response, "SYNTH_CANDIDATES")
        sketch = extract_meta_field(response, "SYNTH_SOLUTION_SKETCH")
        result["synth_solution_sketch"] = "" if sketch.strip().lower() in {"(n/a)", "n/a"} else sketch

    return result
