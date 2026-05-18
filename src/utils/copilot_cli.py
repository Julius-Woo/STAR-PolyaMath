#!/usr/bin/env python3
"""
GitHub Copilot CLI API Wrapper

Parses GitHub Copilot CLI's `--output-format=json` JSONL
event stream instead of scraping the human-readable text output. The public
return-dict shape of `call_copilot_cli` is preserved for backward compatibility
with `src/agent_runner.py` and other callers.
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed


def _create_error_result(error_msg, stderr="", error_type="other_error"):
    """Create standardized error result dictionary."""
    return {
        "success": False,
        "response": "",
        "stdout": "",
        "stderr": stderr or error_msg,
        "returncode": -1,
        "error": error_msg,
        "error_type": error_type,
    }


def apply_byok_env(model, reasoning_effort=None):
    """Apply BYOK provider env vars for `model` if config.toml maps it.

    Mutates os.environ in place (so child `copilot` processes inherit them).
    Returns a tuple `(env_keys_set, effective_model, effective_reasoning_effort)`:

    - `env_keys_set`: list of env var names that were set; the caller can pop
      them after the spawn to keep the parent process clean (best-effort).
    - `effective_model`: the value to pass to `copilot --model`. In BYOK mode
      we substitute the wire model name (e.g. `gpt-5.5`) for the user-facing
      alias (e.g. `azure-gpt-5.5`) so the CLI's local model registry
      recognizes it and does not reject `--reasoning-effort` with
      "Model X does not support reasoning effort configuration". Outside
      BYOK mode, returns the input model unchanged.
    - `effective_reasoning_effort`: resolved value to pass through to the
      CLI's `--reasoning-effort` flag. Replaces the caller-supplied value
      when an override exists in
      `[provider.wire_models."<model>"].reasoning_effort`; otherwise
      returns None when in BYOK mode (most BYOK deployments reject
      unsupported `reasoning_effort` values, so we drop it by default).

    Models NOT in PROVIDER_WIRE_MODELS are routed through GitHub's hosted
    models and are returned unchanged.
    """
    # Local import: keep this module import-light when CONFIG isn't needed.
    from src.config import CONFIG

    if not (CONFIG.PROVIDER_BASE_URL and model in CONFIG.PROVIDER_WIRE_MODELS):
        return [], model, reasoning_effort

    wire_model = CONFIG.PROVIDER_WIRE_MODELS[model]
    # Per the official BYOK docs, only these env vars are recognized:
    # COPILOT_PROVIDER_BASE_URL, COPILOT_PROVIDER_TYPE, COPILOT_PROVIDER_API_KEY.
    # The wire-model name is sent via `--model` so the CLI's local registry
    # accepts `--reasoning-effort`.
    env = {
        "COPILOT_PROVIDER_BASE_URL": CONFIG.PROVIDER_BASE_URL,
        "COPILOT_PROVIDER_TYPE": CONFIG.PROVIDER_TYPE or "azure",
        "COPILOT_PROVIDER_API_KEY": CONFIG.PROVIDER_API_KEY,
    }
    if CONFIG.PROVIDER_API_VERSION:
        env["COPILOT_PROVIDER_AZURE_API_VERSION"] = CONFIG.PROVIDER_API_VERSION

    set_keys = []
    for k, v in env.items():
        if v:
            os.environ[k] = v
            set_keys.append(k)

    # Per-model reasoning_effort override (see config.py docstring). When the
    # caller didn't ask for any effort, leave it None.
    effective_effort = reasoning_effort
    if reasoning_effort:
        override = CONFIG.PROVIDER_WIRE_MODEL_EFFORTS.get(model)
        effective_effort = override if override else None

    return set_keys, wire_model, effective_effort


# Copilot CLI 1.0.39 emits a JSONL stream when invoked with
# `--output-format json`. Each line is one JSON object with a `type` field.
# We parse the stream and surface a clean response, tool/subagent calls, and
# session/usage metadata.

_STREAM_ERROR_MARKERS = (
    "Stream completed without a response.completed event",
    "Execution failed:",
)


def _extract_assistant_text(data):
    """Extract textual content from an assistant.message `data` payload.

    The CLI uses a few shapes for `data.content`:
      - a plain string
      - a list of {"type": "text", "text": "..."} blocks (and possibly other
        block types we ignore)
    Returns a joined string.
    """
    if data is None:
        return ""
    content = data.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                # text-style blocks
                txt = block.get("text")
                if isinstance(txt, str):
                    parts.append(txt)
                    continue
                # delta-style blocks
                dtxt = block.get("deltaContent") or block.get("content")
                if isinstance(dtxt, str):
                    parts.append(dtxt)
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return ""


def _parse_jsonl_stream(text):
    """Parse a Copilot CLI JSONL output stream.

    Tolerant to malformed lines (skipped with a warning to stderr).

    Returns a dict with keys.
    """
    parsed = {
        "final_response": "",
        "all_assistant_messages": [],
        "tool_calls": [],
        "subagent_calls": [],
        "session_id": None,
        "exit_code": None,
        "premium_requests": None,
        "session_duration_ms": None,
        "api_duration_ms": None,
        "code_changes": None,
        "output_tokens": 0,
        "raw_events": [],
        "stream_error": None,
    }

    if not text:
        return parsed

    # Index tool calls and subagent starts by toolCallId so we can attach
    # results / completion timestamps.
    tool_by_id = {}
    subagent_by_id = {}
    assistant_delta_parts = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Lines that aren't JSON objects (e.g. stray banner text) are skipped.
        if not (line.startswith("{") and line.endswith("}")):
            # Still scan plaintext for stream-error markers.
            for marker in _STREAM_ERROR_MARKERS:
                if marker in line and parsed["stream_error"] is None:
                    parsed["stream_error"] = line
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError as e:
            print(f"[copilot_cli] WARN: skipping malformed JSONL line: {e}",
                  file=sys.stderr)
            continue
        if not isinstance(evt, dict):
            continue
        parsed["raw_events"].append(evt)
        etype = evt.get("type", "")
        data = evt.get("data") or {}

        if etype == "assistant.message":
            text_out = _extract_assistant_text(data)
            if not text_out and assistant_delta_parts:
                text_out = "".join(assistant_delta_parts)
            if text_out:
                parsed["all_assistant_messages"].append(text_out)
                parsed["final_response"] = text_out
            # Per-message output token count (Copilot CLI 1.0.39 emits this
            # on every assistant.message event; sum across the turn).
            try:
                tok = data.get("outputTokens")
                if isinstance(tok, (int, float)) and tok > 0:
                    parsed["output_tokens"] += int(tok)
            except Exception:
                pass
            assistant_delta_parts = []

        elif etype == "assistant.message_delta":
            chunk = data.get("deltaContent")
            if isinstance(chunk, str) and chunk:
                assistant_delta_parts.append(chunk)

        elif etype == "tool.execution_start":
            tool_call_id = data.get("toolCallId")
            entry = {
                "name": data.get("toolName"),
                "arguments": data.get("arguments"),
                "result": None,
                "success": None,
            }
            parsed["tool_calls"].append(entry)
            if tool_call_id is not None:
                tool_by_id[tool_call_id] = entry

        elif etype == "tool.execution_complete":
            tool_call_id = data.get("toolCallId")
            entry = tool_by_id.get(tool_call_id)
            if entry is None:
                # We never saw the start event; record it standalone.
                entry = {
                    "name": data.get("toolName"),
                    "arguments": None,
                    "result": None,
                    "success": None,
                }
                parsed["tool_calls"].append(entry)
            result_payload = data.get("result") or {}
            entry["result"] = result_payload.get("content") \
                or result_payload.get("detailedContent")
            entry["success"] = data.get("success")

        elif etype == "subagent.started":
            tool_call_id = data.get("toolCallId")
            entry = {
                "agentName": data.get("agentName"),
                "agentDisplayName": data.get("agentDisplayName"),
                "started_at": evt.get("timestamp"),
                "completed_at": None,
            }
            parsed["subagent_calls"].append(entry)
            if tool_call_id is not None:
                subagent_by_id[tool_call_id] = entry

        elif etype == "subagent.completed":
            tool_call_id = data.get("toolCallId")
            entry = subagent_by_id.get(tool_call_id)
            if entry is None:
                entry = {
                    "agentName": data.get("agentName"),
                    "agentDisplayName": data.get("agentDisplayName"),
                    "started_at": None,
                    "completed_at": evt.get("timestamp"),
                }
                parsed["subagent_calls"].append(entry)
            else:
                entry["completed_at"] = evt.get("timestamp")

        elif etype == "result":
            parsed["session_id"] = evt.get("sessionId")
            parsed["exit_code"] = evt.get("exitCode")
            usage = evt.get("usage") or {}
            parsed["premium_requests"] = usage.get("premiumRequests")
            parsed["session_duration_ms"] = usage.get("sessionDurationMs")
            parsed["api_duration_ms"] = usage.get("totalApiDurationMs")
            cc = usage.get("codeChanges")
            if isinstance(cc, dict):
                parsed["code_changes"] = {
                    "linesAdded": cc.get("linesAdded", 0),
                    "linesRemoved": cc.get("linesRemoved", 0),
                }

        # Stream-error scan: check any string fields in the event.
        if parsed["stream_error"] is None:
            # cheap path: serialize-and-search only if the line itself contains
            # any marker substring (avoids extra work for normal events).
            for marker in _STREAM_ERROR_MARKERS:
                if marker in line:
                    parsed["stream_error"] = marker
                    break

    if assistant_delta_parts:
        partial_text = "".join(assistant_delta_parts)
        if partial_text and (
            not parsed["all_assistant_messages"]
            or parsed["all_assistant_messages"][-1] != partial_text
        ):
            parsed["all_assistant_messages"].append(partial_text)
        if partial_text and not parsed["final_response"]:
            parsed["final_response"] = partial_text

    return parsed


def _build_usage_from_parsed(parsed):
    """Build the legacy-shaped `usage` dict from a parsed JSONL result.

    Backward-compatible keys are preserved (input_tokens, output_tokens,
    cache_read, cache_write, model, tools_used, shell_commands, code_changes).
    The Copilot CLI 1.0.39 JSONL stream exposes per-message ``outputTokens``
    (which we sum into ``output_tokens``) and ``premiumRequests`` /
    durations, but does not expose input or cache token counts; those
    fields therefore remain 0.
    """
    tools_used = [tc.get("name") for tc in parsed["tool_calls"] if tc.get("name")]

    shell_commands = []
    for tc in parsed["tool_calls"]:
        if tc.get("name") == "bash":
            args = tc.get("arguments") or {}
            cmd = None
            if isinstance(args, dict):
                cmd = args.get("command")
            elif isinstance(args, str):
                cmd = args
            if cmd:
                shell_commands.append(cmd)

    cc = parsed.get("code_changes") or {}
    code_changes = {
        "lines_added": cc.get("linesAdded", 0),
        "lines_removed": cc.get("linesRemoved", 0),
    }

    return {
        "input_tokens": 0,
        "output_tokens": parsed.get("output_tokens", 0),
        "cache_read": 0,
        "cache_write": 0,
        "model": None,
        "tools_used": tools_used,
        "shell_commands": shell_commands,
        "code_changes": code_changes,
        "premium_requests": parsed.get("premium_requests"),
        "session_id": parsed.get("session_id"),
        "session_duration_ms": parsed.get("session_duration_ms"),
        "api_duration_ms": parsed.get("api_duration_ms"),
    }


def _finalize_result_from_jsonl(stdout_text, stderr_text, returncode,
                                show_usage, timed_out=False):
    """Build the public `call_copilot_cli` result dict from a JSONL stdout."""
    parsed = _parse_jsonl_stream(stdout_text)

    # Determine error_type / success.
    # When the CLI fails fast (e.g. `--resume <name>` matches multiple
    # sessions, or BYOK rejects the request before streaming starts), it
    # prints to stderr, exits 0, and emits no JSONL on stdout. Treat
    # "no `result` event AND no assistant message AND non-empty stderr" as
    # an error so the retry loop surfaces the real CLI message.
    cli_emitted_nothing = (
        parsed["exit_code"] is None
        and not parsed["final_response"]
        and not parsed["all_assistant_messages"]
        and not parsed["raw_events"]
    )
    error_type = None
    if timed_out:
        error_type = "our_timeout"
    elif parsed["stream_error"] or returncode != 0 or (
        parsed["exit_code"] is not None and parsed["exit_code"] != 0
    ):
        error_type = "other_error"
    elif cli_emitted_nothing and (stderr_text or "").strip():
        error_type = "other_error"

    # Compose response. We surface the clean final assistant message. If a
    # stream error was detected, append the marker so the legacy substring
    # scan in agent_runner.py still triggers.
    response = parsed["final_response"]
    if parsed["stream_error"]:
        if response:
            response = response + "\n\n" + parsed["stream_error"]
        else:
            response = parsed["stream_error"]

    success = (
        not timed_out
        and not parsed["stream_error"]
        and error_type is None
        and (parsed["exit_code"] == 0 or (parsed["exit_code"] is None and returncode == 0))
    )

    result_dict = {
        "success": success,
        "response": response,
        "all_assistant_messages": list(parsed["all_assistant_messages"]),
        "stdout": stdout_text,
        "stderr": stderr_text,
        "returncode": returncode if returncode is not None else (parsed["exit_code"] or 0),
        "session_id": parsed["session_id"],
    }
    if error_type is not None:
        result_dict["error_type"] = error_type
    if timed_out:
        result_dict["error"] = "Command timed out"
        result_dict["partial"] = True
    elif error_type is not None:
        stderr_summary = (stderr_text or "").strip()
        if len(stderr_summary) > 2000:
            stderr_summary = "..." + stderr_summary[-2000:]
        if parsed["stream_error"]:
            error_msg = parsed["stream_error"]
        elif stderr_summary:
            error_msg = stderr_summary
        else:
            error_msg = (
                "Copilot CLI failed "
                f"(returncode={returncode}, exit_code={parsed['exit_code']})"
            )
        result_dict["error"] = error_msg
    if show_usage:
        result_dict["usage"] = _build_usage_from_parsed(parsed)
    return result_dict


# === End JSONL parsing ===


def _build_copilot_cmd(
    prompt,
    model=None,
    agent=None,
    allow_tools=None,
    allow_all_tools=False,
    excluded_tools=None,
    deny_urls=None,
    add_dirs=None,
    resume=None,
    continue_session=False,
    reasoning_effort=None,
    name=None,
):
    """Build the `copilot` CLI command.

    Always appends `--output-format json` so the wrapper can parse the structured JSONL event stream. Supports `name=` for naming a new session (`-n NAME`) — mutually exclusive with `resume=` / `continue_session=True`.
    """
    if name and resume:
        raise ValueError("`name` and `resume` are mutually exclusive")
    if name and continue_session:
        raise ValueError("`name` and `continue_session` are mutually exclusive")

    cmd = ["copilot", "-p", prompt, "--output-format", "json"]

    if model:
        cmd.extend(["--model", model])

    if agent:
        cmd.extend(["--agent", agent])

    if allow_all_tools:
        cmd.append("--allow-all-tools")
    elif allow_tools:
        for tool in allow_tools:
            cmd.extend(["--allow-tool", tool])

    if reasoning_effort:
        cmd.extend(["--reasoning-effort", reasoning_effort])

    if excluded_tools:
        for tool in excluded_tools:
            cmd.extend(["--excluded-tools", tool])

    if deny_urls:
        for url in deny_urls:
            cmd.extend(["--deny-url", url])

    if add_dirs:
        for d in add_dirs:
            cmd.extend(["--add-dir", str(d)])

    if continue_session:
        cmd.append("--continue")
    elif resume:
        cmd.extend(["--resume", str(resume)])
    elif name:
        cmd.extend(["-n", str(name)])

    return cmd


def _summarize_tool_args(name, args):
    """One-line human-readable summary of a tool call's arguments."""
    if not isinstance(args, dict):
        return ""
    # Per-tool curated summaries
    if name == "bash":
        cmd = args.get("command") or args.get("cmd") or ""
        cmd = cmd.replace("\n", " \u23ce ")
        if len(cmd) > 150:
            cmd = cmd[:147] + "..."
        return cmd
    if name in ("view", "read_file"):
        return args.get("path") or args.get("file") or args.get("filepath") or ""
    if name in ("create_file", "create"):
        return args.get("filePath") or args.get("path") or ""
    if name in ("edit", "edit_files", "apply_patch"):
        target = args.get("filePath") or args.get("path") or args.get("file") or ""
        return target
    if name == "skill":
        return args.get("skill") or args.get("name") or ""
    if name in ("grep", "rg"):
        return args.get("pattern") or args.get("query") or ""
    if name in ("glob", "list_dir"):
        return args.get("pattern") or args.get("path") or ""
    if name == "task":
        agent_type = args.get("agent_type") or "?"
        prompt = (args.get("prompt") or "").replace("\n", " ")
        if len(prompt) > 80:
            prompt = prompt[:77] + "..."
        return f"agent={agent_type} prompt={prompt!r}"
    if name == "report_intent":
        intent = args.get("intent") or ""
        if len(intent) > 150:
            intent = intent[:147] + "..."
        return intent
    if name == "web_fetch":
        return args.get("url") or ""
    # Generic fallback: first scalar argument value
    for k, v in args.items():
        if isinstance(v, (str, int, float, bool)):
            s = str(v).replace("\n", " ")
            if len(s) > 100:
                s = s[:97] + "..."
            return f"{k}={s}"
    return ""


def _default_stream_printer(event):
    """Default human-readable streaming printer used when no callback supplied.

    Prints assistant message_delta deltaContent chunks inline (so the user
    sees streamed text) and `[tool: <name> <arg-summary>]` markers for tool starts.
    """
    if not isinstance(event, dict):
        return
    etype = event.get("type", "")
    data = event.get("data") or {}
    if etype == "assistant.message_delta":
        chunk = data.get("deltaContent")
        if isinstance(chunk, str) and chunk:
            print(chunk, end="", flush=True)
    elif etype == "tool.execution_start":
        name = data.get("toolName") or "?"
        summary = _summarize_tool_args(name, data.get("arguments"))
        marker = f"[tool: {name}]" if not summary else f"[tool: {name} \u2014 {summary}]"
        print(f"\n{marker}", flush=True)
    elif etype == "subagent.started":
        name = data.get("agentDisplayName") or data.get("agentName") or "?"
        print(f"\n[subagent: {name}]", flush=True)
    elif etype == "assistant.message":
        # Ensure newline boundary at end of a turn.
        print("", flush=True)


def _write_transcript_oneshot(transcript_path, stdout_text):
    """Write captured stdout to a transcript file in one shot (non-streaming).

    Best-effort: open/write failures are logged to stderr and ignored.
    """
    if not transcript_path:
        return
    try:
        tp = Path(transcript_path)
        tp.parent.mkdir(parents=True, exist_ok=True)
        with open(tp, "a", encoding="utf-8") as fh:
            fh.write(stdout_text or "")
            fh.flush()
    except Exception as e:
        print(f"[copilot_cli] WARN: could not write transcript {transcript_path}: {e}",
              file=sys.stderr)


def _call_copilot_streaming(cmd, timeout, show_usage, stream_callback=None,
                            transcript_path=None, extra_env=None):
    """Execute copilot CLI with streaming JSONL output.

    `stream_callback`, if provided, receives **decoded JSON event dicts**
    (not raw lines). When omitted, a default human-readable printer is used.

    `transcript_path`, if set, is a path where each raw JSONL line read from
    stdout is appended in real time (best-effort: open failures are logged
    to stderr and the call continues without tee).
    """
    transcript_fh = None
    if transcript_path:
        try:
            tp = Path(transcript_path)
            tp.parent.mkdir(parents=True, exist_ok=True)
            transcript_fh = open(tp, "a", encoding="utf-8")
        except Exception as e:
            print(f"[copilot_cli] WARN: could not open transcript {transcript_path}: {e}",
                  file=sys.stderr)
            transcript_fh = None

    try:
        # Build a per-spawn env (do NOT mutate os.environ — multi-thread safety).
        spawn_env = None
        if extra_env:
            spawn_env = os.environ.copy()
            spawn_env.update({k: v for k, v in extra_env.items() if v is not None})

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=spawn_env,
        )

        stdout_lines = []
        stderr_lines = []

        def read_stderr():
            for line in iter(process.stderr.readline, ''):
                stderr_lines.append(line)

        stderr_thread = threading.Thread(target=read_stderr, daemon=True)
        stderr_thread.start()

        callback = stream_callback or _default_stream_printer

        start_time = time.time()
        timed_out = False
        try:
            for line in iter(process.stdout.readline, ''):
                if time.time() - start_time > timeout:
                    process.kill()
                    timed_out = True
                    break

                stdout_lines.append(line)

                if transcript_fh is not None:
                    try:
                        transcript_fh.write(line)
                        transcript_fh.flush()
                    except Exception as e:
                        print(f"[copilot_cli] WARN: transcript write failed: {e}",
                              file=sys.stderr)
                        try:
                            transcript_fh.close()
                        except Exception:
                            pass
                        transcript_fh = None

                stripped = line.strip()
                if stripped.startswith("{") and stripped.endswith("}"):
                    try:
                        evt = json.loads(stripped)
                    except json.JSONDecodeError:
                        evt = None
                    if evt is not None:
                        try:
                            callback(evt)
                        except Exception as cb_err:
                            print(f"[copilot_cli] stream callback error: {cb_err}",
                                  file=sys.stderr)

            if not timed_out:
                process.wait(timeout=max(1, timeout - (time.time() - start_time)))

        except subprocess.TimeoutExpired:
            process.kill()
            timed_out = True

        stderr_thread.join(timeout=1)

        stdout_text = ''.join(stdout_lines)
        stderr_text = ''.join(stderr_lines)
        returncode = process.returncode if process.returncode is not None else -1

        return _finalize_result_from_jsonl(
            stdout_text=stdout_text,
            stderr_text=stderr_text,
            returncode=returncode,
            show_usage=show_usage,
            timed_out=timed_out,
        )

    except FileNotFoundError:
        return _create_error_result("Copilot CLI not found. Please install it first.",
                                    error_type="other_error")
    except Exception as e:
        return _create_error_result(str(e), error_type="other_error")
    finally:
        if transcript_fh is not None:
            try:
                transcript_fh.close()
            except Exception:
                pass


def call_copilot_cli(
    prompt,
    model=None,
    agent=None,
    allow_tools=None,
    allow_all_tools=False,
    excluded_tools=None,
    deny_urls=None,
    add_dirs=None,
    timeout=60,
    show_usage=True,
    streaming=False,
    stream_callback=None,
    resume=None,
    continue_session=False,
    reasoning_effort=None,
    name=None,
    transcript_path=None,
    extra_env=None,
):
    """Call GitHub Copilot CLI and return a parsed result dict.

    Returns the same backward-compatible dict shape as before:
        - success (bool)
        - response (str): clean final assistant message (JSONL-derived)
        - stdout (str): raw JSONL
        - stderr (str)
        - returncode (int)
        - error_type (str, optional): "our_timeout" | "other_error"
        - usage (dict, optional)
        - session_id (str | None): top-level for convenience

    `name` and `resume` are mutually exclusive (ValueError if both).
    """
    if name and resume:
        raise ValueError("`name` and `resume` are mutually exclusive")

    if not prompt or not prompt.strip():
        return _create_error_result("Prompt cannot be empty")

    cmd = _build_copilot_cmd(
        prompt=prompt,
        model=model,
        agent=agent,
        allow_tools=allow_tools,
        allow_all_tools=allow_all_tools,
        excluded_tools=excluded_tools,
        deny_urls=deny_urls,
        add_dirs=add_dirs,
        resume=resume,
        continue_session=continue_session,
        reasoning_effort=reasoning_effort,
        name=name,
    )

    if streaming:
        return _call_copilot_streaming(cmd, timeout, show_usage, stream_callback,
                                       transcript_path=transcript_path,
                                       extra_env=extra_env)

    try:
        # Build per-spawn env without mutating os.environ.
        spawn_env = None
        if extra_env:
            spawn_env = os.environ.copy()
            spawn_env.update({k: v for k, v in extra_env.items() if v is not None})
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=spawn_env,
        )
        stdout_text = result.stdout or ""
        _write_transcript_oneshot(transcript_path, stdout_text)
        return _finalize_result_from_jsonl(
            stdout_text=stdout_text,
            stderr_text=result.stderr or "",
            returncode=result.returncode,
            show_usage=show_usage,
            timed_out=False,
        )
    except subprocess.TimeoutExpired as e:
        partial_stdout = (e.stdout or "") if isinstance(e.stdout, str) else ""
        partial_stderr = (e.stderr or "") if isinstance(e.stderr, str) else ""
        _write_transcript_oneshot(transcript_path, partial_stdout)
        return _finalize_result_from_jsonl(
            stdout_text=partial_stdout,
            stderr_text=partial_stderr or f"Command timed out after {timeout} seconds",
            returncode=-1,
            show_usage=show_usage,
            timed_out=True,
        )
    except FileNotFoundError:
        return _create_error_result("Copilot CLI not found. Please install it first.",
                                    error_type="other_error")
    except Exception as e:
        return _create_error_result(str(e), error_type="other_error")


def call_copilot_parallel(
    prompt,
    models=None,
    agent=None,
    allow_tools=None,
    allow_all_tools=False,
    timeout=60,
    show_usage=True,
    resume=None,
    continue_session=False,
):
    """Call Copilot CLI with the same prompt across multiple models in parallel."""
    if models is None:
        models = ['claude-sonnet-4.5', 'claude-sonnet-4', 'claude-haiku-4.5', 'gpt-5']

    if not prompt or not prompt.strip():
        return _create_error_result("Prompt cannot be empty")

    results = {}
    success_count = 0

    with ThreadPoolExecutor(max_workers=len(models)) as executor:
        future_to_model = {
            executor.submit(
                call_copilot_cli,
                prompt=prompt,
                model=model,
                agent=agent,
                allow_tools=allow_tools,
                allow_all_tools=allow_all_tools,
                timeout=timeout,
                show_usage=show_usage,
                resume=resume,
                continue_session=continue_session,
            ): model
            for model in models
        }

        for future in as_completed(future_to_model):
            model = future_to_model[future]
            try:
                result = future.result()
                results[model] = result
                if result.get('success'):
                    success_count += 1
            except Exception as e:
                results[model] = {
                    'success': False,
                    'error': f'Exception occurred: {str(e)}',
                }

    return {
        'success': success_count > 0,
        'results': results,
        'success_count': success_count,
        'total_count': len(models),
    }


def main():
    """Command line interface."""
    parser = argparse.ArgumentParser(description="GitHub Copilot CLI API Wrapper")
    parser.add_argument("prompt", nargs="+", help="Prompt to send to Copilot")
    parser.add_argument("--model", default=None, help="Model name (passed to copilot --model)")
    parser.add_argument("--agent", default=None, help="Agent name (passed to copilot --agent)")
    parser.add_argument("--timeout", type=int, default=60, help="Timeout in seconds")
    parser.add_argument("--no-usage", dest="show_usage", action="store_false", help="Disable usage parsing")
    parser.add_argument("--stream", dest="streaming", action="store_true", help="Stream output to stdout")
    parser.add_argument("--resume", nargs="?", const=True, default=None, help="Pass through to copilot --resume [sessionId]")
    parser.add_argument("--continue", dest="continue_session", action="store_true", help="Pass through to copilot --continue")
    parser.add_argument("--dry-run", action="store_true", help="Print the copilot command and exit")
    parser.add_argument("--reasoning-effort", default=None, help="Reasoning effort (low/medium/high/xhigh)")

    args = parser.parse_args()
    prompt = " ".join(args.prompt)

    # Apply BYOK provider env vars when the requested model is
    # mapped in config.toml so the smoke-test path matches what agent_runner
    # does for real agent calls. The effective model is the wire-model name
    # (e.g. `gpt-5.5`) which CLI's local registry recognizes; otherwise
    # `--reasoning-effort` would be rejected for unknown BYOK aliases.
    effective_model = args.model
    effective_effort = args.reasoning_effort
    if args.model:
        _byok_keys, effective_model, effective_effort = apply_byok_env(
            args.model, effective_effort
        )

    if args.dry_run:
        cmd = _build_copilot_cmd(
            prompt=prompt,
            model=effective_model,
            agent=args.agent,
            allow_all_tools=True,
            resume=(None if args.resume is True else args.resume),
            continue_session=args.continue_session,
            reasoning_effort=effective_effort,
        )
        print(" ".join(shlex.quote(part) for part in cmd))
        return

    result = call_copilot_cli(
        prompt,
        model=effective_model,
        agent=args.agent,
        allow_all_tools=True,
        timeout=args.timeout,
        show_usage=args.show_usage,
        streaming=args.streaming,
        resume=(None if args.resume is True else args.resume),
        continue_session=args.continue_session,
        reasoning_effort=effective_effort,
    )

    if result["success"]:
        response = result["response"]
        print(response)

        usage = result.get("usage", {})
        if usage:
            print("\n" + "=" * 50)
            print("Usage Summary:")
            if usage.get("model"):
                print(f"  Model: {usage['model']}")
            if usage.get("session_id"):
                print(f"  Session: {usage['session_id']}")
            if usage.get("premium_requests") is not None:
                print(f"  Premium requests: {usage['premium_requests']}")
            if usage.get("api_duration_ms") is not None:
                print(f"  API duration: {usage['api_duration_ms']} ms")
            if usage.get('tools_used'):
                print(f"  Tools used: {len(usage['tools_used'])}")
                for tool in usage['tools_used']:
                    print(f"    \u2022 {tool}")
            if usage.get('shell_commands'):
                print(f"  Shell commands: {len(usage['shell_commands'])}")
                for cmd in usage['shell_commands']:
                    print(f"    $ {cmd}")
            code_changes = usage.get('code_changes', {})
            if code_changes.get('lines_added') or code_changes.get('lines_removed'):
                print(f"  Code changes: +{code_changes['lines_added']} -{code_changes['lines_removed']} lines")
            print("=" * 50)
    else:
        print(f"Error: {result.get('stderr') or result.get('error')}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
