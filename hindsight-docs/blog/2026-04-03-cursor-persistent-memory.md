---
title: "Giving Cursor a Long-Term Memory"
description: Cursor resets its memory every session. Learn how to add persistent cross-session memory using the Hindsight plugin for Cursor — automatic session recall, conversation retention, and on-demand MCP tools.
authors: [DK09876]
date: 2026-04-03T12:00
tags: [cursor, integrations, memory, plugin, coding-agents]
image: /img/blog/cursor-persistent-memory.png
hide_table_of_contents: true
---

![Giving Cursor a Long-Term Memory](/img/blog/cursor-persistent-memory.png)

Cursor 3 introduced a plugin system with hooks, skills, and rules. It's a powerful architecture for extending what the agent can do. But one thing Cursor still doesn't have out of the box is persistent memory across sessions. Every new chat starts from scratch.

If you've spent three turns explaining your project's architecture, your team's naming conventions, or that you prefer functional patterns over classes, Cursor forgets all of it the moment the session ends.

The Hindsight plugin for Cursor fixes this. It uses the `sessionStart` hook to recall relevant project memories at the beginning of each session, and the `stop` hook to retain conversation transcripts after every task. It also configures Cursor's MCP support for on-demand memory tools mid-session. No manual effort, no copy-pasting context.

<!-- truncate -->

## TL;DR

- Cursor 3 plugins can add hooks, skills, and rules, but they still do not give Cursor durable cross-session memory on their own
- The Hindsight Cursor plugin adds automatic session recall at the start of each session and automatic retention after each task
- For mid-session memory operations, the plugin configures Cursor's MCP support with explicit `recall`, `retain`, and `reflect` tools
- You can run it against Hindsight Cloud, a self-hosted Hindsight API, or a local `hindsight-embed` daemon
- Memory can stay global, per project, per session, or per user depending on how you configure bank IDs

## The Problem: Session-Scoped Memory

Cursor's built-in context system is designed around the current session. You get your codebase, your open files, and whatever you type. That's great for single-session tasks, but it falls apart for anything that spans multiple sessions:

- **Repeated explanations.** You tell Cursor your API uses snake_case, your frontend uses camelCase, and tests go in `__tests__/` directories. Next session? You explain it again.
- **Lost decisions.** You and Cursor agree on an architecture — event-driven with a message bus. Two days later, you start a new session and Cursor suggests REST endpoints.
- **No user model.** Cursor doesn't know that you're a senior engineer who doesn't need basic explanations, or that you prefer TypeScript over JavaScript, or that your team uses Vitest instead of Jest.

These aren't edge cases. They're the default experience for anyone who uses Cursor across more than a few sessions.

## How the Plugin Works

The Hindsight plugin uses two complementary mechanisms to create a persistent memory loop:

### Session Recall (sessionStart hook)

When you open a new session, the `sessionStart` hook fires:

1. Builds a broad project-level query from your workspace context (project name, workspace roots)
2. Calls Hindsight's recall API with multi-strategy retrieval (semantic search, BM25, graph traversal, temporal filtering)
3. Formats the results into a `<hindsight_memories>` block
4. Injects it as `additionalContext` — the agent sees it for the entire session, you don't

The memories appear in the agent's context window but not in your chat transcript. Cursor uses them to inform every response in the session without cluttering your conversation.

### Auto-Retain (stop hook)

After Cursor completes a task, the plugin fires again:

1. Reads the conversation transcript from Cursor's JSONL file
2. Strips any injected memory tags (preventing feedback loops)
3. Applies chunked or full-session retention based on your config
4. POSTs the transcript to Hindsight for fact extraction and storage

Hindsight extracts structured facts — preferences, decisions, project details — and builds a knowledge graph over time. The next session, those facts are available for recall.

### On-Demand MCP Tools

The `init` command also configures Cursor's native MCP support (`.cursor/mcp.json`) to connect to Hindsight's MCP endpoint. This gives the agent explicit `recall`, `retain`, and `reflect` tools for mid-session use:

- **recall** — search for specific memories beyond what session recall provided
- **retain** — explicitly store important decisions or context
- **reflect** — reason over accumulated memories for architectural decisions

This two-layer approach — ambient session memory plus on-demand MCP tools — covers both "memory should just be there" and "I need to look something specific up."

## Setup

**Step 1:** Install the plugin:

```bash
pip install hindsight-cursor
cd /path/to/your-project
```

**Step 2a:** Connect to Hindsight Cloud (fastest — no local server needed):

```bash
hindsight-cursor init --api-url https://api.hindsight.vectorize.io --api-token YOUR_HINDSIGHT_API_TOKEN
```

Sign up at [Hindsight Cloud](https://ui.hindsight.vectorize.io/signup) to get a token.

**Step 2b:** Or connect to a local Hindsight server:

```bash
hindsight-cursor init --api-url http://localhost:8888
```

If you don't have Hindsight running locally, start it with Docker:

```bash
export OPENAI_API_KEY=your-key
docker run --rm -it --pull always -p 8888:8888 \
  -e HINDSIGHT_API_LLM_API_KEY=$OPENAI_API_KEY \
  -e HINDSIGHT_API_LLM_MODEL=gpt-4o-mini \
  -v $HOME/.hindsight-docker:/home/hindsight/.pg0 \
  ghcr.io/vectorize-io/hindsight:latest
```

**Step 3:** If Cursor is already open, **fully quit and reopen it** — plugins load at startup. The plugin activates automatically.

The `init` command handles everything: plugin files, connection config, and MCP setup.

## Per-Project Memory Isolation

By default, all Cursor sessions share a single memory bank (`cursor`). For teams or multi-project workflows, you can isolate memory per project:

```json
{
  "dynamicBankId": true,
  "dynamicBankGranularity": ["agent", "project"]
}
```

This derives the bank ID from the working directory. Your React project and your Go API get separate memories that don't cross-contaminate.

Available granularity fields: `agent`, `project`, `session`, `channel`, `user`. Combine them to match your workflow.

## Alternative: MCP-Only

If you'd rather skip the plugin hooks entirely and use only Cursor's native MCP support, you can configure `.cursor/mcp.json` manually:

```json
{
  "mcpServers": {
    "hindsight": {
      "url": "http://localhost:8888/mcp/"
    }
  }
}
```

This gives you explicit `retain`, `recall`, and `reflect` tools without any automatic behavior. The plugin approach is more automatic (hooks fire without you asking); the MCP-only approach gives you full control.

## Cookbook Demo

If you want a minimal reproducible demo before wiring the plugin into your real project, use the Cursor cookbook example:

- Seed a bank with sample coding preferences and project facts
- Open Cursor 3 in a test repository with the plugin installed
- Ask questions like `what testing framework do I prefer?` or `what stack is this project using?`

The cookbook lives alongside the other Hindsight examples in `hindsight-cookbook/applications/cursor-memory`.

## What's Next

The Cursor plugin is open source and available in the [Hindsight repository](https://github.com/vectorize-io/hindsight/tree/main/hindsight-integrations/cursor). Full configuration reference is in the [integration docs](/sdks/integrations/cursor).

Works with [Hindsight Cloud](https://ui.hindsight.vectorize.io/signup) or self-hosted via `hindsight-embed`.
