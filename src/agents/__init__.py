"""
Agent wrappers for Reasoner, Verifier, and Meta-Strategist.

Uses Copilot CLI with --agent flag to invoke the agents defined in .github/agents/
"""

from __future__ import annotations

from src.agent_runner import _call_agent, resume_session
from src.agents.agents_reasoner import (
    reasoner_create_plan,
    reasoner_execute_step,
    reasoner_continue_with_code_guidance,
    reasoner_respond_challenge,
    reasoner_write_solution,
    reasoner_execute_meta_task,
    reasoner_explore,
    reasoner_polish_exploration_solution,
)
from src.agents.agents_verifier import (
    verifier_review_report,
    verifier_evaluate_response,
    verifier_continue_session,
)
from src.agents.agents_meta_strategist import (
    meta_strategist_analyze_problem,
    meta_strategist_diagnose_and_guide,
    meta_strategist_decide_replan,
    meta_strategist_review_exploration,
    meta_strategist_orchestrate_reexplore,
)
