"""
Planning and pre-planning behaviors for Orchestrator.
"""

from __future__ import annotations

import re
from typing import Any

from src.state import ProblemStatus
from src.agents import (
    meta_strategist_analyze_problem,
    reasoner_create_plan,
)

class OrchestratorPlanningMixin:
    """Planning and pre-planning behaviors for Orchestrator."""

    # ========== Phase 0: Pre-Planning Analysis (Meta-Strategist) ==========

    def get_meta_suggestions(self) -> dict[str, Any]:
        """Get pre-planning hints based on hint_mode.
        
        This is Intervention Point 1: Pre-Planning Analysis.
        
        hint_mode determines the source of hints:
        - "auto": Call Meta-Strategist agent to analyze and suggest
        - "custom": Use user-provided hint_content directly
        - None: Skip hints entirely (proceed without pre-planning guidance)
        """
        # Skip if no hint mode is set
        if self.hint_mode is None:
            self.log("No hint mode specified, skipping pre-planning analysis")
            self.meta_suggestions = None
            return {"success": True, "skipped": True}
        
        problem = self.state.get_problem()
        if not problem:
            return {"success": False, "error": "No problem set"}
        
        if self.hint_mode == "custom":
            # Use user-provided hint directly
            self.log("Using custom user-provided hint for pre-planning...")
            self.meta_suggestions = self.hint_content or ""
            # Save to file for reference
            suggestions_file = self.state.base_dir / "meta_suggestions.md"
            suggestions_file.write_text(
                f"# Custom User Hint\n\n{self.meta_suggestions}",
                encoding="utf-8"
            )
            self.log("Custom hint applied")
            return {"success": True, "suggestions": self.meta_suggestions, "source": "custom"}
        
        elif self.hint_mode == "auto":
            # Call Meta-Strategist agent
            self.log("Requesting pre-planning analysis from Meta-Strategist...")
            
            result = meta_strategist_analyze_problem(
                problem=problem,
                session_id=self.state.meta_strategist_session_name or self.state.derived_meta_session_name(),
                transcript_path=str(self.state.base_dir / "jsonl" / "meta-analysis.jsonl"),
            )

            # Persist deterministic name on first success.
            if result.get("success") and self.state.meta_strategist_session_name is None:
                self.state.meta_strategist_session_name = self.state.derived_meta_session_name()
                self.state.save()
            
            if result.get("success"):
                self.meta_suggestions = result.get("response", "")
                # Save suggestions to file for reference
                suggestions_file = self.state.base_dir / "meta_suggestions.md"
                suggestions_file.write_text(self.meta_suggestions, encoding="utf-8")
                self.log("Meta-Strategist analysis complete")
                return {"success": True, "suggestions": self.meta_suggestions, "source": "meta-strategist"}
            else:
                self.log(f"Meta-Strategist failed: {result.get('error')}")
                # Non-fatal: continue without suggestions
                self.meta_suggestions = None
                return {"success": False, "error": result.get("error")}
        
        else:
            self.log(f"Unknown hint_mode: {self.hint_mode}, skipping")
            self.meta_suggestions = None
            return {"success": False, "error": f"Unknown hint_mode: {self.hint_mode}"}
    
    # ========== Phase 1: Planning ==========
    

    def request_plan(self, force_new_approach: bool = False) -> dict[str, Any]:
        """Ask Reasoner to create a plan.
        
        Args:
            force_new_approach: If True, pass previous failed plans to Reasoner
        """
        self.log("Requesting plan from Reasoner...")
        
        problem = self.state.get_problem()
        if not problem:
            return {"success": False, "error": "No problem set"}
        
        self.state.status = ProblemStatus.PLANNING
        self.state.set_phase("planning")
        
        # Get previous plans if forcing new approach (: derived
        # from structured failed_records).
        previous_plans = None
        if force_new_approach and self.state.failed_records:
            previous_plans = []
            for r in self.state.failed_records:
                if r.kind != "replan":
                    continue
                parts = [
                    f"## Plan v{r.plan_version} ({r.triggered_by} triggered)",
                    f"**Summary**: {r.summary}",
                    f"**Reason**: {r.reason}",
                ]
                if r.forbidden_directions:
                    parts.append("**Forbidden directions**:")
                    parts.extend(f"- {d}" for d in r.forbidden_directions)
                previous_plans.append("\n".join(parts))
        if previous_plans:
            self.log(f"Re-planning (attempt {self.state.replan_count + 1}), avoiding {len(previous_plans)} previous approaches")
        
        # Plan creation joins the same shared reasoner
        # session that step execution / write_solution will use. After re-plan
        # the state.reasoner_session_name is reset (see initiate_replan), so
        # the next plan call starts a fresh CLI session under the same name.
        reasoner_name = self.state.derived_reasoner_session_name()
        reasoner_session_id = self.state.reasoner_session_name or reasoner_name

        # Call Reasoner with Meta-Strategist suggestions
        result = reasoner_create_plan(
            problem=problem,
            previous_plans=previous_plans,
            meta_suggestions=self.meta_suggestions,
            transcript_path=str(self.state.base_dir / "jsonl" / "reason-plan.jsonl"),
            problem_id=self.problem_id,
            session_id=reasoner_session_id,
        )
        # Persist deterministic name on first successful spawn (idempotent).
        if result.get("success") and self.state.reasoner_session_name is None:
            self.state.reasoner_session_name = reasoner_name
            self.state.save()
        
        if not result.get("success"):
            self.log(f"Reasoner failed: {result.get('error')}")
            return result
        
        plan_content = result.get("response", "")
        self.log("Plan received, checking validity...")
        
        # Check plan validity (including suggestion coverage if available)
        check_result = self._check_plan(plan_content)
        
        if check_result.get("valid"):
            step_titles = check_result.get("step_titles", [])
            self.state.set_plan(plan_content, step_titles)
            self.log(f"Plan accepted with {len(step_titles)} steps")
            return {
                "success": True,
                "plan": plan_content,
                "steps": step_titles,
            }
        else:
            self.log(f"Plan rejected: {check_result.get('reason')}")
            return {
                "success": False,
                "error": "Plan does not meet requirements",
                "reason": check_result.get("reason"),
                "plan_content": plan_content,
            }
    

    def _check_plan(self, plan_content: str) -> dict[str, Any]:
        """Check if plan is valid (has numbered steps)."""
        if not plan_content or not plan_content.strip():
            return {"valid": False, "reason": "Empty plan"}
        
        # Use regex to extract step titles - this is a procedural check
        # Pattern matches: "Step 1:", "## Step 1:", "1.", "1)", etc.
        patterns = [
            r"##?\s*Step\s*(\d+)[:\s]+([^\n]+)",  # ## Step 1: Title
            r"Step\s*(\d+)[:\s]+([^\n]+)",         # Step 1: Title
            r"^(\d+)\.\s+([^\n]+)",                # 1. Title
            r"^(\d+)\)\s+([^\n]+)",                # 1) Title
        ]
        
        titles = []
        for pattern in patterns:
            matches = re.findall(pattern, plan_content, re.MULTILINE)
            if matches:
                titles = [m[1].strip() for m in matches]
                break
        
        if not titles:
            # Regex couldn't find numbered steps. Don't stop the run — fall
            # back to a single-step plan so the reasoner can take over and
            # work directly from `plan.md`. The plan content is preserved
            # verbatim; only the orchestrator-side step count degrades to 1.
            self.log("Plan has no recognizable numbered steps; falling back to single-step plan")
            titles = ["Execute plan"]
        
        return {"valid": True, "step_titles": titles}
    
    # ========== Phase 2: Step Execution ==========
