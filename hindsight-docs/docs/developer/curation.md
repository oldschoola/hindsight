---
sidebar_position: 6
---

# Curation: Editing, Invalidating & Pruning Memories

Memory is append-only by design — but sometimes a stored fact is **wrong**, has gone **stale**, or is a **duplicate**. Curation lets you correct or retire individual memories without losing the audit trail. Retired facts are moved out of the active set, so recall never returns them, while remaining fully recoverable.

## When to reach for what

Not every "bad memory" needs the same tool. Pick by *why* it's bad:

| The memory is… | Use | Why |
|---|---|---|
| **Wrong because the whole bank extracts badly** (e.g. consistently wrong subject) | Fix the bank's `retain_mission` / `observations_mission`, then **reprocess** the document | Systematic problems are best fixed at the source, then replayed — see [Retain](./retain.md) and [Observations](./observations.mdx). |
| **Wrong as a one-off** (a single misextracted fact) | **Edit** the memory | Corrects the text and regenerates everything derived from it. |
| **No longer true, with nothing to replace it** (decommissioned server, a tool that was fixed, a role that changed) | **Invalidate** the memory | Nothing in the pipeline knows the world changed, so you tell it explicitly. |
| **A duplicate or superseded fact** | **Invalidate** the memory | Removes the noise from recall while keeping the audit trail. |
| **Superseded by a newer fact you're storing anyway** (e.g. "likes BMW" → "likes Toyota") | Just retain the new fact | Consolidation already reconciles in-stream contradictions into a single observation. |

The rule of thumb: **if Hindsight could have known, let consolidation handle it; if only you know, curate it.**

## Edit a memory

Correct a fact's text. Hindsight re-embeds it, drops the observations and links derived from the old text, and re-consolidates — so downstream knowledge reflects the correction. The previous text is kept in the memory's history.

```bash
curl -X PATCH "$HINDSIGHT_URL/v1/default/banks/$BANK/memories/$MEMORY_ID" \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"text": "The user visited Paris in 2023.", "reason": "wrong subject"}'
```

## Invalidate a memory (reversible)

Soft-retire a fact. An invalidated memory:

- **disappears from recall**, consolidation, and the knowledge graph,
- has its **links pruned** and its **derived observations re-computed** without it,
- **stays in the bank** for audit (visible via the memory and document views), and
- can be **restored** at any time.

```bash
# Invalidate
curl -X PATCH "$HINDSIGHT_URL/v1/default/banks/$BANK/memories/$MEMORY_ID" \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"state": "invalidated", "reason": "server decommissioned 2026-06-01"}'

# Restore
curl -X PATCH "$HINDSIGHT_URL/v1/default/banks/$BANK/memories/$MEMORY_ID" \
  -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
  -d '{"state": "valid"}'
```

Only raw **world** and **experience** facts can be curated. Observations are *derived* — they regenerate from their sources, so you curate the underlying facts, not the observation.

:::note Documents are the source of truth
A memory is extracted from a document. Editing or invalidating a memory does **not** change the document it came from — that's deliberate: the document stays as an accurate historical record. As a result, **reprocessing a document resets curation** of the facts it produced (extraction runs fresh from the original text). Fix systematic issues at the mission level and reprocess; use edit/invalidate for the residue.
:::

## Find and audit curated memories

Listing memories returns a `state` (`valid` | `invalidated`) and an optional `invalidation_reason` for each, and **includes invalidated rows by default** so curation stays auditable. Filter with `?state=`:

```bash
# Only the invalidated ones (e.g. to review duplicates)
curl "$HINDSIGHT_URL/v1/default/banks/$BANK/memories/list?state=invalidated" \
  -H "Authorization: Bearer $API_KEY"
```

A typical pruning workflow: cluster duplicates from `memories/list`, then **invalidate** them — recall is clean immediately, and the audit trail is preserved.

## Curation in the control plane

The memory detail panel shows an **Invalidated** badge with its reason and an **Invalidate / Restore** button for raw facts, so you can curate directly from the UI.
