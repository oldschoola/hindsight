#!/usr/bin/env python3
"""Auto-recall hook for Cursor's beforeSubmitPrompt event.

Flow:
  1. Read hook input from stdin (prompt, conversation_id, cwd)
  2. Resolve API URL (external, existing local, or auto-start daemon)
  3. Derive bank ID (static or dynamic from project context)
  4. Ensure bank mission is set (first use only)
  5. Compose multi-turn query if recallContextTurns > 1
  6. Truncate to recallMaxQueryChars
  7. Call Hindsight recall API
  8. Format memories and output additionalContext
  9. Save last recall to state

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
from lib.content import (
    compose_recall_query,
    format_current_time,
    format_memories,
    truncate_recall_query,
)
from lib.daemon import get_api_url
from lib.state import write_state

LAST_RECALL_STATE = "last_recall.json"


def read_transcript_messages(transcript_path: str) -> list:
    """Read messages from a JSONL transcript file for multi-turn context.

    Cursor transcript format:
      {role: "user", content: "..."}
      {role: "assistant", content: "..."}
    """
    if not transcript_path or not os.path.isfile(transcript_path):
        return []
    messages = []
    try:
        with open(transcript_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if "role" in entry and "content" in entry:
                        messages.append(entry)
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return messages


def _write_recall_status(status: str, **extra):
    """Write recall diagnostics on every invocation."""
    data = {
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": "plugin",
        "status": status,
    }
    data.update(extra)
    try:
        write_state(LAST_RECALL_STATE, data)
    except Exception:
        pass


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

    debug_log(config, f"Hook input keys: {list(hook_input.keys())}")

    # Extract user query from Cursor's hook input
    prompt = (hook_input.get("prompt") or hook_input.get("user_prompt") or "").strip()
    if not prompt or len(prompt) < 5:
        debug_log(config, "Prompt too short for recall, skipping")
        _write_recall_status("skipped", reason="short_prompt")
        return

    # Resolve API URL
    def _dbg(*a):
        debug_log(config, *a)

    try:
        api_url = get_api_url(config, debug_fn=_dbg, allow_daemon_start=False)
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

    # Multi-turn query composition
    recall_context_turns = config.get("recallContextTurns", 1)
    recall_max_query_chars = config.get("recallMaxQueryChars", 800)

    if recall_context_turns > 1:
        transcript_path = hook_input.get("transcript_path", "")
        messages = read_transcript_messages(transcript_path)
        debug_log(config, f"Multi-turn context: {recall_context_turns} turns, {len(messages)} messages")
        query = compose_recall_query(prompt, messages, recall_context_turns)
    else:
        query = prompt

    query = truncate_recall_query(query, prompt, recall_max_query_chars)

    if len(query) > recall_max_query_chars:
        query = query[:recall_max_query_chars]

    debug_log(config, f"Recalling from bank '{bank_id}', query length: {len(query)}")

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
        debug_log(config, "No memories found")
        _write_recall_status("empty", bank_id=bank_id, query_length=len(query))
        return

    debug_log(config, f"Injecting {len(results)} memories")

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

    # Output for Cursor hook system
    output = {
        "hookSpecificOutput": {
            "hookEventName": "beforeSubmitPrompt",
            "additionalContext": context_message,
        }
    }
    json.dump(output, sys.stdout)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[Hindsight] Unexpected error in recall: {e}", file=sys.stderr)
        try:
            from lib.config import load_config

            sys.exit(2 if load_config().get("debug") else 0)
        except Exception:
            sys.exit(0)
