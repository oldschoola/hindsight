#!/usr/bin/env python3
"""Session start hook for Cursor's sessionStart event.

Injects relevant project memories at the beginning of each Cursor session
via additionalContext. Unlike beforeSubmitPrompt (which cannot return
additionalContext), sessionStart is the correct Cursor hook for ambient
context injection.

Flow:
  1. Read hook input from stdin (workspace_roots, conversation_id)
  2. Resolve API URL (external, existing local, or auto-start daemon)
  3. Derive bank ID (static or dynamic from project context)
  4. Ensure bank mission is set (first use only)
  5. Compose a broad project-level query from workspace context
  6. Call Hindsight recall API
  7. Format memories and output additionalContext
  8. Save last recall to state

Exit codes:
  0 -- always (graceful degradation on any error)
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.bank import derive_bank_id, ensure_bank_mission
from lib.client import HindsightClient
from lib.config import debug_log, load_config
from lib.content import format_current_time, format_memories
from lib.daemon import get_api_url
from lib.state import write_state

LAST_RECALL_STATE = "last_recall.json"


def _write_recall_status(status: str, **extra):
    """Write recall diagnostics on every invocation."""
    data = {
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": "plugin",
        "hook": "sessionStart",
        "status": status,
    }
    data.update(extra)
    try:
        write_state(LAST_RECALL_STATE, data)
    except Exception:
        pass


def _build_session_query(hook_input: dict, config: dict) -> str:
    """Build a broad recall query from session context.

    At session start we don't have a specific user prompt, so we build a
    query from the workspace context: project name, workspace roots, and
    any configured bank mission.
    """
    parts = []

    # Use workspace roots to identify the project
    workspace_roots = hook_input.get("workspace_roots", [])
    if workspace_roots:
        project_names = [os.path.basename(r) for r in workspace_roots if r]
        if project_names:
            parts.append(f"Project: {', '.join(project_names)}")

    # Fall back to cwd
    cwd = hook_input.get("cwd", "")
    if not parts and cwd:
        parts.append(f"Project: {os.path.basename(cwd)}")

    # Include bank mission as context signal
    mission = config.get("bankMission", "")
    if mission:
        parts.append(mission)

    if not parts:
        parts.append("What are the key context and preferences for this project?")

    return "\n".join(parts)


def main():
    config = load_config()

    if not config.get("autoRecall"):
        debug_log(config, "Auto-recall disabled, exiting")
        _write_recall_status("skipped", reason="disabled")
        return

    # Read hook input from stdin
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        print("[Hindsight] Failed to read hook input", file=sys.stderr)
        _write_recall_status("error", reason="bad_stdin")
        return

    debug_log(config, f"sessionStart hook input keys: {list(hook_input.keys())}")

    # Resolve API URL — allow daemon start since this is session start
    def _dbg(*a):
        debug_log(config, *a)

    try:
        api_url = get_api_url(config, debug_fn=_dbg, allow_daemon_start=True)
    except RuntimeError as e:
        print(f"[Hindsight] {e}", file=sys.stderr)
        _write_recall_status("error", reason=f"api_url: {e}"[:200])
        return

    api_token = config.get("hindsightApiToken")
    try:
        client = HindsightClient(api_url, api_token)
    except ValueError as e:
        print(f"[Hindsight] Invalid API URL: {e}", file=sys.stderr)
        _write_recall_status("error", reason=f"invalid_url: {e}"[:200])
        return

    # Derive bank ID
    bank_id = derive_bank_id(hook_input, config)

    # Set bank mission on first use
    ensure_bank_mission(client, bank_id, config, debug_fn=_dbg)

    # Build a broad project-level query
    query = _build_session_query(hook_input, config)
    recall_max_query_chars = config.get("recallMaxQueryChars", 800)
    if len(query) > recall_max_query_chars:
        query = query[:recall_max_query_chars]

    debug_log(config, f"Session recall from bank '{bank_id}', query length: {len(query)}")

    # Call Hindsight recall API
    try:
        response = client.recall(
            bank_id=bank_id,
            query=query,
            max_tokens=config.get("recallMaxTokens", 1024),
            budget=config.get("recallBudget", "mid"),
            types=config.get("recallTypes"),
            timeout=10,
        )
    except Exception as e:
        print(f"[Hindsight] Recall failed: {e}", file=sys.stderr)
        _write_recall_status("error", reason=str(e)[:200], bank_id=bank_id)
        return

    results = response.get("results", [])
    if not results:
        debug_log(config, "No memories found for session start")
        _write_recall_status("empty", bank_id=bank_id, query_length=len(query))
        return

    debug_log(config, f"Injecting {len(results)} memories at session start")

    # Format context message
    memories_formatted = format_memories(results)
    preamble = config.get("recallPromptPreamble", "")
    current_time = format_current_time()

    context_message = (
        f"<hindsight_memories>\n"
        f"{preamble}\n"
        f"Current time - {current_time}\n\n"
        f"{memories_formatted}\n"
        f"</hindsight_memories>"
    )

    # Save last recall to state
    _write_recall_status("success", bank_id=bank_id, result_count=len(results), query_length=len(query))

    # Output for Cursor sessionStart hook — additionalContext is supported here
    output = {
        "additionalContext": context_message,
    }
    json.dump(output, sys.stdout)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[Hindsight] Unexpected error in session_start: {e}", file=sys.stderr)
        try:
            from lib.config import load_config

            sys.exit(2 if load_config().get("debug") else 0)
        except Exception:
            sys.exit(0)
