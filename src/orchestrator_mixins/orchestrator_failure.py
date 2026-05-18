"""
Failure handling behaviors for Orchestrator.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.state import StepStatus
from src.agents import meta_strategist_decide_replan

class OrchestratorFailureMixin:
    """Failure handling behaviors for Orchestrator."""

    def generate_partial_solution(self) -> Path:
        """Generate partial solution with verified results and conjectures."""
        parts = []
        parts.append("# Partial Solution\n")
        parts.append(f"Problem: {self.problem_id}\n")
        parts.append(f"Status: Incomplete (failed)\n\n")
        
        # Verified Results
        parts.append("## Verified Results\n\n")
        for i in range(1, self.state.total_steps + 1):
            step = self.state.steps.get(i)
            if step and step.status == StepStatus.VERIFIED:
                verify_file = self.state.step_verify_file(i)
                if verify_file.exists():
                    verify_content = verify_file.read_text(encoding="utf-8")
                    verified = self.state._extract_verified_results(verify_content)
                    if verified:
                        parts.append(f"### Step {i}: {step.title}\n{verified}\n\n")
        
        # Failed Steps
        parts.append("## Incomplete Steps\n\n")
        for i in range(1, self.state.total_steps + 1):
            step = self.state.steps.get(i)
            if step and step.status != StepStatus.VERIFIED:
                parts.append(f"- Step {i}: {step.title} (status: {step.status.value})\n")
        
        # Save
        content = "".join(parts)
        partial_file = self.state.base_dir / "partial_solution.md"
        partial_file.write_text(content, encoding="utf-8")
        self.log(f"Partial solution saved: {partial_file}")
        
        return partial_file

    # ========== Re-plan Decision ==========

    def _render_failed_records_for_meta(self) -> str:
        """Render `state.failed_records` as a compact list for meta context."""
        records = self.state.failed_records
        if not records:
            return ""
        lines: list[str] = []
        for r in records:
            head = (
                f"- Plan v{r.plan_version} {r.kind} (by {r.triggered_by}): "
                f"{r.summary}"
            )
            lines.append(head)
            if r.reason:
                lines.append(f"    reason: {r.reason}")
            for d in r.forbidden_directions:
                lines.append(f"    forbidden: {d}")
        return "\n".join(lines)

    def _request_replan_decision(
        self,
        *,
        proposer: str,                # "verifier" | "reasoner" | "orchestrator"
        proposer_reason: str,
        current_step_number: int,
    ) -> dict[str, Any]:
        """Ask Meta-Strategist to authorize / reject a re-plan request.

        unified entry for all three triggers (verifier
        PROPOSE_REPLAN, reasoner [plan-blocked], orchestrator escalation).

        Returns a dict with keys:
          - decision: "replan" | "continue" | "trace_back" | "abort" | "failed"
          - reason_summary: str (brief)
          - plan_summary: str (replan only)
          - forbidden_directions: list[str]
          - rescued_results: list[str]
          - trace_back_to: int | None (trace_back only)
          - failed: True if call failed
        """
        problem = self.state.get_problem() or ""
        meta_session = (
            self.state.meta_strategist_session_name
            or self.state.derived_meta_session_name()
        )
        result = meta_strategist_decide_replan(
            problem=problem,
            proposer=proposer,
            proposer_reason=proposer_reason or "",
            current_step_number=current_step_number,
            failed_records_summary=self._render_failed_records_for_meta(),
            session_id=meta_session,
            transcript_path=str(
                self.state.base_dir
                / "jsonl"
                / f"meta-replan-step{current_step_number:02d}-r{len(self.state.failed_records)}.jsonl"
            ),
        )
        # Persist meta session name on first success.
        if result.get("success") and self.state.meta_strategist_session_name is None:
            self.state.meta_strategist_session_name = self.state.derived_meta_session_name()
            self.state.save()

        if not result.get("success"):
            self.log(f"Meta-Strategist replan decision failed: {result.get('error')}")
            return {"decision": "failed", "failed": True}

        verdict = (result.get("replan_decision") or "").upper()
        reason = result.get("reason_summary", "") or proposer_reason
        plan_sum = result.get("plan_summary", "")
        fbd = list(result.get("forbidden_directions") or [])
        reuse = list(result.get("reusable_results") or [])
        tb = result.get("trace_back_to")

        if verdict == "APPROVE_REPLAN":
            d = "replan"
        elif verdict == "TRACE_BACK_TO":
            d = "trace_back"
            if not isinstance(tb, int) or tb < 1:
                tb = max(1, current_step_number - 1)
        elif verdict == "ABORT":
            d = "abort"
        else:
            d = "continue"

        self.log(
            f"Meta-Strategist replan verdict: {verdict or '(unparsed)'} "
            f"-> {d}; forbidden={len(fbd)}, reusable={len(reuse)}"
        )
        return {
            "decision": d,
            "reason_summary": reason,
            "plan_summary": plan_sum,
            "forbidden_directions": fbd,
            "rescued_results": reuse,
            "trace_back_to": tb,
        }

