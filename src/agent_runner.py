"""
Shared agent invocation utilities.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any
from dataclasses import dataclass, field

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config import CONFIG
from src.utils.copilot_cli import call_copilot_cli
from src.utils.session_names import make_session_name
from src.hooks.lookup import (
    ENV_PROBLEM_ID_KEY,
    ENV_AGENT_ROLE_KEY,
    append_session_index,
    parse_session_name,
)


@dataclass
class AgentStats:
    """Global statistics tracker for agent calls within a solve session."""
    reasoner_calls: int = 0
    verifier_calls: int = 0
    meta_strategist_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cached_tokens: int = 0
    
    def record_call(self, agent_type: str, usage: dict | None = None):
        """Record an agent call and accumulate tokens."""
        # Map agent name to counter
        agent_map = {
            "reason": "reasoner",
            "reasoner": "reasoner",
            "reason-noncoding": "reasoner",  # Non-coding variant counts as reasoner
            "verify": "verifier",
            "verifier": "verifier",
            "meta": "meta_strategist",
            "meta-strategist": "meta_strategist",  # Agent name used in agents_meta_strategist.py
            "meta_strategist": "meta_strategist",
        }
        agent_key = agent_map.get(agent_type.lower(), None)
        
        if agent_key == "reasoner":
            self.reasoner_calls += 1
        elif agent_key == "verifier":
            self.verifier_calls += 1
        elif agent_key == "meta_strategist":
            self.meta_strategist_calls += 1

        if usage:
            self.total_input_tokens += usage.get("input_tokens", 0)
            self.total_output_tokens += usage.get("output_tokens", 0)
            self.total_cached_tokens += usage.get("cache_read", 0)
    
    def reset(self):
        """Reset all counters."""
        self.reasoner_calls = 0
        self.verifier_calls = 0
        self.meta_strategist_calls = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cached_tokens = 0


# Global stats instance for current solve session
_current_stats = AgentStats()


def get_agent_stats() -> AgentStats:
    """Get the current agent stats instance."""
    return _current_stats


def reset_agent_stats():
    """Reset agent stats for a new solve session."""
    _current_stats.reset()


# Deterministic session name registry
#
# Maps deterministic session NAME -> concrete sessionId UUID for the current
# process. `_call_agent` consults this to decide first-spawn (`--name`) vs
# resume, and to prefer resuming by UUID over name. UUIDs are unique on disk;
# names are not (the Copilot CLI session-state cache accumulates entries
# across runs and refuses `--resume <NAME>` when multiple match).
#
# A `None` value means: name is known but UUID not yet captured (still resume
# by name as best-effort).

_known_session_ids: dict[str, str | None] = {}


def mark_session_known(name: str | None, session_id: str | None = None) -> None:
    """Tell `_call_agent` that *name* refers to an already-spawned session.

    If `session_id` (a concrete UUID) is provided, future resumes will use
    `--resume <UUID>` to avoid the "multiple sessions match" failure mode
    when the on-disk session-state cache has stale entries with the same
    name from previous runs.

    Idempotent. Pass None safely (no-op). Once a UUID is recorded for a
    name, repeated calls without a UUID do not erase it.
    """
    if not name:
        return
    if session_id:
        _known_session_ids[name] = session_id
    elif name not in _known_session_ids:
        _known_session_ids[name] = None


def get_session_uuid(name: str | None) -> str | None:
    """Return the recorded sessionId UUID for *name*, or None."""
    if not name:
        return None
    return _known_session_ids.get(name)


def reset_known_sessions() -> None:
    """Clear the known-sessions registry (useful for tests/per-problem)."""
    _known_session_ids.clear()


def _extract_partial_work_from_transcript(
    transcript_path: str | None,
    max_chars: int = 8000,
) -> str | None:
    """Recover partial assistant work from a JSONL transcript file.

    Tolerates missing/empty files (returns None). Joins all assistant messages
    parsed by `_parse_jsonl_stream` and truncates to `max_chars`.
    """
    if not transcript_path:
        return None
    try:
        from src.utils.copilot_cli import _parse_jsonl_stream
        path = Path(transcript_path)
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8", errors="replace")
        if not text or len(text) < 20:
            return None
        parsed = _parse_jsonl_stream(text)
        msgs = parsed.get("all_assistant_messages") or []
        if not msgs:
            return None
        joined = "\n\n---\n\n".join(m for m in msgs if m)
        if not joined.strip():
            return None
        if len(joined) > max_chars:
            joined = "... (earlier work truncated) ...\n\n" + joined[-max_chars:]
        return joined
    except Exception as e:
        print(f"[Agent] Warning: Failed to extract partial work from transcript {transcript_path}: {e}")
        return None


def _extract_partial_work_from_text(text: str, max_chars: int = 8000) -> str | None:
    """
    Extract meaningful partial work from text (streaming output or file content).
    
    Filters out error messages and keeps useful work content.
    """
    if not text or len(text) < 100:
        return None
    
    # Remove known error patterns from the end
    error_patterns = [
        "Stream completed without a response.completed event",
        "Execution failed:",
        "Error:",
    ]
    
    # Find where error starts (if any) and truncate
    content = text
    for pattern in error_patterns:
        idx = content.find(pattern)
        if idx > 0:
            content = content[:idx]
    
    if len(content) < 100:
        return None
    
    # Extract meaningful content (skip headers and empty lines)
    lines = content.strip().split('\n')
    content_lines = []
    for line in lines:
        # Skip markdown headers at top level and separators
        if line.strip() and not line.startswith('# ') and not line.startswith('---'):
            content_lines.append(line)
    
    if not content_lines:
        return None
    
    # Take last N chars of meaningful content
    result = '\n'.join(content_lines)
    if len(result) > max_chars:
        result = "... (earlier work truncated) ...\n\n" + result[-max_chars:]
    
    return result


def _call_agent(
    prompt: str,
    agent: str,
    model: str,
    timeout: int,
    session_id: str | None = None,
    streaming: bool = True,
    retry_on_timeout: bool = True,
    max_retries: int | None = None,  # Uses CONFIG.AGENT_MAX_RETRIES if None
    output_label: str | None = None,
    context_summary: str | None = None,  # Brief summary for retry context
    reasoning_effort: str | None = None,  # Reasoning effort level (low/medium/high/xhigh)
    name: str | None = None,  # Deterministic session name for `--name <NAME>`
    transcript_path: str | None = None,  # JSONL transcript tee path
    problem_id: str | None = None,  # Passed via env so hooks can route
) -> dict[str, Any]:
    """
    Call an agent via Copilot CLI with timeout retry support.

    Args:
        prompt: The prompt to send
        agent: Agent name (reason, verify, meta-strategist)
        model: Model to use
        timeout: Timeout in seconds
        session_id: Deterministic session NAME to resume.
                    Mutually exclusive with `name`.
        name: Deterministic session NAME for a NEW session.
              Mutually exclusive with `session_id`.
        transcript_path: Path to a per-call JSONL transcript file (tee).
        streaming: Enable streaming output (default: True)
        retry_on_timeout: Whether to retry on timeout with guidance (for short timeouts only)
        max_retries: Maximum number of retries for other_error type failures
        output_label: Label to show before/after streaming output (e.g., "Reasoner")
        context_summary: Brief problem/step context to include on retry

    Returns:
        Result dict with success, response, timeout_retries, is_our_timeout, etc.
    """
    if name and session_id:
        raise ValueError("`name` and `session_id` are mutually exclusive")

    # If caller passed a session_id (a deterministic name) that this process
    # has never spawned, treat as first-call (--name); otherwise resume,
    # preferring the captured UUID over the name (see registry comment above).
    session_name_for_resume: str | None = None
    if session_id and session_id not in _known_session_ids:
        current_session = None
        current_name = session_id
    else:
        current_name = name
        if session_id:
            session_name_for_resume = session_id
            uuid = _known_session_ids.get(session_id)
            current_session = uuid or session_id
        else:
            current_session = None

    # Derive problem_id and role from session name when caller
    # didn't pass it explicitly. This drives the `STAR_PROBLEM_ID` env var
    # that hooks read to look up per-problem state.
    # When resuming by UUID, current_session is a UUID rather than a name,
    # so use the original deterministic name we stashed in
    # `session_name_for_resume` for parsing.
    effective_name = current_name or session_name_for_resume or current_session
    derived_role: str | None = None
    if problem_id is None and effective_name:
        parsed = parse_session_name(effective_name)
        if parsed:
            derived_role, problem_id = parsed
    elif effective_name:
        parsed = parse_session_name(effective_name)
        if parsed:
            derived_role = parsed[0]

    # Apply default from config if not specified
    if max_retries is None:
        max_retries = CONFIG.AGENT_MAX_RETRIES

    last_result = None
    retries = 0
    original_prompt = prompt  # Keep original for context
    
    # Set BYOK environment variables if this model has a wire_model mapping.
    # Models NOT in PROVIDER_WIRE_MODELS use default GitHub Copilot routing.
    # In BYOK mode the wire-model name (e.g. "gpt-5.5") is sent as `--model`
    # so the CLI's local model registry recognizes it and accepts
    # `--reasoning-effort` (the user-facing alias like "azure-gpt-5.5" would
    # otherwise be rejected). Per-model `reasoning_effort` override comes
    # from [provider.wire_models."<model>"].reasoning_effort in config.toml;
    # when absent, BYOK drops the flag.
    from src.utils.copilot_cli import apply_byok_env
    _byok_keys, model, reasoning_effort = apply_byok_env(model, reasoning_effort)

    def _cleanup_byok():
        for k in _byok_keys:
            os.environ.pop(k, None)
    
    while retries <= max_retries:
        # Print output label header if specified
        if output_label and streaming:
            separator = "-" * 40
            print(f"\n[{output_label} Output] {separator}")
        
        # Also pass role so the hook can branch (e.g.
        # inject reasoner-private notes only for reason role).
        _spawn_env: dict[str, str] = {}
        if problem_id:
            _spawn_env[ENV_PROBLEM_ID_KEY] = problem_id
        if derived_role:
            _spawn_env[ENV_AGENT_ROLE_KEY] = derived_role

        result = call_copilot_cli(
            prompt=prompt,
            model=model,
            agent=agent,
            allow_all_tools=True,
            excluded_tools=CONFIG.EXCLUDED_TOOLS,
            deny_urls=CONFIG.DENIED_URLS,
            timeout=timeout,
            show_usage=True,
            streaming=streaming,
            resume=current_session,
            name=current_name if not current_session else None,
            reasoning_effort=reasoning_effort,
            transcript_path=transcript_path,
            extra_env=_spawn_env or None,
        )
        
        last_result = result
        
        # Check error type from copilot_cli
        error_type = result.get("error_type")
        is_our_timeout = error_type == "our_timeout"
        is_other_error = error_type == "other_error"
        
        if result.get("success"):
            # Check for "success" that's actually an error (stream issues, etc.)
            response = result.get("response", "")
            if "Stream completed without a response.completed event" in response or \
               "Execution failed:" in response:
                # This is actually an error - treat as other_error for retry
                print(f"[Agent] Detected stream error in {agent} response, will retry...")
                is_other_error = True
                result["success"] = False
                result["error_type"] = "other_error"
                result["error"] = "Stream completed without response"
            else:
                # Genuine success - return with cleaned response
                result["response"] = response
                result["timeout_retries"] = retries
                # Capture the concrete sessionId UUID so future resumes can
                # use --resume <UUID> instead of <NAME> (see registry note).
                returned_uuid = result.get("session_id")
                if current_name:
                    mark_session_known(current_name, returned_uuid)
                elif session_name_for_resume:
                    mark_session_known(session_name_for_resume, returned_uuid)
                # Write session-index entry on first spawn (current_name set
                # implies this was --name, not --resume).
                if current_name and problem_id and derived_role:
                    append_session_index(
                        session_name=current_name,
                        problem_id=problem_id,
                        role=derived_role,
                        session_id=returned_uuid,
                    )
                if output_label and streaming:
                    separator = "-" * 40
                    print(f"[End {output_label}] {separator}\n")
                # Record agent call stats
                _current_stats.record_call(agent, result.get("usage"))
                _cleanup_byok()
                return result
        
        if is_our_timeout:
            # Our configured timeout was triggered - let Orchestrator handle with Meta-Strategist
            # Do NOT retry here, return immediately for Meta-Strategist intervention
            result["timeout_retries"] = retries
            result["is_our_timeout"] = True
            if output_label and streaming:
                separator = "-" * 40
                print(f"[End {output_label}] {separator}\n")
            # Record agent call stats (even on timeout)
            _current_stats.record_call(agent, result.get("usage"))
            _cleanup_byok()
            return result
        
        if is_other_error:
            # Stream error / API error / network error - retry with context preservation
            if retries >= max_retries:
                result["timeout_retries"] = retries
                result["is_our_timeout"] = False
                if output_label and streaming:
                    separator = "-" * 40
                    print(f"[End {output_label}] {separator}\n")
                # Record agent call stats (even on error after max retries)
                _current_stats.record_call(agent, result.get("usage"))
                _cleanup_byok()
                return result
            
            retries += 1

            error_detail = (
                result.get("error")
                or result.get("stderr")
                or "Copilot CLI returned an unspecified error"
            ).strip()
            if len(error_detail) > 500:
                error_detail = "..." + error_detail[-500:]

            # Try to extract partial work - prefer streaming output, fallback to transcript JSONL
            # For stream errors, result["response"] often contains partial work before error
            streaming_output = result.get("response", "") or result.get("stdout", "")
            partial_work = _extract_partial_work_from_text(streaming_output)

            # Fallback to JSONL transcript if streaming output is empty/useless
            if not partial_work and transcript_path:
                partial_work = _extract_partial_work_from_transcript(transcript_path)

            # Stream errors are transient; reuse the same session for the
            # retry. Resetting to an anonymous session here let the agent
            # find unrelated `scratch/` directories and write the wrong
            # solution. Prefer the captured UUID over the name on resume.
            returned_uuid = result.get("session_id")
            if returned_uuid and (current_name or session_name_for_resume):
                mark_session_known(current_name or session_name_for_resume,
                                   returned_uuid)
            resume_uuid = (
                returned_uuid
                or get_session_uuid(current_name)
                or get_session_uuid(session_name_for_resume)
            )
            if resume_uuid:
                current_session = resume_uuid
            else:
                current_session = current_session or current_name
            current_name = None
            
            if partial_work:
                # Construct retry prompt with preserved context
                print(f"[Agent] Copilot CLI error on {agent}: {error_detail}; retry {retries}/{max_retries} with {len(partial_work)} chars of recovered work...")
                prompt = f"""## ⚠️ CONNECTION INTERRUPTED - PLEASE CONTINUE

A stream error occurred and your session was lost. Below is your work before the interruption.
**DO NOT repeat this work** - continue from where you left off.

## YOUR WORK BEFORE INTERRUPTION:
---
{partial_work}
---

## ORIGINAL TASK:
{original_prompt}

**Continue from where you left off. Do not restart from the beginning.**"""
            else:
                # No partial work recovered, just retry with original prompt
                print(f"[Agent] Copilot CLI error on {agent}: {error_detail}; retry {retries}/{max_retries} (no partial work recovered)...")
                prompt = original_prompt

            retry_delay = min(2 ** (retries - 1), 15)
            if retry_delay:
                time.sleep(retry_delay)

            continue
        
        # Unknown error type (shouldn't happen, but handle gracefully)
        result["timeout_retries"] = retries
        result["is_our_timeout"] = False
        if output_label and streaming:
            separator = "-" * 40
            print(f"[End {output_label}] {separator}\n")
        # Record agent call stats
        _current_stats.record_call(agent, result.get("usage"))
        _cleanup_byok()
        return result
    
    # Record for last_result if we exit the loop
    if last_result:
        _current_stats.record_call(agent, last_result.get("usage"))
    
    _cleanup_byok()
    return last_result


def resume_session(
    session_id: str,
    new_message: str,
    timeout: int | None = None,  # Uses CONFIG.SESSION_RESUME_TIMEOUT if None
    transcript_path: str | None = None,
    agent: str = "reason",
) -> dict[str, Any]:
    """
    Resume an existing Copilot session with a new message.

    `session_id` now carries a deterministic session NAME (from
    `make_session_name`), passed to `copilot --resume <NAME>`. The parameter
    name is preserved for back-compat with existing callers.

    Args:
        session_id: Deterministic session name to resume.
        agent: Agent to use - "reason" for full capabilities, "reason-noncoding" for pure math mode
    """
    if timeout is None:
        timeout = CONFIG.SESSION_RESUME_TIMEOUT

    output_label = "Reasoner (non-coding, resumed)" if agent == "reason-noncoding" else "Reasoner (resumed)"

    return _call_agent(
        prompt=new_message,
        agent=agent,
        model=None,  # Session resume doesn't need model specification
        timeout=timeout,
        transcript_path=transcript_path,
        session_id=session_id,  # Now a deterministic name
        output_label=output_label,
    )
