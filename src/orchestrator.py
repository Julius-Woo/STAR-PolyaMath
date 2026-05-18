"""
Orchestrator - The central coordinator of the multi-agent reasoning system.

The Orchestrator:
- Does NOT perform reasoning itself
- Makes yes/no decisions on procedural matters
- Dispatches Reasoner and Verifier agents
- Detects convergence/stalemate in debates
- Manages trace-backs and human intervention
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config import CONFIG
from src.state import ProblemState, StepStatus, ProblemStatus
from src.orchestrator_mixins.orchestrator_planning import OrchestratorPlanningMixin
from src.orchestrator_mixins.orchestrator_execution import OrchestratorExecutionMixin
from src.orchestrator_mixins.orchestrator_challenge import OrchestratorChallengeMixin
from src.orchestrator_mixins.orchestrator_failure import OrchestratorFailureMixin
from src.orchestrator_mixins.orchestrator_exploration import OrchestratorExplorationMixin
from src.orchestrator_mixins.orchestrator_solve import OrchestratorSolveMixin


class Orchestrator(OrchestratorPlanningMixin, OrchestratorExecutionMixin, OrchestratorChallengeMixin, OrchestratorFailureMixin, OrchestratorExplorationMixin, OrchestratorSolveMixin):
    """
    Central coordinator for the multi-agent reasoning system.
    """
    
    def __init__(
        self, 
        workspace_root: Path, 
        problem_id: str, 
        verbose: bool = True,
        hint_mode: str | None = None,      # "auto", "custom", or None
        hint_content: str | None = None,   # Custom hint content if hint_mode="custom"
    ):
        self.workspace_root = workspace_root
        self.problem_id = problem_id
        self.verbose = verbose
        self.hint_mode = hint_mode              # Controls pre-planning hint behavior
        self.hint_content = hint_content        # Custom hint from user
        self.state = ProblemState.load_or_create(workspace_root, problem_id)
        self.meta_suggestions: str | None = None  # Cached Meta-Strategist suggestions
    
    def log(self, message: str) -> None:
        """Print Orchestrator log message if verbose."""
        if self.verbose:
            print(f"[Orchestrator] {message}")
    
    def log_agent(self, role: str, content: str) -> None:
        """Print agent output with clear visual separation."""
        if self.verbose:
            separator = "-" * 40
            print(f"\n[{role} Output] {separator}")
            # Truncate very long outputs
            if len(content) > 3000:
                print(content[:1500])
                print(f"\n... ({len(content) - 3000} chars truncated) ...\n")
                print(content[-1500:])
            else:
                print(content)
            print(f"[End {role}] {separator}\n")
    
    def _parse_json_response(self, response: str) -> dict[str, Any] | None:
        """Extract JSON from response."""
        # Try to find JSON block
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass
        
        # Try to parse entire response as JSON
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass
        
        # Try to find any JSON object
        json_match = re.search(r"\{[^{}]*\}", response, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass
        
        return None
