"""
Configuration for the Orchestrator system.

All limits are hardcoded and cannot be overridden by LLM.
"""

from dataclasses import dataclass, field
from typing import FrozenSet


@dataclass(frozen=True)
class OrchestratorConfig:
    """Immutable configuration for the orchestrator system."""
    
    # ========== Model Configuration ==========
    REASONER_MODEL: str = "gpt-5.5"
    VERIFIER_MODEL: str = "gpt-5.5"
    META_STRATEGIST_MODEL: str = "gpt-5.5"
    
    # ========== Reasoning Effort ==========
    REASONER_EFFORT: str = "xhigh"
    VERIFIER_EFFORT: str = "xhigh"
    META_STRATEGIST_EFFORT: str = "xhigh"
    
    # ========== Timeout Limits (seconds) ==========
    REASONER_TIMEOUT: int = 1800       # Reasoner needs time for complex reasoning + code (30 min)
    VERIFIER_TIMEOUT: int = 1200        # Verifier review (20 min)
    META_STRATEGIST_TIMEOUT: int = 600   # Meta-Strategist advice (10 min)
    CODE_EXECUTION_TIMEOUT: int = 600  # Python code execution (10 min) - for fallback verification
    SESSION_RESUME_TIMEOUT: int = 600  # Resuming session with new message (10 min)
    
    # ========== Challenge Limits ==========
    MAX_CHALLENGE_ROUNDS: int = 5      # Hard limit on challenge rounds; reaching it is treated as deadlock and triggers a Meta-Strategist re-plan decision.
    MIN_CHALLENGE_ROUNDS: int = 1      # At least one round before giving up
    MAX_CONSECUTIVE_TIMEOUTS: int = 3  # Max consecutive timeouts before escalating to human
    
    # ========== Code Issue Limits ==========
    MAX_CODE_RETRIES: int = 3          # Max retries with Meta-Strategist guidance for code issues
    MAX_TIMEOUT_RETRIES: int = 8       # Max retries after Reasoner timeout
    
    # ========== Agent Retry Limits ==========
    AGENT_MAX_RETRIES: int = 5         # Max retries for agent API/network errors
    MAX_SOLUTION_RETRIES: int = 2      # Max retries for writing final solution
    
    # ========== Re-Plan Limits ==========
    MAX_STEP1_FAILURES: int = 3        # After 3 failures at Step 1, trigger re-plan
    MAX_REPLANS: int = 3               # Maximum number of completely new plans

    # ========== Exploration ==========
    ENABLE_EXPLORATION: bool = True    # Plan-pre Exploration phase. False = skip directly to plan.
    MAX_EXPLORATION_ROUNDS: int = 2    # Hard cap on Exploration rounds before forced plan.
    EXPLORATION_BUDGET_MIN: int = 15   # Soft per-round wall-clock budget (minutes; reasoner self-checks).
    
    # ========== Iteration Limits ==========
    MAX_STEPS: int = 20                # Max steps in a plan
    MAX_TRACE_BACKS: int = 5           # Max times we can go back to previous steps
    MAX_TOTAL_DURATION: int = 3600     # 1 hour total timeout
    
    # ========== Tool Access Control ==========
    # Tools excluded from model visibility.
    EXCLUDED_TOOLS: tuple[str, ...] = (
        "web_search",
        "web_fetch",
    )
    # URLs denied for all agents. Blocks shell-based web access (curl, requests, etc.).
    # Use '*' to deny all URLs.
    DENIED_URLS: tuple[str, ...] = ("*",)
    
    # ========== Allowed Models (whitelist) ==========
    ALLOWED_MODELS: FrozenSet[str] = frozenset({
        "claude-sonnet-4.6",
        "claude-sonnet-4.5",
        "claude-opus-4.7",
        "claude-opus-4.6",
        "claude-opus-4.5",
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.2",
    })
    
    # ========== BYOK: Custom Model Provider ==========
    # Loaded from config.toml at project root (gitignored).
    # Copy config.toml.example → config.toml and fill in your values.
    # When PROVIDER_BASE_URL is non-empty, BYOK mode activates automatically.
    PROVIDER_TYPE: str = ""
    PROVIDER_BASE_URL: str = ""
    PROVIDER_API_KEY: str = ""
    PROVIDER_API_VERSION: str = ""
    # model_id -> deployment name (Azure) or wire model name (OpenAI/Anthropic)
    PROVIDER_WIRE_MODELS: dict[str, str] = field(default_factory=dict)
    # model_id -> reasoning effort override for BYOK (e.g. "low"/"medium"/"high").
    # When set, replaces the caller-supplied effort (xhigh→high, etc.) before
    # the value is forwarded to `copilot --reasoning-effort`. When absent, the
    # BYOK branch drops reasoning_effort entirely (safe default).
    PROVIDER_WIRE_MODEL_EFFORTS: dict[str, str] = field(default_factory=dict)
    
    def validate_model(self, model: str) -> bool:
        """Check if model is in whitelist."""
        return model in self.ALLOWED_MODELS


# Global config instance
CONFIG = OrchestratorConfig()


def _load_provider_config() -> None:
    """Load BYOK provider settings from config.toml if it exists.
    
    Since OrchestratorConfig is frozen, we use object.__setattr__ to patch
    the singleton after construction. This runs once at import time.
    """
    import tomllib
    from pathlib import Path
    
    toml_path = Path(__file__).resolve().parents[1] / "config.toml"
    if not toml_path.exists():
        return
    
    with open(toml_path, "rb") as f:
        data = tomllib.load(f)
    
    provider = data.get("provider", {})
    if not provider.get("base_url"):
        return
    
    for key, toml_key in [
        ("PROVIDER_TYPE", "type"),
        ("PROVIDER_BASE_URL", "base_url"),
        ("PROVIDER_API_KEY", "api_key"),
        ("PROVIDER_API_VERSION", "api_version"),
    ]:
        val = provider.get(toml_key, "")
        if val:
            object.__setattr__(CONFIG, key, val)
    
    # wire_models entries can be either:
    # "model-id" = "deployment-name" (string form, no effort override)
    # or:
    #   [provider.wire_models."model-id"]
    # Deployment = "deployment-name"
    #   reasoning_effort = "low" | "medium" | "high"
    wire_models_raw = provider.get("wire_models", {})
    deployments: dict[str, str] = {}
    efforts: dict[str, str] = {}
    for model_id, val in wire_models_raw.items():
        if isinstance(val, str):
            deployments[model_id] = val
        elif isinstance(val, dict):
            dep = val.get("deployment") or val.get("name")
            if dep:
                deployments[model_id] = dep
            eff = val.get("reasoning_effort")
            if eff:
                efforts[model_id] = eff
    if deployments:
        object.__setattr__(CONFIG, "PROVIDER_WIRE_MODELS", deployments)
    if efforts:
        object.__setattr__(CONFIG, "PROVIDER_WIRE_MODEL_EFFORTS", efforts)


_load_provider_config()
