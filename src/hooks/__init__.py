"""Hook scripts for the multi-agent reasoning system.

These run as command hooks under `.github/hooks/*.json`. They read the JSON
payload from stdin (provided by Copilot CLI) and emit a JSON decision on
stdout.

The shared lookup helpers in `lookup.py` resolve a `problem_id` and load the
corresponding `ProblemState` snapshot from disk.
"""
