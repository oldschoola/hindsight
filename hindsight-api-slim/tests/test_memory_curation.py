"""Tests for memory curation: edit / invalidate / revert.

Covers the engine-level `update_memory_unit` contract and the recall-exclusion
guarantee:

1. Invalidate sets state='invalidated', drops the embedding, prunes the memory's
   links, removes derived observations, and resets surviving sources.
2. Revert restores state='valid' and recomputes the embedding.
3. Edit replaces the text, re-embeds, records the previous text in history, and
   re-derives observations.
4. Observations cannot be curated directly; invalidated rows cannot be edited.
5. list_memory_units exposes state/reason and filters by state.
6. Recall excludes invalidated memories.
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from hindsight_api import RequestContext
from hindsight_api.engine.memory_engine import MemoryEngine
from hindsight_api.engine.retain import embedding_processing

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_memory(
    conn, memory: MemoryEngine, bank_id: str, text: str, fact_type: str = "experience"
) -> uuid.UUID:
    """Insert a memory unit with a real embedding, bypassing the LLM pipeline."""
    mem_id = uuid.uuid4()
    emb = await embedding_processing.generate_embeddings_batch(memory.embeddings, [text])
    await conn.execute(
        """
        INSERT INTO memory_units (id, bank_id, text, fact_type, embedding, event_date, created_at, updated_at, consolidated_at)
        VALUES ($1, $2, $3, $4, $5::vector, NOW(), NOW(), NOW(), NOW())
        """,
        mem_id,
        bank_id,
        text,
        fact_type,
        str(emb[0]),
    )
    return mem_id


async def _insert_observation(conn, bank_id: str, text: str, source_memory_ids: list[uuid.UUID]) -> uuid.UUID:
    obs_id = uuid.uuid4()
    await conn.execute(
        """
        INSERT INTO memory_units (
            id, bank_id, text, fact_type, event_date, source_memory_ids, proof_count, created_at, updated_at
        ) VALUES ($1, $2, $3, 'observation', NOW(), $4, $5, NOW(), NOW())
        """,
        obs_id,
        bank_id,
        text,
        source_memory_ids,
        len(source_memory_ids),
    )
    return obs_id


async def _insert_link(conn, bank_id: str, from_id: uuid.UUID, to_id: uuid.UUID) -> None:
    await conn.execute(
        """
        INSERT INTO memory_links (from_unit_id, to_unit_id, link_type, weight, bank_id)
        VALUES ($1, $2, 'temporal', 0.5, $3)
        """,
        from_id,
        to_id,
        bank_id,
    )


async def _row(conn, mem_id: uuid.UUID) -> dict:
    return dict(
        await conn.fetchrow(
            "SELECT state, invalidation_reason, invalidated_at, embedding, text, consolidated_at, history "
            "FROM memory_units WHERE id = $1",
            mem_id,
        )
    )


async def _link_count(conn, mem_id: uuid.UUID) -> int:
    return await conn.fetchval(
        "SELECT COUNT(*) FROM memory_links WHERE from_unit_id = $1 OR to_unit_id = $1",
        mem_id,
    )


async def _obs_ids(conn, bank_id: str) -> list[str]:
    rows = await conn.fetch(
        "SELECT id FROM memory_units WHERE bank_id = $1 AND fact_type = 'observation'",
        bank_id,
    )
    return [str(r["id"]) for r in rows]


async def _ensure_bank(memory: MemoryEngine, bank_id: str, request_context: RequestContext) -> None:
    await memory.get_bank_profile(bank_id=bank_id, request_context=request_context)


# ---------------------------------------------------------------------------
# Invalidate
# ---------------------------------------------------------------------------


class TestInvalidate:
    @pytest.mark.asyncio
    async def test_invalidate_prunes_links_observations_and_drops_embedding(
        self, memory: MemoryEngine, request_context: RequestContext
    ):
        bank_id = f"test-curation-inv-{uuid.uuid4().hex[:8]}"
        await _ensure_bank(memory, bank_id, request_context)

        pool = await memory._get_pool()
        async with pool.acquire() as conn:
            m1 = await _insert_memory(conn, memory, bank_id, "The deploy server srv-04 runs PostgreSQL 14.")
            m2 = await _insert_memory(conn, memory, bank_id, "srv-04 is in the eu-west datacenter.")
            obs_id = await _insert_observation(conn, bank_id, "srv-04 runs PG14 in eu-west.", [m1, m2])
            await _insert_link(conn, bank_id, m1, m2)
            assert await _link_count(conn, m1) == 1

        with (
            patch.object(memory, "submit_async_consolidation", new=AsyncMock()),
            patch.object(memory, "submit_async_graph_maintenance", new=AsyncMock()),
        ):
            result = await memory.update_memory_unit(
                bank_id, str(m1), state="invalidated", reason="decommissioned", request_context=request_context
            )

        assert result is not None
        assert result["state"] == "invalidated"
        assert result["invalidation_reason"] == "decommissioned"
        assert result["invalidated_at"] is not None

        async with pool.acquire() as conn:
            row = await _row(conn, m1)
            assert row["state"] == "invalidated"
            assert row["embedding"] is None, "embedding must be dropped on invalidate"
            assert await _link_count(conn, m1) == 0, "links must be pruned"
            assert str(obs_id) not in await _obs_ids(conn, bank_id), "derived observation must be removed"
            # m2 is a surviving source of the deleted observation → reset for re-consolidation
            assert (await _row(conn, m2))["consolidated_at"] is None

        await memory.delete_bank(bank_id, request_context=request_context)

    @pytest.mark.asyncio
    async def test_invalidate_then_revert_restores_state_and_reembeds(
        self, memory: MemoryEngine, request_context: RequestContext
    ):
        bank_id = f"test-curation-rev-{uuid.uuid4().hex[:8]}"
        await _ensure_bank(memory, bank_id, request_context)

        pool = await memory._get_pool()
        async with pool.acquire() as conn:
            m1 = await _insert_memory(conn, memory, bank_id, "Alice prefers tea over coffee.")

        with (
            patch.object(memory, "submit_async_consolidation", new=AsyncMock()),
            patch.object(memory, "submit_async_graph_maintenance", new=AsyncMock()),
        ):
            await memory.update_memory_unit(bank_id, str(m1), state="invalidated", request_context=request_context)
            async with pool.acquire() as conn:
                assert (await _row(conn, m1))["embedding"] is None

            result = await memory.update_memory_unit(
                bank_id, str(m1), state="valid", request_context=request_context
            )

        assert result["state"] == "valid"
        assert result["invalidation_reason"] is None
        assert result["invalidated_at"] is None
        async with pool.acquire() as conn:
            row = await _row(conn, m1)
            assert row["embedding"] is not None, "embedding must be recomputed on revert"
            assert row["consolidated_at"] is None, "reverted memory must be re-consolidated"

        await memory.delete_bank(bank_id, request_context=request_context)

    @pytest.mark.asyncio
    async def test_invalidate_is_idempotent_and_updates_reason(
        self, memory: MemoryEngine, request_context: RequestContext
    ):
        bank_id = f"test-curation-idem-{uuid.uuid4().hex[:8]}"
        await _ensure_bank(memory, bank_id, request_context)

        pool = await memory._get_pool()
        async with pool.acquire() as conn:
            m1 = await _insert_memory(conn, memory, bank_id, "Bob works at Google.")

        with (
            patch.object(memory, "submit_async_consolidation", new=AsyncMock()),
            patch.object(memory, "submit_async_graph_maintenance", new=AsyncMock()),
        ):
            await memory.update_memory_unit(
                bank_id, str(m1), state="invalidated", reason="first", request_context=request_context
            )
            result = await memory.update_memory_unit(
                bank_id, str(m1), state="invalidated", reason="second", request_context=request_context
            )

        assert result["state"] == "invalidated"
        assert result["invalidation_reason"] == "second"
        await memory.delete_bank(bank_id, request_context=request_context)


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------


class TestEdit:
    @pytest.mark.asyncio
    async def test_edit_changes_text_records_history_and_rederives(
        self, memory: MemoryEngine, request_context: RequestContext
    ):
        bank_id = f"test-curation-edit-{uuid.uuid4().hex[:8]}"
        await _ensure_bank(memory, bank_id, request_context)

        pool = await memory._get_pool()
        async with pool.acquire() as conn:
            m1 = await _insert_memory(conn, memory, bank_id, "The assistant visited Paris in 2023.")
            obs_id = await _insert_observation(conn, bank_id, "The assistant went to Paris.", [m1])

        with (
            patch.object(memory, "submit_async_consolidation", new=AsyncMock()),
            patch.object(memory, "submit_async_graph_maintenance", new=AsyncMock()),
        ):
            result = await memory.update_memory_unit(
                bank_id,
                str(m1),
                text="The user visited Paris in 2023.",
                reason="wrong subject",
                request_context=request_context,
            )

        assert result["text"] == "The user visited Paris in 2023."
        assert result["state"] == "valid"
        async with pool.acquire() as conn:
            row = await _row(conn, m1)
            assert row["text"] == "The user visited Paris in 2023."
            assert row["embedding"] is not None
            assert row["consolidated_at"] is None
            history = row["history"]
            import json

            history = json.loads(history) if isinstance(history, str) else history
            assert any(h.get("previous_text") == "The assistant visited Paris in 2023." for h in history)
            assert str(obs_id) not in await _obs_ids(conn, bank_id), "stale observation must be re-derived"

        await memory.delete_bank(bank_id, request_context=request_context)

    @pytest.mark.asyncio
    async def test_cannot_edit_invalidated_memory(self, memory: MemoryEngine, request_context: RequestContext):
        bank_id = f"test-curation-editinv-{uuid.uuid4().hex[:8]}"
        await _ensure_bank(memory, bank_id, request_context)

        pool = await memory._get_pool()
        async with pool.acquire() as conn:
            m1 = await _insert_memory(conn, memory, bank_id, "Stale fact.")

        with (
            patch.object(memory, "submit_async_consolidation", new=AsyncMock()),
            patch.object(memory, "submit_async_graph_maintenance", new=AsyncMock()),
        ):
            await memory.update_memory_unit(bank_id, str(m1), state="invalidated", request_context=request_context)
            with pytest.raises(ValueError, match="revert"):
                await memory.update_memory_unit(
                    bank_id, str(m1), text="corrected", request_context=request_context
                )

        await memory.delete_bank(bank_id, request_context=request_context)


# ---------------------------------------------------------------------------
# Guards / listing / recall
# ---------------------------------------------------------------------------


class TestGuardsAndListing:
    @pytest.mark.asyncio
    async def test_cannot_curate_observation(self, memory: MemoryEngine, request_context: RequestContext):
        bank_id = f"test-curation-obs-{uuid.uuid4().hex[:8]}"
        await _ensure_bank(memory, bank_id, request_context)

        pool = await memory._get_pool()
        async with pool.acquire() as conn:
            m1 = await _insert_memory(conn, memory, bank_id, "source fact")
            obs_id = await _insert_observation(conn, bank_id, "a synthesized observation", [m1])

        with pytest.raises(ValueError, match="observation"):
            await memory.update_memory_unit(bank_id, str(obs_id), state="invalidated", request_context=request_context)

        await memory.delete_bank(bank_id, request_context=request_context)

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self, memory: MemoryEngine, request_context: RequestContext):
        bank_id = f"test-curation-404-{uuid.uuid4().hex[:8]}"
        await _ensure_bank(memory, bank_id, request_context)
        result = await memory.update_memory_unit(
            bank_id, str(uuid.uuid4()), state="invalidated", request_context=request_context
        )
        assert result is None
        await memory.delete_bank(bank_id, request_context=request_context)

    @pytest.mark.asyncio
    async def test_list_exposes_state_and_filters(self, memory: MemoryEngine, request_context: RequestContext):
        bank_id = f"test-curation-list-{uuid.uuid4().hex[:8]}"
        await _ensure_bank(memory, bank_id, request_context)

        pool = await memory._get_pool()
        async with pool.acquire() as conn:
            await _insert_memory(conn, memory, bank_id, "Valid fact one.")
            m2 = await _insert_memory(conn, memory, bank_id, "Fact to retire.")

        with (
            patch.object(memory, "submit_async_consolidation", new=AsyncMock()),
            patch.object(memory, "submit_async_graph_maintenance", new=AsyncMock()),
        ):
            await memory.update_memory_unit(
                bank_id, str(m2), state="invalidated", reason="dup", request_context=request_context
            )

        # Default listing includes both, with a state field.
        all_items = (await memory.list_memory_units(bank_id, request_context=request_context))["items"]
        assert {i["state"] for i in all_items} == {"valid", "invalidated"}

        invalid_only = (
            await memory.list_memory_units(bank_id, state="invalidated", request_context=request_context)
        )["items"]
        assert len(invalid_only) == 1
        assert invalid_only[0]["id"] == str(m2)
        assert invalid_only[0]["invalidation_reason"] == "dup"

        await memory.delete_bank(bank_id, request_context=request_context)

    @pytest.mark.asyncio
    async def test_recall_excludes_invalidated(self, memory: MemoryEngine, request_context: RequestContext):
        bank_id = f"test-curation-recall-{uuid.uuid4().hex[:8]}"
        await _ensure_bank(memory, bank_id, request_context)

        unit_ids = await memory.retain_async(
            bank_id,
            "The Anaconda XR7 telescope has a 9000mm focal length.",
            request_context=request_context,
        )
        assert unit_ids, "retain should produce at least one memory unit"

        def _hit(res) -> bool:
            return any("anaconda" in f.text.lower() or "telescope" in f.text.lower() for f in res.results)

        before = await memory.recall_async(
            bank_id, "Anaconda XR7 telescope focal length", request_context=request_context
        )
        assert _hit(before), "fact should be recalled before invalidation"

        with (
            patch.object(memory, "submit_async_consolidation", new=AsyncMock()),
            patch.object(memory, "submit_async_graph_maintenance", new=AsyncMock()),
        ):
            for uid in unit_ids:
                await memory.update_memory_unit(bank_id, uid, state="invalidated", request_context=request_context)

        after = await memory.recall_async(
            bank_id, "Anaconda XR7 telescope focal length", request_context=request_context
        )
        assert not _hit(after), "invalidated fact must be excluded from recall"

        await memory.delete_bank(bank_id, request_context=request_context)

