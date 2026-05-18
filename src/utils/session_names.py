"""Deterministic Copilot CLI session name helpers.

Builds deterministic session names from (role, problem_id, [step]). Names are
passed to the CLI via `--name <NAME>` on first call and `--resume <NAME>` on
subsequent calls, decoupling session identity from any per-call UUID
extraction.
"""

from __future__ import annotations

import re

# CLI doesn't document a name length limit; keep names <= 100 chars to be safe.
_MAX_NAME_LEN = 100
# Allowed characters in the final name (matches typical CLI-friendly tokens).
_SAFE_CHAR_RE = re.compile(r"[^a-zA-Z0-9_-]")


def _sanitize(token: str) -> str:
    """Replace any non-[a-zA-Z0-9_-] character with `_`."""
    return _SAFE_CHAR_RE.sub("_", token)


def make_session_name(
    role: str,
    problem_id: str,
    *,
    step: int | None = None,
    attempt: int = 0,
    run_salt: str | None = None,
) -> str:
    """Build a deterministic session name for `copilot --name` / `--resume`.

    Naming scheme:
      - Reasoner per problem:           ``reason-<problem_id>[-r<salt>]``
      - Verifier per step:              ``verify-<problem_id>[-r<salt>]-step{NN}``
      - Meta-Strategist per problem:    ``meta-<problem_id>[-r<salt>]``
      - After re-plan / trace-back:     ``<role>-<problem_id>[-r<salt>]-a{N}[-step{NN}]``
        where N is the cumulative reasoner-session attempt index.
        attempt=0 means the original session (no suffix).

    The ``run_salt`` (set once per process) prevents collisions in the
    Copilot CLI's global session store when the same ``problem_id`` is
    re-used across runs (e.g. crash-and-restart, batch reruns).

    The returned name is sanitized to ``[a-zA-Z0-9_-]`` and truncated to
    ``_MAX_NAME_LEN`` characters by trimming the problem_id portion.
    """
    if not role or not role.strip():
        raise ValueError("role must be a non-empty string")
    if not problem_id or not problem_id.strip():
        raise ValueError("problem_id must be a non-empty string")
    if attempt < 0:
        raise ValueError("attempt must be >= 0")

    role_clean = _sanitize(role.strip())
    pid_clean = _sanitize(problem_id.strip())
    salt_clean = _sanitize(run_salt.strip()) if run_salt and run_salt.strip() else ""

    suffix = ""
    if salt_clean:
        suffix += f"-r{salt_clean}"
    if attempt > 0:
        suffix += f"-a{attempt}"
    if step is not None:
        suffix += f"-step{step:02d}"
    # Reserve room for role prefix + dash + suffix; trim problem_id if needed.
    overhead = len(role_clean) + 1 + len(suffix)
    max_pid = max(1, _MAX_NAME_LEN - overhead)
    if len(pid_clean) > max_pid:
        pid_clean = pid_clean[:max_pid]

    return f"{role_clean}-{pid_clean}{suffix}"
