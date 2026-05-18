#!/usr/bin/env python3
"""sessionStart hook — inject per-problem state into agent's context.

Triggered by Copilot CLI on every session start (including --resume). Reads
the JSON payload from stdin, resolves which problem this session belongs to,
loads PROBLEM_STATE.md (and reasoner-private notes when applicable), and
emits `{"additionalContext": "..."}` so the content is prepended to the
next user message.

Exit code 0 with `{}` for "no-op" cases (unrecognized session, missing state
files, etc.) so the agent call is never blocked by a hook failure.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Make `from src...` imports work when CLI launches this script directly.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.hooks.lookup import (  # noqa: E402
    emit_decision,
    parse_session_name,
    read_problem_state_md,
    resolve_problem_id,
)


# Roles that should receive the full problem state. Keep in sync with
# session naming in src/utils/session_names.py.
_INJECTED_ROLES = {"reason", "verify", "meta", "explore"}


def _read_payload() -> dict:
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def _build_context(problem_id: str, role: str | None) -> str | None:
    """Compose the additionalContext block, or None for a no-op."""
    state_md = read_problem_state_md(problem_id)
    if not state_md:
        return None

    parts: list[str] = []
    parts.append("=" * 70)
    parts.append(f"[Live problem state — {problem_id}]")
    parts.append("(Injected at session start. Treat as authoritative for problem,")
    parts.append(" plan, current step, verified results, and prior failures.)")
    parts.append("=" * 70)
    parts.append("")
    parts.append(state_md.rstrip())

    return "\n".join(parts)


def main() -> int:
    payload = _read_payload()
    problem_id, role = resolve_problem_id(payload)
    if not problem_id:
        emit_decision({})
        return 0

    # Role might still be unknown (e.g., resolved via index without role).
    # Try one more parse attempt from any session_name that did make it
    # into the payload.
    if role is None:
        parsed = parse_session_name(payload.get("session_name") or payload.get("sessionName"))
        if parsed:
            role = parsed[0]

    if role and role not in _INJECTED_ROLES:
        # Unknown role — be conservative.
        emit_decision({})
        return 0

    ctx = _build_context(problem_id, role)
    if not ctx:
        emit_decision({})
        return 0

    emit_decision({"additionalContext": ctx})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
