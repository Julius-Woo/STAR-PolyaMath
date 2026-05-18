"""
Main solve loop for Orchestrator.
"""

from __future__ import annotations

from typing import Any

from src.state import ProblemStatus
from src.agents import reasoner_write_solution, verifier_review_report
from src.agent_runner import get_agent_stats, reset_agent_stats
from src.config import CONFIG

class OrchestratorSolveMixin:
    """Main solve loop for Orchestrator."""

    def solve(self, problem: str) -> dict[str, Any]:
        """
        Main entry point: solve a problem.
        
        Flow:
        0. Get strategic suggestions from Meta-Strategist (Pre-Planning)
        0.5. Explore problem (small cases, patterns, conjectures)
        1. Set problem
        2. Request plan (with exploration insights + Meta-Strategist suggestions)
        3. For each step:
           a. Execute step
           b. Verify
           c. Challenge if needed (with Meta-Strategist mediation if deadlock)
           d. Advance or trace back
           e. If Step 1 repeatedly fails → Re-plan
        4. Generate solution
        """
        import time
        
        self.log(f"Starting problem: {self.problem_id}")
        
        # Reset agent stats for this solve session
        reset_agent_stats()
        
        # Record start time
        self.state.start_time = time.time()
        self.state.save()
        
        # Initialize
        self.state.set_problem(problem)
        
        # Phase 0: Get Meta-Strategist suggestions (non-blocking)
        self.get_meta_suggestions()

        # Pre-plan Exploration phase.
        if CONFIG.ENABLE_EXPLORATION:
            for _ in range(CONFIG.MAX_EXPLORATION_ROUNDS):
                round_result = self.run_exploration_round()
                if not round_result.get("success"):
                    self.log("Exploration round failed; archiving and proceeding to plan")
                    self.archive_exploration_round()
                    break

                assessment = round_result.get("assessment")
                if assessment == "SOLVED":
                    solved = self.try_solve_via_exploration(round_result["findings"])
                    if solved.get("success"):
                        # Wrap up identically to the plan-path completion below.
                        import time as _t
                        self.state.end_time = _t.time()
                        # Merge agent stats
                        stats = get_agent_stats()
                        self.state.reasoner_calls += stats.reasoner_calls
                        self.state.verifier_calls += stats.verifier_calls
                        self.state.meta_strategist_calls += stats.meta_strategist_calls
                        self.state.total_input_tokens += stats.total_input_tokens
                        self.state.total_output_tokens += stats.total_output_tokens
                        self.state.total_cached_tokens += stats.total_cached_tokens
                        self.state.save()
                        stats_path = self.state.write_stats()
                        self.log(f"Solved via exploration; statistics: {stats_path}")
                        return {
                            "success": True,
                            "status": "completed",
                            "solution_path": solved["solution_path"],
                            "stats_path": str(stats_path),
                            "via": "exploration",
                        }
                    # Fall-through: rejection, treat findings as a failed plan
                    self.record_exploration_rejection(solved.get("challenge_text", ""))
                    self.archive_exploration_round()
                    break

                if assessment == "PARTIALLY_SOLVED":
                    verdict = self.review_exploration()
                    self.archive_exploration_round()
                    if verdict == "retry":
                        continue
                    break  # proceed to plan

                # NEED_PLAN or UNKNOWN
                self.archive_exploration_round()
                break

        # Outer loop for re-planning
        while True:
            # Phase 1: Planning (with Meta-Strategist suggestions)
            force_new = self.state.replan_count > 0
            plan_result = self.request_plan(force_new_approach=force_new)
            if not plan_result.get("success"):
                return plan_result
            
            # Phase 2-4: Step execution loop
            need_replan = False
            need_abort: dict[str, Any] | None = None

            def _apply_replan_decision(
                decision_result: dict[str, Any],
                step_no: int,
            ) -> str:
                """Apply meta replan verdict; return loop control: 'replan' | 'trace_back' | 'continue' | 'abort'."""
                nonlocal need_replan, need_abort
                d = decision_result.get("decision", "continue")
                reason = decision_result.get("reason_summary", "") or ""
                fbd = decision_result.get("forbidden_directions") or []
                if d == "replan":
                    plan_sum = decision_result.get("plan_summary") or f"Plan v{self.state.replan_count + 1} abandoned"
                    ok = self.state.initiate_replan(
                        triggered_by="meta",
                        summary=plan_sum,
                        reason=reason,
                        forbidden_directions=fbd,
                    )
                    if not ok:
                        # MAX_REPLANS exhausted; treat as forced ABORT.
                        self.log(
                            f"Meta-Strategist approved re-plan but MAX_REPLANS="
                            f"{CONFIG.MAX_REPLANS} reached — forcing ABORT."
                        )
                        self.state.status = ProblemStatus.FAILED
                        self.state.save()
                        partial_path = self.generate_partial_solution()
                        need_abort = {
                            "success": False,
                            "status": "failed",
                            "reason": (
                                f"MAX_REPLANS={CONFIG.MAX_REPLANS} reached after "
                                f"Meta-Strategist approved another re-plan; root cause: "
                                f"{reason or 'unknown'}."
                            ),
                            "partial_solution": str(partial_path),
                        }
                        return "abort"
                    self.log(f"Meta-Strategist approved re-plan #{self.state.replan_count}")

                    # After re-plan, optionally re-enter
                    # exploration before generating a new plan. The replan
                    # signal usually means the prior plan's underlying
                    # conjecture was wrong; jumping straight to plan would
                    # let the new plan re-anchor on the same wrong target.
                    self._run_post_replan_reexploration_if_enabled(
                        forbidden_directions=fbd,
                    )

                    need_replan = True
                    return "replan"
                if d == "trace_back":
                    trace_to = decision_result.get("trace_back_to") or max(1, step_no - 1)
                    self.state.trace_back_to(
                        trace_to,
                        triggered_by="meta",
                        reason=reason,
                        forbidden_directions=fbd,
                    )
                    self.log(f"Meta-Strategist directed trace-back to step {trace_to}")
                    return "trace_back"
                if d == "abort":
                    self.log(f"Meta-Strategist ABORT: {reason}")
                    self.state.status = ProblemStatus.FAILED
                    self.state.save()
                    partial_path = self.generate_partial_solution()
                    need_abort = {
                        "success": False,
                        "status": "failed",
                        "reason": reason or "Meta-Strategist aborted further work.",
                        "partial_solution": str(partial_path),
                    }
                    return "abort"
                # CONTINUE / failed / unknown: keep going on current plan
                self.log(f"Meta-Strategist declined re-plan ({d}); continuing.")
                return "continue"

            while self.state.current_step <= self.state.total_steps:
                step = self.state.get_current_step()
                self.log(f"\n{'='*50}")
                self.log(f"Step {step.step_number}/{self.state.total_steps}: {step.title}")
                self.log(f"{'='*50}\n")
                
                # Execute
                exec_result = self.execute_current_step()
                if not exec_result.get("success"):
                    # Meta said trace_back during timeout retries.
                    # Apply directly without burning more retries.
                    if exec_result.get("meta_trace_back"):
                        trace_to = exec_result.get("trace_back_to") or max(1, step.step_number - 1)
                        reason = exec_result.get("meta_reason") or exec_result.get("error", "")
                        self.log(
                            f"Meta-Strategist trace_back during timeout: → step {trace_to}"
                        )
                        self.state.trace_back_to(
                            trace_to,
                            triggered_by="meta",
                            reason=reason[:1000],
                        )
                        continue

                    # Chronic step-execution timeouts
                    # are a stalemate signal — let Meta-Strategist decide
                    # re-plan / trace-back / abort instead of failing outright.
                    if exec_result.get("timeout_exhausted"):
                        # Short-circuit: if Meta already returned `replan` in
                        # `diagnose_and_guide`, skip the second `decide_replan`
                        # call (same persistent meta session, identical context).
                        if exec_result.get("meta_action") == "replan":
                            self.log(
                                f"Step {step.step_number}: Meta short-circuited to APPROVE_REPLAN "
                                "during timeout diagnose; applying directly."
                            )
                            short_circuit = {
                                "decision": "replan",
                                "reason_summary": exec_result.get("meta_reason", "")[:1000],
                                "plan_summary": f"Plan v{self.state.replan_count + 1} abandoned (chronic timeout)",
                                "forbidden_directions": [],
                            }
                            ctl = _apply_replan_decision(short_circuit, step.step_number)
                            if ctl == "replan":
                                break
                            if ctl == "abort":
                                return need_abort
                            # Should not happen for a synthetic replan dict.
                            continue
                        self.log(
                            f"Step {step.step_number} timeout retries exhausted "
                            "— escalating to Meta-Strategist for re-plan decision."
                        )
                        decision_result = self._request_replan_decision(
                            proposer="orchestrator",
                            proposer_reason=(
                                f"Step {step.step_number} reasoner exhausted all "
                                f"timeout retries: {exec_result.get('error', 'unknown')}. "
                                "Likely the planned attack is computationally "
                                "infeasible within the step budget."
                            ),
                            current_step_number=step.step_number,
                        )
                        ctl = _apply_replan_decision(decision_result, step.step_number)
                        if ctl == "replan":
                            break
                        if ctl == "abort":
                            return need_abort
                        if ctl == "trace_back":
                            continue
                        # CONTINUE from meta on a timeout_exhausted is
                        # nonsensical (we have no completed step); treat as
                        # ABORT to avoid spinning.
                        self.log("Meta declined re-plan after timeout exhaustion; forcing ABORT.")
                        self.state.status = ProblemStatus.FAILED
                        self.state.save()
                        partial_path = self.generate_partial_solution()
                        return {
                            "success": False,
                            "status": "failed",
                            "reason": (
                                f"Step {step.step_number} timeout retries exhausted "
                                "and Meta-Strategist declined to re-plan."
                            ),
                            "partial_solution": str(partial_path),
                        }
                    return exec_result

                # Trigger 2: reasoner [plan-blocked:...] tag.
                report_text = exec_result.get("report", "") or ""
                from src.utils.extract import extract_replan_proposal
                blocked_reason = extract_replan_proposal(report_text)
                if blocked_reason:
                    self.log(f"Reasoner emitted [plan-blocked]: {blocked_reason[:120]}")
                    decision_result = self._request_replan_decision(
                        proposer="reasoner",
                        proposer_reason=blocked_reason,
                        current_step_number=step.step_number,
                    )
                    ctl = _apply_replan_decision(decision_result, step.step_number)
                    if ctl == "replan":
                        break
                    if ctl == "abort":
                        return need_abort
                    if ctl == "trace_back":
                        continue  # loop will re-pick current_step
                    # continue: fall through to verifier (proceeds normally)
                
                # Verify
                verify_result = self.verify_current_step()
                if not verify_result.get("success"):
                    return verify_result
                
                decision = verify_result.get("decision")
                
                if decision == "accept":
                    # Advance to next step
                    if not self.state.advance_step():
                        break  # All steps done
                
                elif decision == "propose_replan":
                    # Trigger 1: verifier escalates to a re-plan request.
                    decision_result = self._request_replan_decision(
                        proposer="verifier",
                        proposer_reason=verify_result.get("reason", ""),
                        current_step_number=step.step_number,
                    )
                    ctl = _apply_replan_decision(decision_result, step.step_number)
                    if ctl == "replan":
                        break
                    if ctl == "abort":
                        return need_abort
                    if ctl == "trace_back":
                        continue
                    # CONTINUE: meta said proposer was wrong; fall through to challenge.
                    self.log("Meta said CONTINUE; treating verifier verdict as challenge.")
                    decision = "challenge"

                if decision == "challenge":
                    # Run challenge loop
                    challenge_result = self.run_challenge_loop()
                    
                    if challenge_result.get("decision") == "accept":
                        if not self.state.advance_step():
                            break
                    
                    elif challenge_result.get("decision") == "trace_back":
                        trace_to = challenge_result.get("trace_back_to", max(1, step.step_number - 1))
                        triggered_by = challenge_result.get("triggered_by", "verifier")
                        reason = challenge_result.get("reason", "")
                        self.log(f"Tracing back to step {trace_to} (by {triggered_by})")
                        self.state.trace_back_to(
                            trace_to,
                            triggered_by=triggered_by,
                            reason=reason,
                        )
                    
                    elif challenge_result.get("decision") == "replan":
                        # Meta said `replan` during Diagnose_and_guide, or the
                        # reasoner emitted [plan-blocked] in a challenge round.
                        decision_result = self._request_replan_decision(
                            proposer=challenge_result.get("triggered_by", "orchestrator"),
                            proposer_reason=challenge_result.get("reason", "replan proposed during challenge"),
                            current_step_number=step.step_number,
                        )
                        ctl = _apply_replan_decision(decision_result, step.step_number)
                        if ctl == "replan":
                            break
                        if ctl == "abort":
                            return need_abort
                        # trace_back / continue: fall through
                    
                    elif challenge_result.get("decision") == "failed":
                        self.log(f"Step {step.step_number} failed: {challenge_result.get('reason', 'unknown')}")
                        self.state.status = ProblemStatus.FAILED
                        self.state.save()
                        partial_path = self.generate_partial_solution()
                        return {
                            "success": False,
                            "status": "failed",
                            "reason": challenge_result.get("reason", "Challenge loop exhausted"),
                            "partial_solution": str(partial_path),
                        }
                
                elif decision == "trace_back":
                    # `_run_verifier` only emits decision == "trace_back" when
                    # a structured TRACE_BACK_TO step number was parsed and
                    # is < current_step. Ambiguous trace-backs have already
                    # been converted to propose_replan or challenge.
                    trace_to = verify_result.get("trace_back_to")
                    if not isinstance(trace_to, int) or trace_to < 1:
                        # Defensive: should not happen given _run_verifier's
                        # contract; surface as propose_replan rather than
                        # silently guessing a step.
                        self.log(
                            "Verifier returned trace_back without trace_back_to; "
                            "escalating as propose_replan."
                        )
                        decision_result = self._request_replan_decision(
                            proposer="verifier",
                            proposer_reason=verify_result.get("reason", "unspecified trace-back"),
                            current_step_number=step.step_number,
                        )
                        ctl = _apply_replan_decision(decision_result, step.step_number)
                        if ctl == "replan":
                            break
                        if ctl == "abort":
                            return need_abort
                        continue
                    triggered_by = verify_result.get("triggered_by", "verifier")
                    reason = verify_result.get("reason") or verify_result.get("verification", "")
                    self.log(f"Tracing back to step {trace_to} (by {triggered_by})")
                    self.state.trace_back_to(
                        trace_to,
                        triggered_by=triggered_by,
                        reason=reason,
                    )

            if need_abort:
                return need_abort

            # Check if we need to re-plan
            if need_replan:
                self.log("Re-planning triggered, starting new plan...")
                continue

            # If we completed all steps, exit loop
            if self.state.status == ProblemStatus.COMPLETED or self.state.current_step > self.state.total_steps:
                break
        
        # Generate final solution
        self.log("\n" + "="*50)
        self.log("All steps completed! Generating solution...")
        self.log("="*50 + "\n")
        self.state.set_phase("writing_solution")
        
        # Ask Reasoner to write final solution (with retry on failure)
        problem = self.state.get_problem()
        completed = self.state.get_completed_steps_context()
        
        # Error patterns that indicate a failed/incomplete response
        error_patterns = [
            "Execution failed:",
            "Stream completed without",
            "Error:",
            "response.completed event",
        ]
        
        def is_valid_solution(response: str) -> bool:
            """Check if response is a valid solution (not error or too short)."""
            if not response or len(response.strip()) < 50:
                return False
            # Check for error patterns at the end of response (last 200 chars)
            response_tail = response[-200:] if len(response) > 200 else response
            for pattern in error_patterns:
                if pattern in response_tail:
                    return False
            return True
        
        solution = None
        # Write_solution joins the shared reasoner session.
        reasoner_session_id = self.state.reasoner_session_name or self.state.derived_reasoner_session_name()
        for attempt in range(CONFIG.MAX_SOLUTION_RETRIES):
            solution_result = reasoner_write_solution(
                problem=problem,
                completed_steps=completed,
                problem_id=self.problem_id,
                session_id=reasoner_session_id,
                transcript_path=str(self.state.base_dir / "jsonl" / (f"reason-solution-r{attempt}.jsonl" if attempt > 0 else "reason-solution.jsonl")),
            )

            if solution_result.get("success"):
                response = solution_result.get("response", "")
                if is_valid_solution(response):
                    # Verifier integral review of the
                    # produced solution to catch cross-problem contamination
                    # and answer-mismatch (write_solution sometimes browses
                    # the filesystem and pastes a sibling problem's answer).
                    self.state.set_phase("verifying_solution")
                    verify_transcript = str(
                        self.state.base_dir / "jsonl"
                        / (f"verify-solution-r{attempt}.jsonl" if attempt > 0 else "verify-solution.jsonl")
                    )
                    sv = verifier_review_report(
                        problem=self.state.get_problem() or "",
                        completed_steps=self.state.get_completed_steps_context(),
                        report=response,
                        step_number=0,  # 0 = whole-solution review
                        code_dir=str(self.state.code_dir),
                        transcript_path=verify_transcript,
                        intervention_history=self.state.meta_intervention_history,
                        session_id=None,  # fresh, one-shot
                    )
                    if not sv.get("success"):
                        self.log(
                            f"Solution attempt {attempt + 1}: verifier call failed "
                            f"({sv.get('error', 'unknown')}); accepting solution without verification."
                        )
                        solution = response
                        break
                    sv_text = sv.get("response", "") or ""
                    sv_decision = self._parse_verifier_decision(sv_text)
                    # Save verifier's whole-solution review for transparency.
                    (self.state.base_dir / f"solution-verify-r{attempt}.md").write_text(
                        sv_text, encoding="utf-8"
                    )
                    if sv_decision == "accept":
                        self.log(f"Solution attempt {attempt + 1}: verifier ACCEPT.")
                        solution = response
                        break
                    self.log(
                        f"Solution attempt {attempt + 1}: verifier {sv_decision.upper()} — "
                        "retrying solution write with explicit feedback."
                    )
                    self.state.set_phase("writing_solution")
                    # Continue retry loop — the next iteration re-invokes
                    # reasoner_write_solution. The reasoner sees the verifier
                    # rejection in PROBLEM_STATE injection and its own
                    # session transcript (fresh user message will follow).
                else:
                    self.log(f"Solution attempt {attempt + 1} invalid (error pattern or too short), retrying...")
            else:
                self.log(f"Solution attempt {attempt + 1} failed: {solution_result.get('error', 'unknown')}, retrying...")
        
        # Fallback to completed steps if all retries failed
        if not solution:
            self.log("WARNING: All solution attempts failed, using completed steps as solution")
            solution = completed
        
        solution_path = self.state.save_solution(solution)
        
        # Record end time and write statistics
        import time
        self.state.end_time = time.time()
        
        # Merge agent stats into state (accumulate, don't overwrite for resume support)
        stats = get_agent_stats()
        self.state.reasoner_calls += stats.reasoner_calls
        self.state.verifier_calls += stats.verifier_calls
        self.state.meta_strategist_calls += stats.meta_strategist_calls
        self.state.total_input_tokens += stats.total_input_tokens
        self.state.total_output_tokens += stats.total_output_tokens
        self.state.total_cached_tokens += stats.total_cached_tokens
        
        self.state.save()
        
        stats_path = self.state.write_stats()
        self.log(f"Statistics written to: {stats_path}")
        
        return {
            "success": True,
            "status": "completed",
            "solution_path": str(solution_path),
            "stats_path": str(stats_path),
        }
