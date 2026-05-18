"""
Orchestrator-Driven Multi-Agent Reasoning System

Architecture:
    Orchestrator (claude-sonnet-4.5) - Flow control, yes/no decisions, agent dispatch
    Reasoner (gpt-5.2) - Problem decomposition, step-by-step reasoning
    Verifier (gemini-3-pro-preview) - Challenge and verify reasoning steps

See DESIGN.md for full documentation.
"""

from .config import OrchestratorConfig, CONFIG
from .state import ProblemState
from .orchestrator import Orchestrator

__all__ = ["OrchestratorConfig", "CONFIG", "ProblemState", "Orchestrator"]
