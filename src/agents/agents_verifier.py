"""
Verifier agent prompts.
"""

from __future__ import annotations

from typing import Any

from src.config import CONFIG
from src.agent_runner import _call_agent

# ============================================================
# Verifier Functions
# ============================================================

def verifier_review_report(
    problem: str,
    completed_steps: str,
    report: str,
    step_number: int,
    code_dir: str | None = None,
    transcript_path: str | None = None,
    intervention_history: list[dict] | None = None,
    session_id: str | None = None,  # Deterministic session NAME for resume.
    name: str | None = None,  # Deterministic name for spawning a NEW session.
) -> dict[str, Any]:
    """
    Ask Verifier to review a reasoning report.
    
    Args:
        intervention_history: List of Meta-Strategist interventions (timeout/calibration)
            that occurred during this step's execution.
    """
    # Code instruction at the TOP of the prompt for visibility
    code_instruction = ""
    if code_dir:
        code_instruction = f"""## ⚠️ CODE SAVING REQUIREMENT ⚠️
**You MUST save all Python scripts to:** `{code_dir}/`
**Filename format:** `verify_step{step_number:02d}_*.py`
**DO NOT use `scratch/` directly - use the path above.**

---

"""

    # Build intervention context section if there was Meta-Strategist guidance
    intervention_context = ""
    if intervention_history:
        # Filter to interventions relevant to this step (recent ones)
        relevant = [h for h in intervention_history if h.get("type") == "timeout"]
        if relevant:
            intervention_context = "\n## EXECUTION CONTEXT \n"
            intervention_context += """The Reasoner encountered issues during this step's execution.
Meta-Strategist provided guidance to help resolve them. This context may help you understand the approach taken.

"""
            for i, h in enumerate(relevant[-3:], 1):  # Last 3 interventions
                intervention_context += f"### Intervention {i}\n"
                intervention_context += f"- **Type**: {h.get('type', 'unknown')}\n"
                if h.get('action_type'):
                    intervention_context += f"- **Action**: {h.get('action_type')}\n"
                if h.get('task'):
                    intervention_context += f"- **Meta-Strategist Task**: {h.get('task')[:300]}...\n"
                if h.get('use_pure_reasoning'):
                    intervention_context += f"- **Mode**: Pure reasoning (no code execution)\n"
                intervention_context += "\n"
            intervention_context += "---\n\n"

    prompt = f"""{code_instruction}{intervention_context}Review the Step {step_number} report below.

The problem statement, plan, current step goal, and prior verified results
are in your injected `[Live problem state]` context — treat it as authoritative.

Follow the **`verifier-review-protocol`** skill: Two-gate evaluation, required
output structure, verdict rules, and the mandatory `Verified Results` summary.
For tag semantics see the **`verification-tag-protocol`** skill.

STEP {step_number} REPORT TO VERIFY:
---
{report}
---

Produce your verdict now."""
    
    return _call_agent(
        prompt=prompt,
        agent="verify",
        model=CONFIG.VERIFIER_MODEL,
        timeout=CONFIG.VERIFIER_TIMEOUT,
        session_id=session_id,
        name=name,
        transcript_path=transcript_path,
        output_label="Verifier",
        reasoning_effort=CONFIG.VERIFIER_EFFORT,
    )


def verifier_evaluate_response(
    response: str,
    code_dir: str | None = None,
    session_id: str | None = None,  # Deterministic session NAME for resume.
    transcript_path: str | None = None,
    # Context parameters for new session (when session_id is None or lost)
    problem: str | None = None,
    completed_steps: str | None = None,
    step_number: int | None = None,
    original_report: str | None = None,  # Reasoner's original step report
    name: str | None = None,  # Deterministic name for spawning a NEW session.
) -> dict[str, Any]:
    """
    Ask Verifier to evaluate Reasoner's response to challenge.
    
    If session_id is None (new session), full context is included.
    If session_id is provided (continuing session), only the response is sent.
    """
    # Code instruction - prominent for new sessions, brief reminder for continuing
    code_instruction_header = ""
    code_instruction_reminder = ""
    if code_dir:
        code_instruction_header = f"""## ⚠️ CODE SAVING REQUIREMENT ⚠️
**You MUST save all Python scripts to:** `{code_dir}/`
**DO NOT use `scratch/` directly - use the path above.**

---

"""
        code_instruction_reminder = f"\n**Remember: Save any scripts to `{code_dir}/`**\n"

    if session_id:
        # Continuing the verifier session — short prompt; PROBLEM_STATE already injected.
        # Fallback: if injected context missing, read scratch/<id>/PROBLEM_STATE.md.
        prompt = f"""The Reasoner's response to your challenge:

---
{response}
---

Follow the **`verifier-review-protocol`** skill (Two-gate evaluation, output
structure, mandatory `Verified Results` summary). Did the Reasoner adequately
address the concerns? Update your verdict. Treat the injected `[Live problem state]` context as authoritative.
{code_instruction_reminder}"""
    else:
        # Fresh session fallback — keep the original step report inline since
        # the verifier doesn't have step-internal challenge history.
        prompt = f"""{code_instruction_header}You are evaluating the Reasoner's response to a challenge on Step {step_number}.

The step's goal, plan, prior verified results, and prior failures are in your
injected `[Live problem state]` context.

ORIGINAL STEP {step_number} REPORT:
---
{original_report if original_report else "(not provided)"}
---

The Reasoner's response:
---
{response}
---

Follow the **`verifier-review-protocol`** skill (Two-gate evaluation, output
structure, mandatory `Verified Results` summary). Produce your verdict."""

    return _call_agent(
        prompt=prompt,
        agent="verify",
        model=CONFIG.VERIFIER_MODEL,
        timeout=CONFIG.VERIFIER_TIMEOUT,
        session_id=session_id,
        name=name,
        transcript_path=transcript_path,
        output_label="Verifier",
        reasoning_effort=CONFIG.VERIFIER_EFFORT,
    )


def verifier_continue_session(
    prompt: str,
    session_id: str | None = None,
    transcript_path: str | None = None,
) -> dict[str, Any]:
    return _call_agent(
        prompt=prompt,
        agent="verify",
        model=CONFIG.VERIFIER_MODEL,
        timeout=CONFIG.VERIFIER_TIMEOUT,
        session_id=session_id,
        transcript_path=transcript_path,
        output_label="Verifier",
        reasoning_effort=CONFIG.VERIFIER_EFFORT,
    )

