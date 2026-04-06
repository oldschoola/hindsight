---
name: hindsight-recall
description: Search long-term memory for relevant context from past coding sessions using Hindsight
---

# Hindsight Recall

## Trigger

Use when the user explicitly asks about past decisions, project context, preferences, or anything that may have been discussed in prior sessions.

> **Note:** The Hindsight plugin already recalls memories automatically before each prompt. This skill is for cases where the user wants an explicit, on-demand memory lookup beyond what the automatic recall provided.

## Workflow

1. Identify the key topic or question from the user's request.
2. Check if `<hindsight_memories>` in the current context already contains relevant results.
3. If automatic recall already covered it, use those memories directly.
4. If the user needs deeper or different recall, tell them to adjust `recallBudget` or `recallTypes` in `~/.hindsight/cursor.json`, or use the Hindsight MCP integration for explicit tool-based recall.

## Guardrails

- Only suggest deeper recall when the automatic recall was insufficient.
- When memories conflict with current context, prefer current context and note the discrepancy.
- Do not expose raw memory metadata to the user unless asked.

## Output

- Relevant memories integrated into the response
- Guidance on adjusting recall settings if needed
