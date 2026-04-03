---
name: hindsight-recall
description: Search long-term memory for relevant context from past coding sessions using Hindsight
---

# Hindsight Recall

## Trigger

Use when the user asks about past decisions, project context, preferences, or anything that may have been discussed in prior sessions. Also useful when starting work on a codebase to recall relevant architectural decisions and patterns.

## Workflow

1. Identify the key topic or question from the user's request.
2. Use the Hindsight MCP `recall` tool to search for relevant memories.
3. If recall returns results, incorporate them naturally into your response.
4. If the user shares new important information (decisions, preferences, patterns), use the `retain` tool to store it.

## Guardrails

- Only recall when prior context would genuinely help answer the question.
- Do not recall for simple, self-contained coding questions.
- When memories conflict with current context, prefer current context and note the discrepancy.
- Do not expose raw memory metadata to the user unless asked.

## Output

- Relevant memories integrated into the response
- New information stored if the user shares durable knowledge
