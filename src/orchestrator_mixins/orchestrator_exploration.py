"""
Pre-plan Exploration phase.

Drives one or more Reasoner exploration rounds before plan generation:
- SOLVED → polish solution + verifier integral review (single shot, no
  step-level granularity); ACCEPT → done; CHALLENGE → fall through to plan
  with findings preserved.
- PARTIALLY_SOLVED → ask Meta whether to retry exploration or proceed.
- NEED_PLAN → directly proceed to plan with findings injected.

The mixin is small on purpose: it does not own the solve-loop control flow
(that stays in `orchestrator_solve.py`); it exposes round-level operations
that the solve loop composes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.config import CONFIG
from src.state import ProblemStatus
from src.agents import (
    meta_strategist_orchestrate_reexplore,
    meta_strategist_review_exploration,
    reasoner_explore,
    reasoner_polish_exploration_solution,
    verifier_review_report,
)
from src.utils.extract import parse_exploration_md


class OrchestratorExplorationMixin:
    """Exploration phase driver."""

    # ---------- Single round ----------

    def run_exploration_round(self) -> dict[str, Any]:
        """Run one exploration round; return a dict including parsed findings.

        Returns:
            {
                "success": bool,
                "assessment": "SOLVED" | "PARTIALLY_SOLVED" | "NEED_PLAN" | "UNKNOWN",
                "findings": dict (parsed exploration.md, see parse_exploration_md),
                "session_id": str (the explore session name used),
                "error": str (on failure),
            }
        """
        self.state.exploration_attempt += 1
        attempt = self.state.exploration_attempt
        self.state.set_phase("exploration")
        self.log(f"\n{'='*50}\nExploration round {attempt}\n{'='*50}")

        problem = self.state.get_problem()
        if not problem:
            return {"success": False, "error": "No problem set"}

        # Each round is a fresh explore session: deterministic name carries
        # the attempt index. Treats prior rounds as upstream context (already
        # rendered into PROBLEM_STATE.md as `## Exploration Findings`).
        session_name = self.state.derived_explore_session_name()
        transcript_path = str(
            self.state.base_dir / "jsonl" / f"explore-r{attempt}.jsonl"
        )

        result = reasoner_explore(
            problem=problem,
            attempt=attempt,
            budget_min=CONFIG.EXPLORATION_BUDGET_MIN,
            code_dir=str(self.state.code_dir),
            problem_id=self.problem_id,
            session_id=session_name,
            transcript_path=transcript_path,
        )

        if not result.get("success"):
            self.log(f"Exploration round {attempt} failed: {result.get('error')}")
            return {
                "success": False,
                "assessment": "UNKNOWN",
                "findings": {},
                "session_id": session_name,
                "error": result.get("error"),
            }

        # Parse the deliverable. If the file is missing (reasoner forgot),
        # fall back to parsing the raw response text.
        deliv = self.state.exploration_file
        if deliv.exists():
            text = deliv.read_text(encoding="utf-8")
            source = "file"
        else:
            text = result.get("response", "") or ""
            source = "response"
            self.log(
                f"Exploration round {attempt}: deliverable file missing, "
                f"parsing from response text instead"
            )

        findings = parse_exploration_md(text)
        findings["round"] = attempt
        findings["source"] = source
        # Persist on state and re-render PROBLEM_STATE so subsequent phases
        # (further explore rounds, plan, write_solution) see the findings.
        self.state.exploration_findings = findings
        self.state.save()
        self.state.write_problem_state_md()

        self.log(
            f"Exploration round {attempt} parsed: "
            f"assessment={findings.get('assessment')}, "
            f"worked={len(findings.get('worked', []))}, "
            f"failed={len(findings.get('failed', []))}, "
            f"candidates={len(findings.get('candidates', []))}"
        )

        return {
            "success": True,
            "assessment": findings.get("assessment", "UNKNOWN"),
            "findings": findings,
            "session_id": session_name,
        }

    # ---------- PARTIALLY_SOLVED review ----------

    def review_exploration(self) -> str:
        """Ask meta whether to retry exploration or proceed to plan.

        Returns "retry" | "proceed". Forces "proceed" if attempt >= max.
        """
        attempt = self.state.exploration_attempt
        if attempt >= CONFIG.MAX_EXPLORATION_ROUNDS:
            self.log(
                f"Exploration round {attempt}/{CONFIG.MAX_EXPLORATION_ROUNDS}: "
                f"max rounds reached, forcing PROCEED_TO_PLAN"
            )
            return "proceed"

        problem = self.state.get_problem() or ""
        findings = self.state.exploration_findings or {}
        # Inline a compact rendering of findings for the meta prompt
        # (PROBLEM_STATE.md is also injected via sessionStart hook on new
        # meta sessions, but include it here for resumed sessions too).
        f_lines: list[str] = []
        for label, key in (
            ("Tried", "tried"),
            ("Worked", "worked"),
            ("Failed", "failed"),
            ("Candidate Approaches", "candidates"),
        ):
            items = findings.get(key) or []
            if items:
                f_lines.append(f"### {label}")
                f_lines.extend(f"- {it}" for it in items)
        summary = "\n".join(f_lines) or "(empty)"

        meta_session = (
            self.state.meta_strategist_session_name
            or self.state.derived_meta_session_name()
        )
        result = meta_strategist_review_exploration(
            problem=problem,
            findings_summary=summary,
            attempt=attempt,
            max_rounds=CONFIG.MAX_EXPLORATION_ROUNDS,
            session_id=meta_session,
            transcript_path=str(
                self.state.base_dir / "jsonl" / f"meta-explore-review-r{attempt}.jsonl"
            ),
        )
        if result.get("success") and self.state.meta_strategist_session_name is None:
            self.state.meta_strategist_session_name = self.state.derived_meta_session_name()
            self.state.save()

        if not result.get("success"):
            self.log(f"Meta review_exploration failed: {result.get('error')} → PROCEED_TO_PLAN")
            return "proceed"
        verdict = (result.get("explore_decision") or "").upper()
        self.log(f"Meta exploration review: {verdict or '(unparsed)'}")
        if verdict == "RETRY_EXPLORE":
            return "retry"
        return "proceed"

    # ---------- SOLVED path ----------

    def try_solve_via_exploration(
        self, findings: dict[str, Any]
    ) -> dict[str, Any]:
        """SOLVED branch: polish solution + verifier integral review.

        Returns one of:
          {"success": True, "status": "completed", "solution_path": ...}
              → done, the outer solve() should return this.
          {"success": False, "fall_through_to_plan": True, "challenge_text": ...}
              → caller should record an `exploration_rejected` failure record
                and continue to plan phase.
        """
        attempt = self.state.exploration_attempt
        self.log(f"Exploration round {attempt}: SOLVED → polishing + verifying")

        # 1. Polish solution.md (continue same explore session).
        self.state.set_phase("writing_solution")
        explore_session = self.state.derived_explore_session_name()
        polish_transcript = str(
            self.state.base_dir / "jsonl" / f"explore-r{attempt}-polish.jsonl"
        )
        polish_result = reasoner_polish_exploration_solution(
            problem=self.state.get_problem() or "",
            solution_sketch=findings.get("solution_sketch", "") if isinstance(findings, dict) else "",
            problem_id=self.problem_id,
            session_id=explore_session,
            transcript_path=polish_transcript,
        )
        if not polish_result.get("success"):
            self.log(f"Polish failed: {polish_result.get('error')}")
            return {
                "success": False,
                "fall_through_to_plan": True,
                "challenge_text": f"Polish step failed: {polish_result.get('error')}",
            }

        # Locate the solution file. The reasoner is expected to write it,
        # but fall back to the response text if it didn't.
        solution_path = self.state.solution_file
        if solution_path.exists():
            solution_text = solution_path.read_text(encoding="utf-8")
        else:
            solution_text = polish_result.get("response", "") or findings.get("solution_sketch", "")
            if solution_text:
                self.state.save_solution(solution_text)
                solution_text = self.state.solution_file.read_text(encoding="utf-8")
            else:
                self.log("No solution text produced; falling through to plan")
                return {
                    "success": False,
                    "fall_through_to_plan": True,
                    "challenge_text": "Reasoner produced no solution text.",
                }

        # 2. Verifier integral review of the whole solution.
        self.state.set_phase("verifying_solution")
        # Fresh per-attempt verifier session; we reuse the make_session_name
        # API with a synthetic step number = 0 to keep it deterministic.
        verify_transcript = str(
            self.state.base_dir / "jsonl" / f"verify-explore-r{attempt}.jsonl"
        )
        verify_result = verifier_review_report(
            problem=self.state.get_problem() or "",
            completed_steps="(no steps; SOLVED via exploration)",
            report=solution_text,
            step_number=0,  # 0 = "whole-solution review", not a real step
            code_dir=str(self.state.code_dir),
            transcript_path=verify_transcript,
            intervention_history=self.state.meta_intervention_history,
            session_id=None,  # one-shot, fresh
        )
        if not verify_result.get("success"):
            self.log(f"Verifier failed during integral review: {verify_result.get('error')}")
            return {
                "success": False,
                "fall_through_to_plan": True,
                "challenge_text": f"Verifier call failed: {verify_result.get('error')}",
            }

        verification = verify_result.get("response", "") or ""
        decision = self._parse_verifier_decision(verification)
        self.log(f"Verifier integral verdict: {decision}")

        # Save verify text for transparency
        (self.state.base_dir / f"exploration-v{attempt}-verify.md").write_text(
            verification, encoding="utf-8"
        )

        if decision == "accept":
            # All good — finalize.
            self.state.status = ProblemStatus.COMPLETED
            self.state.set_phase("completed")
            return {
                "success": True,
                "status": "completed",
                "solution_path": str(self.state.solution_file),
            }

        # CHALLENGE / TRACE_BACK / PROPOSE_REPLAN → fall through to plan.
        from src.utils.extract import extract_section_paragraph
        reason = extract_section_paragraph(verification, "Reason") or verification[:500]
        return {
            "success": False,
            "fall_through_to_plan": True,
            "challenge_text": reason,
        }

    # ---------- Fall-through bookkeeping ----------

    def archive_exploration_round(self) -> None:
        """Move current exploration.md to archive, leaving findings in state."""
        self.state.archive_exploration()

    def record_exploration_rejection(self, challenge_text: str) -> None:
        """SOLVED → CHALLENGE or polish failure: record as failed_records entry.

        Verified observations and forbidden directions are already in
        `exploration_findings`; they remain rendered in PROBLEM_STATE.md.
        Here we add a failure record so plan-phase prompts see the rejection
        reason as "Confirmed Failures".
        """
        from src.state import FailureRecord

        attempt = self.state.exploration_attempt
        findings = self.state.exploration_findings or {}
        sketch = findings.get("solution_sketch", "")
        rec = FailureRecord(
            kind="replan",  # reuse "replan" kind so existing rendering works
            plan_version=self.state.replan_count + 1,
            step_number=None,
            triggered_by="verifier",
            summary=f"Exploration round {attempt} SOLVED solution rejected by integral verifier",
            reason=(challenge_text or "(no reason)").strip()[:1000],
            forbidden_directions=[],
            rescued_results=[
                f"(Exploration v{attempt} sketch — rejected)\n{sketch.strip()}"
            ] if sketch else [],
        )
        self.state.failed_records.append(rec)
        self.state.save()
        self.state.write_problem_state_md()

    # ---------- Post-replan re-exploration ----------

    def _run_post_replan_reexploration_if_enabled(
        self,
        *,
        forbidden_directions: list[str],
    ) -> None:
        """After APPROVE_REPLAN, optionally run a meta-orchestrated re-explore.

        Skips silently when:
          - CONFIG.ENABLE_EXPLORATION is False,
          - or `state.exploration_attempt >= CONFIG.MAX_EXPLORATION_ROUNDS`
            (cumulative budget exhausted; prefer plan over more explore).

        Otherwise asks Meta-Strategist to fan out N parallel sub-agents (via
        the `task` tool inside meta's session). Synthesised findings are
        written to `state.exploration_findings`; `exploration_attempt` is
        bumped by the number of sub-agents requested. PROBLEM_STATE.md is
        re-rendered so the next plan attempt sees the new findings.
        """
        if not CONFIG.ENABLE_EXPLORATION:
            return
        remaining = CONFIG.MAX_EXPLORATION_ROUNDS - self.state.exploration_attempt
        if remaining <= 0:
            self.log(
                "Post-replan re-explore skipped: exploration budget exhausted "
                f"({self.state.exploration_attempt}/{CONFIG.MAX_EXPLORATION_ROUNDS})."
            )
            return

        n_hints = max(1, min(3, remaining))
        self.log(
            f"Post-replan re-explore: meta orchestrating {n_hints} parallel "
            f"sub-agent(s) (budget remaining: {remaining})."
        )
        self.state.set_phase("exploration")

        # Render prior findings + failed_records in compact form for the
        # meta prompt.
        prior = self.state.exploration_findings or {}
        prior_lines: list[str] = []
        if prior:
            prior_lines.append(
                f"Prior round assessment: {prior.get('assessment', 'UNKNOWN')}"
            )
            for label, key in (
                ("Worked", "worked"),
                ("Failed", "failed"),
                ("Candidates", "candidates"),
            ):
                items = prior.get(key) or []
                if items:
                    prior_lines.append(f"{label}:")
                    prior_lines.extend(f"  - {it}" for it in items)
        prior_summary = "\n".join(prior_lines) or "(no prior exploration findings)"

        failed_summary = self._render_failed_records_for_meta()

        meta_session = (
            self.state.meta_strategist_session_name
            or self.state.derived_meta_session_name()
        )
        result = meta_strategist_orchestrate_reexplore(
            problem=self.state.get_problem() or "",
            forbidden_directions=list(forbidden_directions or []),
            prior_findings_summary=prior_summary,
            failed_records_summary=failed_summary,
            n_hints=n_hints,
            session_id=meta_session,
            transcript_path=str(
                self.state.base_dir
                / "jsonl"
                / f"meta-reexplore-r{self.state.replan_count}.jsonl"
            ),
        )
        if result.get("success") and self.state.meta_strategist_session_name is None:
            self.state.meta_strategist_session_name = self.state.derived_meta_session_name()
            self.state.save()

        if not result.get("success"):
            self.log(f"Meta orchestrate_reexplore failed: {result.get('error')} — skipping")
            return

        # Bump exploration_attempt by n_hints (each sub-agent counts).
        self.state.exploration_attempt += n_hints

        # Synthesise into exploration_findings shape compatible with the
        # rest of the pipeline.
        synth = {
            "tried": [],  # we don't track raw 'tried' across sub-agents
            "worked": list(result.get("synth_worked") or []),
            "failed": list(result.get("synth_failed") or []),
            "candidates": list(result.get("synth_candidates") or []),
            "assessment": result.get("synth_assessment") or "NEED_PLAN",
            "solution_sketch": result.get("synth_solution_sketch") or "",
            "raw": result.get("response", "") or "",
            "parse_ok": bool(result.get("synth_assessment")),
            "round": self.state.exploration_attempt,
            "source": "meta-orchestrated",
        }
        self.state.exploration_findings = synth
        self.state.save()
        self.state.write_problem_state_md()
        self.log(
            f"Post-replan re-explore done: assessment={synth['assessment']}, "
            f"worked={len(synth['worked'])}, candidates={len(synth['candidates'])}"
        )
