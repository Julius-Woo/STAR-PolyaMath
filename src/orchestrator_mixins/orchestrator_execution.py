"""
Step execution and verification behaviors for Orchestrator.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.config import CONFIG
from src.state import StepStatus
from src.agent_runner import _extract_partial_work_from_transcript
from src.agents import (
    _call_agent,
    meta_strategist_diagnose_and_guide,
    reasoner_execute_meta_task,
    reasoner_execute_step,
    resume_session,
    verifier_review_report,
)
from src.agents.agents_reasoner import _build_meta_retry_prompt


def _parse_verifier_trace_back_to(text: str, current_step: int) -> int | None:
    """Parse a structured TRACE_BACK_TO step number from the Verifier's report.

    The verifier-review-protocol skill mandates a `## TRACE_BACK_TO` section
    whose body is a single integer M (1 <= M <= current_step). Older formats
    (`**TRACE_BACK to Step M**` inline in the verdict) are also accepted as a
    transitional fallback.

    Returns:
        The parsed step number if present and in range, otherwise None.
        The orchestrator escalates None as PROPOSE_REPLAN; the Meta-Strategist
        decides where to go next rather than the orchestrator guessing.
    """
    # Preferred: a dedicated section `## TRACE_BACK_TO` with an integer body.
    m = re.search(
        r"^\s*##\s+TRACE_BACK_TO\s*$\s*([\s\S]*?)(?=^\s*##\s+\S|\Z)",
        text,
        re.IGNORECASE | re.MULTILINE,
    )
    if m:
        body = m.group(1)
        num = re.search(r"\b(\d+)\b", body)
        if num:
            n = int(num.group(1))
            if 1 <= n <= current_step:
                return n

    # Transitional fallback: `**TRACE_BACK to Step M**` inline in the verdict.
    m = re.search(
        r"TRACE[_ ]BACK\s+to\s+Step\s*(\d+)",
        text,
        re.IGNORECASE,
    )
    if m:
        n = int(m.group(1))
        if 1 <= n <= current_step:
            return n

    return None

class OrchestratorExecutionMixin:
    """Step execution and verification behaviors for Orchestrator."""

    def execute_current_step(self) -> dict[str, Any]:
        """Execute the current step."""
        step_info = self.state.get_current_step()
        if not step_info:
            return {"success": False, "error": "No current step"}
        
        self.log(f"Executing Step {step_info.step_number}: {step_info.title}")
        self.state.set_phase(f"executing_step_{step_info.step_number}")
        self.state.update_step_status(step_info.step_number, StepStatus.IN_PROGRESS)
        
        # Get context
        problem = self.state.get_problem()
        plan = self.state.get_plan()
        completed = self.state.get_completed_steps_context()
        
        # Call Reasoner — reuse persistent session if available (cross-step continuity).
        # When session_id is provided, reasoner_execute_step uses a short prompt that
        # only names the next step, avoiding redundant re-load of problem/plan/completed_steps.
        transcript_path = str(self.state.base_dir / "jsonl" / f"reason-step{step_info.step_number:02d}-report.jsonl")
        # Deterministic reasoner session name. _call_agent decides --name vs
        # --resume based on whether the name has been spawned in this process.
        reasoner_name = self.state.derived_reasoner_session_name()
        reasoner_session_id = self.state.reasoner_session_name or reasoner_name
        if self.state.reasoner_session_name:
            self.log(f"Reusing Reasoner session: {reasoner_session_id}")
        result = reasoner_execute_step(
            problem=problem,
            plan=plan,
            completed_steps=completed,
            step_number=step_info.step_number,
            step_title=step_info.title,
            code_dir=str(self.state.code_dir),
            session_id=reasoner_session_id,
            transcript_path=transcript_path,
        )
        # Persist deterministic session name (idempotent).
        if result.get("success") and self.state.reasoner_session_name is None:
            self.state.reasoner_session_name = reasoner_name
            self.state.save()
        
        # Check for timeout - use error_type from result
        if not result.get("success"):
            is_our_timeout = result.get("is_our_timeout", False)
            
            if is_our_timeout:
                # Handle with Meta-Strategist guidance
                # Pass streaming-captured partial output (result.response has it even on timeout)
                streaming_partial = result.get("response", "") or result.get("stdout", "")
                timeout_result = self._handle_reasoner_timeout(
                    step_info=step_info,
                    problem=problem,
                    plan=plan,
                    completed=completed,
                    transcript_path=transcript_path,
                    streaming_partial_output=streaming_partial,
                )
                if timeout_result.get("success"):
                    result = timeout_result
                else:
                    self.log(f"Reasoner timeout not resolved: {timeout_result.get('error')}")
                    return timeout_result
            else:
                # Other errors handled by _call_agent with fresh session retries
                # If we're here, all retries failed
                self.log(f"Reasoner failed: {result.get('error')}")
                return result
        
        report = result.get("response", "")
        self.state.save_step_report(step_info.step_number, report)
        
        self.log("Step report received, sending to verification...")
        return {
            "success": True,
            "report": report,
            "step": step_info.step_number,
        }
    
    # ========== Phase 3: Verification ==========
    

    def verify_current_step(self) -> dict[str, Any]:
        """Verify the current step's report."""
        step_info = self.state.get_current_step()
        if not step_info:
            return {"success": False, "error": "No current step"}
        
        report_file = self.state.step_report_file(step_info.step_number)
        if not report_file.exists():
            return {"success": False, "error": "No report to verify"}
        
        report = report_file.read_text(encoding="utf-8")
        
        # Check what verification is needed
        has_verified = "[verified]" in report.lower()
        has_easy = "[easy-verify]" in report.lower()
        has_hard = "[hard-verify]" in report.lower()
        
        # Always send to Verifier — tags tell the Verifier what type of review to do.
        # If has [easy-verify], run the code as a preliminary check before Verifier review.
        if has_easy:
            self.log("Found [easy-verify] claims, running code as preliminary check...")
            code_results = self._run_easy_verify_code(report, step_info.step_number)
            if code_results.get("errors"):
                self.log(f"Code verification found {len(code_results['errors'])} errors")
                # Append code verification results to report for Verifier
                report += f"\n\n## Code Verification Results (Preliminary)\n\n{code_results.get('summary', '')}"
        
        self.log("Sending to Verifier for review...")
        return self._run_verifier(step_info.step_number, report)
    

    def _run_easy_verify_code(self, report: str, step_num: int) -> dict[str, Any]:
        """Extract and run [easy-verify] code blocks (fallback mechanism).
        
        Note: Ideally Reasoner should run code itself and use [verified] tags.
        This is a fallback for cases where Reasoner proposes but doesn't execute code.
        """
        import subprocess
        
        # Find all [easy-verify] sections with code
        pattern = r'\[easy-verify\][^\`]*```python\s*(.*?)```'
        matches = re.findall(pattern, report, re.DOTALL | re.IGNORECASE)
        
        if not matches:
            return {"success": True, "errors": [], "summary": "No code blocks found"}
        
        results = []
        errors = []
        
        for i, code in enumerate(matches):
            code = code.strip()
            if not code:
                continue
            
            # Save code to file
            code_file = self.state.base_dir / "code" / f"step_{step_num:02d}_verify_{i+1}.py"
            code_file.parent.mkdir(parents=True, exist_ok=True)
            code_file.write_text(code, encoding="utf-8")
            
            self.log(f"  Running code block {i+1}...")
            
            try:
                result = subprocess.run(
                    ["python", str(code_file)],
                    capture_output=True,
                    text=True,
                    timeout=CONFIG.CODE_EXECUTION_TIMEOUT,
                    cwd=str(self.state.base_dir / "code"),
                )
                
                output = result.stdout.strip()
                if result.returncode == 0:
                    results.append({
                        "block": i + 1,
                        "success": True,
                        "output": output,
                    })
                    self.log(f"    ✓ Output: {output[:100]}...")
                else:
                    error_msg = result.stderr.strip() or "Unknown error"
                    errors.append({
                        "block": i + 1,
                        "error": error_msg,
                    })
                    self.log(f"    ✗ Error: {error_msg[:100]}...")
                    
            except subprocess.TimeoutExpired:
                errors.append({
                    "block": i + 1,
                    "error": f"Timeout after {CONFIG.CODE_EXECUTION_TIMEOUT}s",
                })
                self.log(f"    ✗ Timeout")
            except Exception as e:
                errors.append({
                    "block": i + 1,
                    "error": str(e),
                })
                self.log(f"    ✗ Exception: {e}")
        
        # Build summary
        summary_lines = ["### Code Execution Results\n"]
        for r in results:
            summary_lines.append(f"- Block {r['block']}: ✓ `{r['output'][:80]}`")
        for e in errors:
            summary_lines.append(f"- Block {e['block']}: ✗ {e['error'][:80]}")
        
        return {
            "success": len(errors) == 0,
            "results": results,
            "errors": errors,
            "summary": "\n".join(summary_lines),
        }
    

    def _handle_reasoner_timeout(
        self,
        step_info,
        problem: str,
        plan: str,
        completed: str,
        transcript_path: str | None = None,
        streaming_partial_output: str = "",
    ) -> dict[str, Any]:
        """
        Handle Reasoner timeout with Meta-Strategist guidance.
        
        When Reasoner times out (600s), we:
        1. Consult Meta-Strategist for strategic guidance
        2. Resume the Reasoner session with the guidance
        3. Retry up to MAX_TIMEOUT_RETRIES times
        
        Args:
            streaming_partial_output: Partial output captured from streaming stdout.
                                     This is more reliable than the JSONL transcript when process is killed.
        
        Returns:
            - success: True if step completed after retry
            - report: Reasoner's report if successful
        """
        # Prefer streaming-captured output (reliable) over JSONL transcript (may be empty on kill)
        partial_output = streaming_partial_output
        if not partial_output:
            recovered = _extract_partial_work_from_transcript(transcript_path)
            if recovered:
                partial_output = recovered
        
        # Extract session ID for resumption (may be None if session wasn't established)
        # Deterministic session name; no extraction from share file.
        session_id = self.state.reasoner_session_name or self.state.derived_reasoner_session_name()
        if not session_id:
            self.log("No session ID found - will start fresh session with Meta-Strategist guidance")
        
        for retry in range(1, CONFIG.MAX_TIMEOUT_RETRIES + 1):
            self.log(f"Timeout retry {retry}/{CONFIG.MAX_TIMEOUT_RETRIES}: Consulting Meta-Strategist...")
            
            # Build failure context for Meta-Strategist
            failure_context = f"""Timeout after {CONFIG.REASONER_TIMEOUT} seconds.
Retry: {retry}/{CONFIG.MAX_TIMEOUT_RETRIES}

Partial output before timeout:
---
{partial_output}
---"""
            
            # Get diagnosis and task from Meta-Strategist (uses persistent session)
            meta_result = meta_strategist_diagnose_and_guide(
                problem=problem,
                plan=plan,
                completed_steps=completed,
                current_step_number=step_info.step_number,
                current_step_title=step_info.title,
                current_report=partial_output,
                failure_type="timeout",
                failure_context=failure_context,
                intervention_history=self.state.meta_intervention_history,
                session_id=self.state.meta_strategist_session_name or self.state.derived_meta_session_name(),
                transcript_path=str(self.state.base_dir / "jsonl" / f"meta-step{step_info.step_number:02d}-timeout-r{retry}.jsonl"),
            )
            
            # Update Meta-Strategist session ID for persistence
            # Persist deterministic name on first success.
            if meta_result.get("success") and self.state.meta_strategist_session_name is None:
                self.state.meta_strategist_session_name = self.state.derived_meta_session_name()
                self.state.save()
            
            if not meta_result.get("success"):
                self.log(f"Meta-Strategist failed: {meta_result.get('error')}")
                guidance = "The previous attempt timed out. Try a simpler, more direct approach. Avoid exhaustive computation - seek structural insight first."
                use_pure_reasoning = False
                action_type = "retry_different_method"
            else:
                guidance = meta_result.get("response", "")
                use_pure_reasoning = meta_result.get("use_pure_reasoning", False)
                action_type = meta_result.get("action_type", "retry_different_method")
                self.log(
                    f"Meta-Strategist provided guidance ({len(guidance)} chars), "
                    f"action_type={action_type}"
                )
                if use_pure_reasoning:
                    self.log("⚠️ Meta-Strategist recommends NON-CODING mode")

                # Record intervention
                self.state.meta_intervention_history.append({
                    "type": "timeout",
                    "retry": retry,
                    "action_type": action_type,
                    "task": meta_result.get("specific_task", "")[:500],
                    "use_pure_reasoning": use_pure_reasoning,
                    "outcome": "pending",
                })
                self.state.save()

            # Honor meta's structural verdict instead of
            # blindly looping retry_different_method. If meta said the plan
            # itself is wrong (`replan`) or that an earlier step is the
            # real bottleneck (`trace_back`), surface that to the solve loop
            # rather than burning another timeout retry on the same step.
            # `replan` here is meta's first-line verdict during diagnose; the
            # solve loop short-circuits to APPROVE_REPLAN without calling
            # `decide_replan` again (the persistent meta session has the same
            # context either way, so the second call is redundant).
            if action_type == "replan":
                self.log(
                    "Meta-Strategist recommended replan during timeout — "
                    "surfacing as timeout_exhausted with short-circuit to "
                    "APPROVE_REPLAN."
                )
                return {
                    "success": False,
                    "timeout_exhausted": True,
                    "meta_action": "replan",
                    "meta_reason": guidance,
                    "error": (
                        f"Step {step_info.step_number} timeout retry {retry}: "
                        "Meta-Strategist says the plan itself is unsound; re-plan."
                    ),
                    "step_number": step_info.step_number,
                }
            if action_type == "trace_back":
                trace_to_step = meta_result.get("trace_back_to") or step_info.step_number
                if not isinstance(trace_to_step, int) or trace_to_step < 1:
                    trace_to_step = step_info.step_number
                self.log(
                    f"Meta-Strategist recommended trace_back to step {trace_to_step} "
                    "during timeout — surfacing for solve loop to apply."
                )
                return {
                    "success": False,
                    "meta_trace_back": True,
                    "trace_back_to": trace_to_step,
                    "meta_reason": guidance,
                    "error": (
                        f"Step {step_info.step_number} timeout retry {retry}: "
                        f"Meta-Strategist recommends trace_back to step {trace_to_step}."
                    ),
                    "step_number": step_info.step_number,
                }
            
            # Determine which agent to use
            agent_type = "reason-noncoding" if use_pure_reasoning else "reason"

            # Resume Reasoner session with guidance (centralized helper).
            resume_prompt = _build_meta_retry_prompt(
                guidance,
                resume=True,
                step_number=step_info.step_number,
            )
            if use_pure_reasoning:
                resume_prompt += (
                    "\n\n---\n** NON-CODING MODE ACTIVE **\n"
                    "You are now in PURE REASONING mode with NO access to code execution or terminal tools.\n"
                    "You can ONLY read files and write mathematical proofs.\n"
                    "Focus on theoretical arguments, algebraic manipulation, and logical deduction.\n---\n"
                )
            
            new_transcript_path = str(self.state.base_dir / "jsonl" / f"reason-step{step_info.step_number:02d}-timeout-r{retry}.jsonl")
            
            # If we have a session ID, resume; otherwise start fresh with guidance
            if session_id:
                resume_result = resume_session(
                    session_id=session_id,
                    new_message=resume_prompt,
                    timeout=CONFIG.REASONER_TIMEOUT,
                    transcript_path=new_transcript_path,
                    agent=agent_type,
                )
            else:
                # No session to resume - start fresh with Meta-Strategist guidance included
                self.log("Starting fresh session with Meta-Strategist guidance...")
                
                # Extract key progress from partial_output (last 1500 chars to stay within limits)
                partial_context = ""
                if partial_output:
                    # Try to extract meaningful progress (skip markdown headers, focus on content)
                    lines = partial_output.strip().split('\n')
                    # Find lines with actual reasoning content (not just headers or empty)
                    content_lines = [l for l in lines if l.strip() and not l.startswith('#') and not l.startswith('---')]
                    if content_lines:
                        partial_context = "\n".join(content_lines[-30:])  # Last 30 content lines
                        if len(partial_context) > 1500:
                            partial_context = partial_context[-1500:]
                
                problem_summary = (
                    f"PROBLEM:\n{problem}\n\nPLAN:\n{plan}\n\n"
                    f"COMPLETED STEPS:\n{completed if completed else '(none yet)'}\n\n"
                    f"CURRENT TASK: Execute Step {step_info.step_number}: {step_info.title}"
                )
                if partial_context:
                    problem_summary += (
                        f"\n\nPARTIAL PROGRESS FROM TIMED-OUT ATTEMPT (do not continue same approach):\n{partial_context}"
                    )
                fresh_prompt = _build_meta_retry_prompt(
                    guidance,
                    resume=False,
                    problem_summary=problem_summary,
                    step_number=step_info.step_number,
                )
                if use_pure_reasoning:
                    fresh_prompt += (
                        "\n\n---\n** NON-CODING MODE ACTIVE **\n"
                        "You are now in PURE REASONING mode with NO access to code execution or terminal tools.\n"
                        "You can ONLY read files and write mathematical proofs.\n"
                        "Focus on theoretical arguments, algebraic manipulation, and logical deduction.\n---\n"
                    )

                # Determine output label based on agent type
                output_label = "Reasoner (non-coding)" if use_pure_reasoning else "Reasoner"

                # Use the fresh_prompt that includes Meta-Strategist guidance
                resume_result = _call_agent(
                    prompt=fresh_prompt,
                    agent=agent_type,
                    model=CONFIG.REASONER_MODEL,
                    timeout=CONFIG.REASONER_TIMEOUT,
                    transcript_path=new_transcript_path,
                    output_label=output_label,
                )
            
            if resume_result.get("success"):
                response = resume_result.get("response", "")
                
                # Update session ID for potential next retry and persist cross-step
                # Deterministic name; persist on first call.
                if self.state.reasoner_session_name is None:
                    self.state.reasoner_session_name = self.state.derived_reasoner_session_name()
                    self.state.save()
                
                # Check if this looks like a complete step report
                if "## Summary" in response or "## Next Steps" in response or "[verified]" in response:
                    self.log(f"Reasoner completed step after timeout retry {retry}")
                    self.state.save_step_report(step_info.step_number, response)
                    return {
                        "success": True,
                        "response": response,  # Use "response" key to match caller expectation
                        "retries": retry,
                    }
                
                # Partial progress - update output and continue
                recovered = _extract_partial_work_from_transcript(new_transcript_path)
                partial_output = recovered if recovered else response
                self.log(f"Retry {retry}: Got response but may be incomplete")
            else:
                # Another timeout or error
                error = resume_result.get("error", "Unknown error")
                self.log(f"Retry {retry} failed: {error}")
                
                # Update partial output if available
                recovered = _extract_partial_work_from_transcript(new_transcript_path)
                if recovered:
                    partial_output = recovered
        
        # Max retries exhausted: surface a structured `timeout_exhausted`
        # signal so the solve loop can escalate to the Meta-Strategist for a
        # re-plan decision rather than failing outright.
        return {
            "success": False,
            "timeout_exhausted": True,
            "error": f"Reasoner timed out {CONFIG.MAX_TIMEOUT_RETRIES + 1} times (initial + {CONFIG.MAX_TIMEOUT_RETRIES} retries)",
            "step_number": step_info.step_number,
        }




    def _run_verifier(self, step_num: int, report: str) -> dict[str, Any]:
        """Run Verifier on a report."""
        self.state.update_step_status(step_num, StepStatus.VERIFYING)
        
        problem = self.state.get_problem()
        completed = self.state.get_completed_steps_context()
        
        # Pass deterministic session name so sessionStart hook
        # can inject PROBLEM_STATE.md as additionalContext.
        verifier_name = self.state.derived_verifier_session_name(step_num)
        result = verifier_review_report(
            problem=problem,
            completed_steps=completed,
            report=report,
            step_number=step_num,
            code_dir=str(self.state.code_dir),
            transcript_path=str(self.state.base_dir / "jsonl" / f"verify-step{step_num:02d}-report.jsonl"),
            intervention_history=self.state.meta_intervention_history,
            session_id=verifier_name,
        )
        
        if not result.get("success"):
            return result
        
        verification = result.get("response", "")
        
        # Check for empty response - Verifier may have failed silently
        if not verification.strip():
            self.log("⚠️ Verifier returned empty response - treating as failure")
            return {"success": False, "error": "Verifier returned empty response"}
        
        self.state.save_step_verify(step_num, verification)
        
        # Parse Verifier's decision
        decision = self._parse_verifier_decision(verification)
        
        if decision == "accept":
            self.log("Verifier ACCEPTS the report")
            return {"success": True, "decision": "accept", "verification": verification}
        elif decision == "trace_back":
            from src.utils.extract import extract_section_paragraph
            reason_summary = (
                extract_section_paragraph(verification, "Reason")
                or "Verifier requested trace-back; see verify file."
            )
            trace_to = _parse_verifier_trace_back_to(verification, step_num)

            if trace_to is None:
                # No structured TRACE_BACK_TO field, or out of range. Per the
                # verifier-review-protocol skill the verifier should either
                # name an earlier step or fall back to PROPOSE_REPLAN; an
                # ambiguous trace_back is treated as a re-plan proposal so
                # the Meta-Strategist decides the next move.
                self.log(
                    "Verifier verdict TRACE_BACK lacks a parseable TRACE_BACK_TO; "
                    "escalating as PROPOSE_REPLAN."
                )
                return {
                    "success": True,
                    "decision": "propose_replan",
                    "verification": verification,
                    "triggered_by": "verifier",
                    "reason": reason_summary,
                }

            if trace_to == step_num:
                # "Trace back to current step" is semantically identical to
                # CHALLENGE — archiving and re-executing the same step from
                # scratch loses the in-step debate context for no benefit.
                self.log(
                    f"Verifier verdict TRACE_BACK_TO {trace_to} == current step; "
                    "converting to CHALLENGE."
                )
                return {
                    "success": True,
                    "decision": "challenge",
                    "verification": verification,
                }

            self.log(f"Verifier requests trace-back to step {trace_to}")
            return {
                "success": True,
                "decision": "trace_back",
                "verification": verification,
                "trace_back_to": trace_to,
                "triggered_by": "verifier",
                "reason": reason_summary,
            }
        elif decision == "propose_replan":
            # Verifier escalates to a re-plan request. Surface to
            # the solve loop, which routes to meta_strategist_decide_replan.
            self.log("Verifier PROPOSES re-plan")
            from src.utils.extract import extract_section_paragraph
            reason_summary = (
                extract_section_paragraph(verification, "Reason")
                or "Verifier proposed re-plan; see verify file."
            )
            return {
                "success": True,
                "decision": "propose_replan",
                "verification": verification,
                "triggered_by": "verifier",
                "reason": reason_summary,
            }
        else:
            self.log("Verifier CHALLENGES the report")
            return {"success": True, "decision": "challenge", "verification": verification}
    


    def _parse_verifier_decision(self, verification: str) -> str:
        """Parse Verifier's decision from response.

        Returns one of: "accept" | "challenge" | "trace_back" | "propose_replan".

        parse the verdict from inside the `## Verdict`
        section only. Naive whole-text substring matching produced false
        positives when the verifier's prose discussed but rejected a
        verdict (e.g. "the flaw does not trace back to earlier steps"
        was misread as TRACE_BACK).
        """
        import re

        # Try to extract the body of the `## Verdict` section first.
        m = re.search(
            r"^\s*##\s+Verdict\s*$([\s\S]*?)(?=^\s*##\s+\S|\Z)",
            verification,
            re.IGNORECASE | re.MULTILINE,
        )
        verdict_body = (m.group(1) if m else verification).upper()

        # Order matters: PROPOSE_REPLAN before TRACE_BACK before ACCEPT
        # (a verifier may mention earlier verdicts in the Reason section).
        if "PROPOSE_REPLAN" in verdict_body or "PROPOSE-REPLAN" in verdict_body:
            return "propose_replan"
        if "TRACE_BACK" in verdict_body or "TRACE BACK" in verdict_body:
            return "trace_back"
        if (
            "**ACCEPT**" in verdict_body
            or "VERDICT: ACCEPT" in verdict_body
            or ("ACCEPT" in verdict_body and "CHALLENGE" not in verdict_body)
        ):
            return "accept"
        return "challenge"
    


    # ========== Phase 4: Challenge Loop ==========
