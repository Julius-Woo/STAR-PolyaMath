#!/usr/bin/env python3
"""
Runner script for the Orchestrator system.

Usage:
    python -m src.runner --problem-file problems/problem.md
    python -m src.runner --problem "Prove that sqrt(2) is irrational"
"""

import argparse
import sys
from pathlib import Path
from datetime import datetime

# Ensure we can import from the project root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.orchestrator import Orchestrator


def generate_problem_id(problem_name: str | None = None) -> str:
    """Generate a unique problem ID with optional problem name prefix."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if problem_name:
        # Sanitize: keep alphanumeric, underscore, hyphen
        safe_name = ''.join(c if c.isalnum() or c in '_-' else '_' for c in problem_name)
        # Remove consecutive underscores and strip
        while '__' in safe_name:
            safe_name = safe_name.replace('__', '_')
        safe_name = safe_name.strip('_')
        if safe_name:
            return f"{safe_name}_{timestamp}"
    return timestamp


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Orchestrator-driven multi-agent reasoning system"
    )
    
    # Problem input
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--problem-file",
        type=str,
        help="Path to problem file (markdown)"
    )
    input_group.add_argument(
        "--problem",
        type=str,
        help="Problem statement as string"
    )
    
    # Options
    parser.add_argument(
        "--problem-id",
        type=str,
        default=None,
        help="Custom problem ID (default: timestamp)"
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress verbose output"
    )
    parser.add_argument(
        "--hint",
        nargs="?",
        const="auto",
        default=None,
        help="Pre-planning hint. Use '--hint' for Meta-Strategist auto-analysis, or '--hint \"your hint\"' for custom hint"
    )
    parser.add_argument(
        "--hint-file",
        type=str,
        default=None,
        help="File containing pre-planning hint"
    )
    
    args = parser.parse_args()
    
    # Load problem
    problem_name = None
    if args.problem_file:
        problem_path = Path(args.problem_file)
        if not problem_path.exists():
            print(f"Error: Problem file not found: {args.problem_file}", file=sys.stderr)
            return 1
        problem = problem_path.read_text(encoding="utf-8")
        # Extract problem name from filename (without extension)
        problem_name = problem_path.stem
    else:
        problem = args.problem
    
    # Determine problem ID (include problem name if available)
    problem_id = args.problem_id or generate_problem_id(problem_name)
    
    # Determine hint mode
    # - None: no hint
    # - "auto": call Meta-Strategist
    # - string: use provided hint
    hint_mode = None
    hint_content = None
    if args.hint_file:
        hint_path = Path(args.hint_file)
        if hint_path.exists():
            hint_content = hint_path.read_text(encoding="utf-8")
            hint_mode = "custom"
        else:
            print(f"Warning: Hint file not found: {args.hint_file}", file=sys.stderr)
    elif args.hint is not None:
        if args.hint == "auto":
            hint_mode = "auto"  # Will call Meta-Strategist
        else:
            hint_content = args.hint
            hint_mode = "custom"
    
    # Get workspace root (parent of src/)
    workspace_root = Path(__file__).resolve().parents[1]

    # Auto-tee whole stdout/stderr to scratch/<id>/run.log so users don't
    # need `2>&1 | tee` on every run. Append-only; survives crashes.
    run_log_path = workspace_root / "scratch" / problem_id / "run.log"
    run_log_path.parent.mkdir(parents=True, exist_ok=True)
    _run_log_fh = open(run_log_path, "a", encoding="utf-8", buffering=1)

    class _Tee:
        def __init__(self, *streams):
            self._streams = streams
        def write(self, s):
            for st in self._streams:
                try:
                    st.write(s); st.flush()
                except Exception:
                    pass
        def flush(self):
            for st in self._streams:
                try:
                    st.flush()
                except Exception:
                    pass
        def isatty(self):
            return getattr(self._streams[0], "isatty", lambda: False)()
        def fileno(self):
            return self._streams[0].fileno()

    sys.stdout = _Tee(sys.stdout, _run_log_fh)
    sys.stderr = _Tee(sys.stderr, _run_log_fh)
    print(f"\n{'='*60}\n[runner] Run started: {datetime.now().isoformat(timespec='seconds')} (log: {run_log_path})\n{'='*60}\n")

    # Create and run Orchestrator
    orchestrator = Orchestrator(
        workspace_root=workspace_root,
        problem_id=problem_id,
        verbose=not args.quiet,
        hint_mode=hint_mode,              # "auto", "custom", or None
        hint_content=hint_content,        # Custom hint content if provided
    )
    
    # Solve
    result = orchestrator.solve(problem)
    
    if result.get("success"):
        print("\n" + "="*60)
        print("PROBLEM SOLVED")
        print("="*60)
        print(f"Solution: {result.get('solution_path')}")
        return 0
    else:
        print("\n" + "="*60)
        print(f"STOPPED: {result.get('status', 'error')}")
        print("="*60)
        if result.get("reason"):
            print(f"Reason: {result.get('reason')}")
        if result.get("error"):
            print(f"Error: {result.get('error')}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
