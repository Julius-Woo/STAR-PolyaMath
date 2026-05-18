"""
Challenge loop and convergence behaviors for Orchestrator.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.config import CONFIG
from src.state import StepStatus
from src.agents import (
    meta_strategist_diagnose_and_guide,
    reasoner_respond_challenge,
    verifier_evaluate_response,
)
from src.agents.agents_reasoner import _build_meta_retry_prompt
from src.orchestrator_mixins.orchestrator_execution import _parse_verifier_trace_back_to

class OrchestratorChallengeMixin:
    """Challenge loop and convergence behaviors for Orchestrator."""

    def run_challenge_loop(self) -> dict[str, Any]:
        """Run the challenge loop between Reasoner and Verifier."""
        step_info = self.state.get_current_step()
        if not step_info:
            return {"success": False, "error": "No current step"}
        
        self.state.update_step_status(step_info.step_number, StepStatus.CHALLENGING)
        self.state.set_phase(f"challenging_step_{step_info.step_number}")
        
        # Get initial state
        report_file = self.state.step_report_file(step_info.step_number)
        verify_file = self.state.step_verify_file(step_info.step_number)
        
        if not report_file.exists() or not verify_file.exists():
            return {"success": False, "error": "Missing report or verification"}
        
        report = report_file.read_text(encoding="utf-8")
        challenge = verify_file.read_text(encoding="utf-8")
        
        # Start debate log
        self.state.append_debate(step_info.step_number, "Reasoner (initial)", report)
        self.state.append_debate(step_info.step_number, "Verifier (initial)", challenge)
        
        # Session names for maintaining conversation continuity within challenge loop.
        # Deterministic names instead of CLI-issued UUIDs. Reasoner name is
        # the cross-step persistent name (already created during step execution); Verifier
        # gets a fresh per-step name (preserves adversarial independence).
        reasoner_session: str | None = self.state.reasoner_session_name
        verifier_session: str | None = None
        verifier_name = self.state.derived_verifier_session_name(step_info.step_number)
        # Track whether we've spawned the verifier session yet within this loop.
        verifier_spawned: bool = False
        
        # Track consecutive timeouts for escalation
        consecutive_timeouts = 0
        
        # Track if Meta-Strategist requested pure reasoning mode (no code execution)
        use_pure_reasoning: bool = False
        
        # Use while loop for better control over round advancement
        round_num = 0
        while round_num < CONFIG.MAX_CHALLENGE_ROUNDS:
            round_num += 1
            self.log(f"Challenge round {round_num}/{CONFIG.MAX_CHALLENGE_ROUNDS}")
            
            # Reasoner responds (continuing its session)
            # Include full context in case session is lost
            problem = self.state.get_problem()
            plan = self.state.get_plan()
            completed = self.state.get_completed_steps_context()
            
            transcript_path = str(self.state.base_dir / "jsonl" / f"reason-step{step_info.step_number:02d}-debate-r{round_num}.jsonl")
            reasoner_result = reasoner_respond_challenge(
                challenge=challenge,
                session_id=reasoner_session,
                transcript_path=transcript_path,
                # Context for new session (used if session_id is None or lost)
                problem=problem,
                plan=plan,
                completed_steps=completed,
                step_number=step_info.step_number,
                step_title=step_info.title,
                code_dir=str(self.state.code_dir),
                use_pure_reasoning=use_pure_reasoning,
            )
            
            if not reasoner_result.get("success"):
                # Check if this is our timeout
                if reasoner_result.get("is_our_timeout", False):
                    consecutive_timeouts += 1
                    self.log(f"Reasoner timed out in round {round_num} ({consecutive_timeouts}/{CONFIG.MAX_CONSECUTIVE_TIMEOUTS})")
                    
                    if consecutive_timeouts >= CONFIG.MAX_CONSECUTIVE_TIMEOUTS:
                        # Chronic timeouts on the same
                        # step are a deadlock signal, not a definitive fail.
                        # Hand to Meta-Strategist for a re-plan decision so the
                        # supervisor can choose between APPROVE_REPLAN (the
                        # plan's chosen attack is too expensive),
                        # TRACE_BACK_TO (an earlier step is the bottleneck),
                        # or ABORT (truly unsolvable within budget).
                        self.log(
                            f"Max consecutive timeouts ({consecutive_timeouts}) "
                            "reached — escalating to Meta-Strategist for re-plan "
                            "decision instead of failing the step."
                        )
                        return {
                            "success": True,
                            "decision": "replan",
                            "triggered_by": "orchestrator",
                            "reason": (
                                f"Step {step_info.step_number} reasoner timed "
                                f"out {consecutive_timeouts} consecutive times "
                                f"during challenge debate (round {round_num}). "
                                "Likely the planned attack is computationally "
                                "infeasible within the step budget."
                            ),
                            "rounds": round_num,
                        }
                    
                    # Get Meta-Strategist guidance for timeout
                    problem = self.state.get_problem()
                    plan = self.state.get_plan()
                    completed = self.state.get_completed_steps_summary()
                    # Prefer streaming-captured partial output (more reliable when process is killed)
                    partial_output = reasoner_result.get("response", "") or reasoner_result.get("stdout", "")
                    if not partial_output:
                        from src.agent_runner import _extract_partial_work_from_transcript
                        recovered = _extract_partial_work_from_transcript(transcript_path)
                        if recovered:
                            partial_output = recovered
                    
                    # Build failure context
                    failure_context = f"""Timeout during challenge loop debate (round {round_num}).
Consecutive timeouts: {consecutive_timeouts}/{CONFIG.MAX_CONSECUTIVE_TIMEOUTS}

Partial output before timeout:
---
{partial_output[-2000:] if len(partial_output) > 2000 else partial_output}
---"""
                    
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
                        base_dir=str(self.state.base_dir),
                        session_id=self.state.meta_strategist_session_name or self.state.derived_meta_session_name(),
                        transcript_path=str(self.state.base_dir / "jsonl" / f"meta-step{step_info.step_number:02d}-debate-timeout-r{round_num}.jsonl"),
                    )
                    
                    # Update Meta-Strategist session ID for persistence
                    # Deterministic name; persist on first successful call.
                    if meta_result.get("success") and self.state.meta_strategist_session_name is None:
                        self.state.meta_strategist_session_name = self.state.derived_meta_session_name()
                        self.state.save()
                        self.state.save()
                    
                    guidance = meta_result.get("response", "") if meta_result.get("success") else "Simplify your approach. Avoid exhaustive computation."
                    self.log(f"Meta-Strategist provided timeout guidance ({len(guidance)} chars)")
                    
                    # Check if Meta-Strategist requested pure reasoning mode
                    if meta_result.get("success") and meta_result.get("use_pure_reasoning", False):
                        use_pure_reasoning = True
                        self.log("Meta-Strategist requested pure reasoning mode (no new code)")
                    
                    # Record intervention
                    if meta_result.get("success"):
                        self.state.meta_intervention_history.append({
                            "type": "debate_timeout",
                            "round": round_num,
                            "action_type": meta_result.get("action_type", "retry_different_method"),
                            "task": meta_result.get("specific_task", "")[:500],
                            "outcome": "pending",
                        })
                        self.state.save()
                    
                    # Update challenge with Meta-Strategist guidance for retry (same round)
                    retry_preamble = _build_meta_retry_prompt(
                        guidance,
                        resume=True,
                        step_number=step_info.step_number,
                    )
                    challenge = (
                        f"{retry_preamble}\n\n## ORIGINAL CHALLENGE\n---\n{challenge}\n---"
                    )
                    
                    # Don't increment round - retry same round
                    round_num -= 1  # Compensate for the increment at loop start
                    continue
                else:
                    # Other error - already retried in _call_agent, just log and continue
                    self.log(f"Reasoner failed in round {round_num}: {reasoner_result.get('error')}")
                    round_num -= 1  # Don't count failed rounds
                    continue
            
            # Reasoner succeeded - reset timeout counter
            consecutive_timeouts = 0
            
            response = reasoner_result.get("response", "")
            self.state.append_debate(step_info.step_number, f"Reasoner (round {round_num})", response)

            # Also detect [plan-blocked] inside challenge rounds, not only on
            # the initial step report, so the tag isn't silently ignored when
            # the reasoner concludes mid-debate that the plan is unsalvageable.
            from src.utils.extract import extract_replan_proposal
            blocked = extract_replan_proposal(response)
            if blocked:
                self.log(f"Reasoner emitted [plan-blocked] in challenge round {round_num}: {blocked[:120]}")
                return {
                    "success": True,
                    "decision": "replan",
                    "triggered_by": "reasoner",
                    "reason": blocked,
                    "rounds": round_num,
                }

            # Deterministic session name; no extraction from share file needed.
            # The reasoner session name was set when the step report was first generated;
            # ensure state is in sync (idempotent).
            if reasoner_session and reasoner_session != self.state.reasoner_session_name:
                self.state.reasoner_session_name = reasoner_session
                self.state.save()
            
            # Verifier evaluates (continuing its session)
            # Include full context in case session is lost
            verifier_result = verifier_evaluate_response(
                response=response,
                code_dir=str(self.state.code_dir),
                session_id=verifier_session or verifier_name,
                transcript_path=str(self.state.base_dir / "jsonl" / f"verify-step{step_info.step_number:02d}-debate-r{round_num}.jsonl"),
                # Context for new session (used if session_id is None or lost)
                problem=problem,
                completed_steps=completed,
                step_number=step_info.step_number,
                original_report=report,
            )
            
            if not verifier_result.get("success"):
                self.log(f"Verifier failed in round {round_num}")
                continue
            
            counter = verifier_result.get("response", "")
            self.state.append_debate(step_info.step_number, f"Verifier (round {round_num})", counter)

            # After first successful verifier call, mark name spawned so the
            # next round resumes by name.
            if not verifier_spawned and verifier_result.get("success"):
                verifier_session = verifier_name
                verifier_spawned = True            
            # Check convergence
            decision = self._parse_verifier_decision(counter)
            
            if decision == "accept":
                self.log(f"Converged at round {round_num}: ACCEPTED")
                
                # CRITICAL: Update step report with Reasoner's final corrected response
                # This ensures get_completed_steps_context() returns the corrected version
                if round_num > 0:  # Only if there was actual debate (not initial acceptance)
                    self.log(f"Updating step report with Reasoner's corrected response from round {round_num}")
                    self.state.save_step_report(step_info.step_number, response)
                
                return {
                    "success": True,
                    "decision": "accept",
                    "rounds": round_num,
                }
            elif decision == "trace_back":
                trace_to = _parse_verifier_trace_back_to(counter, step_info.step_number)
                from src.utils.extract import extract_section_paragraph
                reason_summary = (
                    extract_section_paragraph(counter, "Reason")
                    or "Verifier requested trace-back during challenge debate."
                )

                if trace_to is None:
                    # Ambiguous trace-back during debate — escalate as a
                    # re-plan proposal so Meta-Strategist decides next move.
                    self.log(
                        f"Verifier verdict TRACE_BACK at round {round_num} lacks a "
                        "parseable TRACE_BACK_TO; escalating as PROPOSE_REPLAN."
                    )
                    return {
                        "success": True,
                        "decision": "replan",
                        "triggered_by": "verifier",
                        "reason": reason_summary,
                        "rounds": round_num,
                    }

                if trace_to == step_info.step_number:
                    # "Trace back to current step" mid-debate is meaningless;
                    # treat it as a new challenge counter and continue.
                    self.log(
                        f"Verifier verdict TRACE_BACK_TO {trace_to} at round {round_num} "
                        "== current step; continuing challenge debate."
                    )
                    challenge = counter
                    continue

                self.log(f"Trace back to step {trace_to} at round {round_num}")
                return {
                    "success": True,
                    "decision": "trace_back",
                    "trace_back_to": trace_to,
                    "triggered_by": "verifier",
                    "reason": reason_summary,
                    "rounds": round_num,
                }
            
            # Update for next round
            challenge = counter
        
        # Reaching MAX_CHALLENGE_ROUNDS is treated as a
        # genuine deadlock signal, not just "rounds exhausted". Hand
        # control to the Meta-Strategist for an explicit re-plan decision.
        self.log(
            f"Max challenge rounds ({CONFIG.MAX_CHALLENGE_ROUNDS}) reached — "
            "treating as deadlock; escalating to Meta-Strategist for re-plan decision."
        )

        # Build a brief deadlock summary for the meta proposer_reason.
        debate_file = self.state.step_debate_file(step_info.step_number)
        debate_excerpt = ""
        if debate_file.exists():
            try:
                full = debate_file.read_text(encoding="utf-8")
                debate_excerpt = full[-2000:] if len(full) > 2000 else full
            except Exception:
                debate_excerpt = ""

        deadlock_reason = (
            f"Step {step_info.step_number} ('{step_info.title}') has been in "
            f"challenge debate for {CONFIG.MAX_CHALLENGE_ROUNDS} rounds without "
            "convergence. The reasoner and verifier are not converging; the "
            "plan's conjectured target may be wrong. Last debate excerpt:\n"
            f"---\n{debate_excerpt}\n---"
        )

        return {
            "success": True,
            "decision": "replan",
            "triggered_by": "orchestrator",
            "reason": deadlock_reason,
            "rounds": CONFIG.MAX_CHALLENGE_ROUNDS,
        }
    

    def _parse_trace_back_step(self, text: str, current_step: int) -> int | None:
        """Strict parser for the verifier's ``## TRACE_BACK_TO`` field.

        Returns ``None`` if the field is absent or out of range; callers
        should treat ``None`` as PROPOSE_REPLAN.
        """
        return _parse_verifier_trace_back_to(text, current_step)
