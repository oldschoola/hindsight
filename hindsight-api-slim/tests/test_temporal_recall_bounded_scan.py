"""Deterministic tests for the bounded temporal-recall entry-point query.

`retrieve_temporal_combined` selects the 50 most-recent in-window units per fact_type
(ranked by COALESCE(occurred_start, mentioned_at, occurred_end)) before reranking the
small candidate set by embedding similarity. It must do so with a bounded
ORDER BY ... LIMIT 50 per fact_type backed by idx_memory_units_temporal_recency — NOT
a full sort of every date-matching row, which degrades to a disk-spilling sort on banks
with dense date metadata (see migration f7a8b9c0d1e2).

These tests are pure DB mechanics (no LLM), so they assert directly.
"""

from datetime import UTC, datetime, timedelta

import pytest

from hindsight_api.engine.search.retrieval import retrieve_temporal_combined
from hindsight_api.engine.task_backend import fq_table

EMBED_DIM = 384


def _vec(*leading: float) -> str:
    """Format a unit-prefixed embedding as a pgvector literal '[v0,v1,0,...]'."""
    values = list(leading) + [0.0] * (EMBED_DIM - len(leading))
    return "[" + ",".join(str(v) for v in values) + "]"


# Query vector and a perfectly-aligned "high similarity" vector (cosine 1.0).
_QUERY = _vec(1.0)
_HIGH_SIM = _vec(1.0)
# Cosine 0.5 against the query — above the 0.1 threshold, below _HIGH_SIM.
_FILLER = _vec(0.5, 0.8660254037844386)


async def _insert_unit(conn, bank_id: str, text: str, fact_type: str, mentioned_at: datetime, embedding: str) -> str:
    table = fq_table("memory_units")
    row = await conn.fetchrow(
        f"""
        INSERT INTO {table} (bank_id, text, fact_type, embedding, event_date, mentioned_at)
        VALUES ($1, $2, $3, $4::vector, $5, $5)
        RETURNING id
        """,
        bank_id,
        text,
        fact_type,
        embedding,
        mentioned_at,
    )
    return str(row["id"])


@pytest.mark.asyncio
async def test_temporal_recall_respects_window_and_recency_bound(memory):
    """Window filter + top-50-by-recency bound: an out-of-window or too-old unit is
    excluded even when its embedding is a perfect match for the query."""
    bank_id = "test_temporal_bounded_scan"
    window_start = datetime(2025, 1, 1, tzinfo=UTC)
    window_end = datetime(2025, 2, 1, tzinfo=UTC)

    pool = await memory._get_pool()
    async with pool.acquire() as conn:
        # Clean slate for idempotent reruns.
        await conn.execute(f"DELETE FROM {fq_table('memory_units')} WHERE bank_id = $1", bank_id)

        # 55 filler units, all in-window, moderate similarity, spread across Jan 10–12.
        filler_base = datetime(2025, 1, 10, tzinfo=UTC)
        for i in range(55):
            await _insert_unit(conn, bank_id, f"filler {i}", "world", filler_base + timedelta(hours=i), _FILLER)

        # Newest in-window unit, perfect similarity → must rank first.
        recent_high_id = await _insert_unit(
            conn, bank_id, "recent high sim", "world", datetime(2025, 1, 20, tzinfo=UTC), _HIGH_SIM
        )

        # Oldest in-window unit, perfect similarity. It is in the window and would win on
        # similarity, but it falls outside the 50 most-recent → must be EXCLUDED. This is
        # the precise guard for the bounded recency scan.
        oldest_high_id = await _insert_unit(
            conn, bank_id, "oldest high sim", "world", datetime(2025, 1, 5, tzinfo=UTC), _HIGH_SIM
        )

        # Out-of-window units with perfect similarity → must be EXCLUDED by the window.
        before_id = await _insert_unit(
            conn, bank_id, "before window", "world", datetime(2024, 12, 1, tzinfo=UTC), _HIGH_SIM
        )
        after_id = await _insert_unit(
            conn, bank_id, "after window", "world", datetime(2025, 3, 1, tzinfo=UTC), _HIGH_SIM
        )

        results = await retrieve_temporal_combined(
            conn,
            _QUERY,
            bank_id,
            ["world"],
            window_start,
            window_end,
            budget=100,
        )

    ids = {r.id for r in results.get("world", [])}

    # The newest, high-similarity unit is returned.
    assert recent_high_id in ids
    # Out-of-window units are never returned, even with a perfect-match embedding.
    assert before_id not in ids
    assert after_id not in ids
    # The oldest unit is excluded by the top-50-by-recency cut despite perfect similarity.
    assert oldest_high_id not in ids
    # Phase 2 caps the entry points at 10 per fact_type.
    assert len(ids) <= 10


@pytest.mark.asyncio
async def test_temporal_recency_index_serves_ordering(memory):
    """The recency index must satisfy the query's `DESC NULLS LAST` ordering directly.

    With sequential scans disabled, the plan must use idx_memory_units_temporal_recency
    and contain no Sort node — a regression that flips the index or query NULLS ordering
    (which silently reintroduces the full sort) is caught here.
    """
    bank_id = "test_temporal_index_ordering"
    table = fq_table("memory_units")

    pool = await memory._get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"DELETE FROM {table} WHERE bank_id = $1", bank_id)
        base = datetime(2025, 1, 10, tzinfo=UTC)
        for i in range(20):
            await _insert_unit(conn, bank_id, f"u{i}", "world", base + timedelta(hours=i), _FILLER)

        plan_sql = f"""
            EXPLAIN (FORMAT TEXT)
            SELECT mu.id
            FROM {table} mu
            WHERE mu.bank_id = $1
              AND mu.fact_type = 'world'
              AND mu.embedding IS NOT NULL
              AND (mu.mentioned_at BETWEEN $2 AND $3)
            ORDER BY COALESCE(mu.occurred_start, mu.mentioned_at, mu.occurred_end) DESC NULLS LAST
            LIMIT 50
        """
        # SET LOCAL needs a transaction; the block also resets enable_seqscan on exit
        # so the session connection isn't left mutated for other tests sharing the pool.
        async with conn.transaction():
            await conn.execute("SET LOCAL enable_seqscan = off")
            rows = await conn.fetch(
                plan_sql, bank_id, datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 2, 1, tzinfo=UTC)
            )
        plan = "\n".join(r[0] for r in rows)

    assert "idx_memory_units_temporal_recency" in plan, plan
    # The index supplies the ordering, so no explicit Sort is needed.
    assert "Sort" not in plan, plan
