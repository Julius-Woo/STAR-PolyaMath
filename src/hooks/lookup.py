"""Hook payload → ProblemState resolution helpers.

Hook scripts in this package call `resolve_problem_id(payload)` and
`load_problem_state(problem_id)` to find which problem the current Copilot
session belongs to, then load its on-disk state.

Resolution strategy (first hit wins):

1. **Session name parsing**: payload-carried session name is the most
   authoritative signal. Names follow `<role>-<problem_id>[-step{NN}]`
   (see `src/utils/session_names.py`).
2. **Environment variable `STAR_PROBLEM_ID`**: set by the Python orchestrator
   in the per-spawn env passed to `subprocess.Popen`. Survives one spawn but
   is also stable across hook invocations within that session because hook
   scripts inherit the copilot child env.
3. **Session-id index file** (`~/.star_session_index.jsonl`,
   append-only): Python writes one line per spawned session mapping
   `sessionId -> problem_id`. Robust across processes (multi-problem
   benchmarks) and across subagents (subagent inherits sessionId in payload).
4. **Working directory match**: if `cwd` lies under any
   `<workspace>/scratch/<id>/`, infer problem_id. Future-proof; today the
   orchestrator always runs at workspace root.

When no problem_id is recoverable, helpers return None and callers should
emit an empty JSON `{}` (no-op decision).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

# ── Module constants ─────────────────────────────────────────────────────────
SESSION_INDEX_PATH = Path(os.path.expanduser("~/.star_session_index.jsonl"))
ENV_PROBLEM_ID_KEY = "STAR_PROBLEM_ID"
ENV_AGENT_ROLE_KEY = "STAR_AGENT_ROLE"  # Reliable role hint

# Mirror of make_session_name(): "<role>-<problem_id>[-r{salt}][-a{N}][-step{NN}]".
# role and problem_id are [a-zA-Z0-9_]+ (with `-` allowed inside problem_id);
# optional `-r{salt}` is the per-process collision-avoidance salt;
# optional `-a{N}` is the reasoner attempt index (re-plan / trace-back epoch);
# optional `-step{NN}` is the verifier per-step suffix.
_SESSION_NAME_RE = re.compile(
    r"^(?P<role>[a-zA-Z0-9_]+)-(?P<problem_id>[a-zA-Z0-9_]+(?:-[a-zA-Z0-9_]+)*?)(?:-r[a-zA-Z0-9_]+)?(?:-a\d+)?(?:-step\d+)?$"
)


# ── Workspace anchoring ─────────────────────────────────────────────────────
def _workspace_root() -> Path:
    """Return repo root (parent of the `src/` package)."""
    # src/hooks/lookup.py → parents[2] = repo root
    return Path(__file__).resolve().parents[2]


def _scratch_dir(problem_id: str) -> Path:
    return _workspace_root() / "scratch" / problem_id


# ── Session-id index (append-only JSONL) ────────────────────────────────────
def append_session_index(
    *,
    session_name: str,
    problem_id: str,
    role: str,
    session_id: str | None = None,
) -> None:
    """Record a (session_name → problem_id) mapping for hook lookups.

    Idempotent in the sense that duplicate entries are harmless; callers
    typically write once per first-spawn. Best-effort — silently swallows
    file errors so caller paths never break.
    """
    try:
        SESSION_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "session_name": session_name,
            "problem_id": problem_id,
            "role": role,
            "session_id": session_id,
        }
        with open(SESSION_INDEX_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _scan_session_index_for(
    *, session_name: str | None = None, session_id: str | None = None
) -> dict[str, Any] | None:
    """Return the most recent index record matching either key, or None."""
    if not SESSION_INDEX_PATH.exists():
        return None
    try:
        latest = None
        with open(SESSION_INDEX_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if session_name and rec.get("session_name") == session_name:
                    latest = rec
                elif session_id and rec.get("session_id") == session_id:
                    latest = rec
        return latest
    except Exception:
        return None


# ── Resolution helpers ──────────────────────────────────────────────────────
def parse_session_name(session_name: str | None) -> tuple[str, str] | None:
    """Split `<role>-<problem_id>[-step{NN}]` → (role, problem_id), or None."""
    if not session_name:
        return None
    m = _SESSION_NAME_RE.match(session_name.strip())
    if not m:
        return None
    return m.group("role"), m.group("problem_id")


def _problem_id_from_cwd(cwd_str: str | None) -> str | None:
    if not cwd_str:
        return None
    try:
        cwd = Path(cwd_str).resolve()
        scratch_root = (_workspace_root() / "scratch").resolve()
        if scratch_root in cwd.parents or cwd == scratch_root:
            rel = cwd.relative_to(scratch_root)
            return rel.parts[0] if rel.parts else None
    except Exception:
        return None
    return None


def resolve_problem_id(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return (problem_id, role) from a hook payload, or (None, None).

    Tries, in order: payload session name → env var → session_id index →
    cwd. The role is best-effort and only populated when derivable.
    """
    # Payload may carry session metadata under a few possible keys; the
    # CamelCase event format uses `sessionId`. Some hook events also expose
    # an `agentName` (subagentStart/subagentStop) which is a plain agent
    # name (e.g. "verify"), not the session name we set with --name.
    session_id = payload.get("sessionId") or payload.get("session_id")

    session_name = payload.get("session_name") or payload.get("sessionName")

    # 1. Direct name parse
    parsed = parse_session_name(session_name)
    if parsed:
        return parsed[1], parsed[0]

    # 2. Env vars (set by Python orchestrator at spawn time)
    pid_env = os.environ.get(ENV_PROBLEM_ID_KEY)
    role_env = os.environ.get(ENV_AGENT_ROLE_KEY)
    if pid_env:
        return pid_env, role_env  # role may be None if caller didn't set it

    # 3. Index by sessionId
    rec = _scan_session_index_for(session_id=session_id)
    if rec and rec.get("problem_id"):
        return rec["problem_id"], rec.get("role")

    # 4. cwd hint
    pid_cwd = _problem_id_from_cwd(payload.get("cwd"))
    if pid_cwd:
        return pid_cwd, None

    return None, None


# ── ProblemState loaders (read-only; hook scripts must not mutate state) ────
def read_problem_state_md(problem_id: str) -> str | None:
    """Read `scratch/<id>/PROBLEM_STATE.md` (or None if missing)."""
    p = _scratch_dir(problem_id) / "PROBLEM_STATE.md"
    if p.exists():
        try:
            return p.read_text(encoding="utf-8")
        except Exception:
            return None
    return None


def read_state_json(problem_id: str) -> dict[str, Any] | None:
    """Read `scratch/<id>/state.json` (or None if missing)."""
    p = _scratch_dir(problem_id) / "state.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None



# ── Misc utilities for hook scripts ─────────────────────────────────────────
def emit_decision(decision: dict[str, Any]) -> None:
    """Print a JSON decision to stdout (hook contract). Empty/None → `{}`."""
    import sys

    if not decision:
        print("{}")
    else:
        print(json.dumps(decision, ensure_ascii=False))
        sys.stdout.flush()
