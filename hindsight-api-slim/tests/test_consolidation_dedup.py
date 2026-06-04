"""Deterministic unit tests for the consolidation duplicate-create guard.

These exercise the dedup decision directly (no LLM, no DB), so they reliably
guard the fix in CI — unlike the real-LLM integration test, which only triggers
the path stochastically.
"""

import types
import uuid
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

from hindsight_api.engine.consolidation.consolidator import (
    _dedup_reconcile_create,
    _DedupDecision,
    _duplicate_create_target,
    _norm_obs_text,
    _RunObs,
)
from hindsight_api.engine.search.types import RetrievalResult


@dataclass
class _FakeObs:
    id: str
    text: str


def _shown(*observations: _FakeObs) -> dict[str, _FakeObs]:
    return {_norm_obs_text(o.text): o for o in observations}


def test_norm_obs_text_collapses_whitespace_preserves_case() -> None:
    # Whitespace (incl. newlines) collapses; case is preserved.
    assert _norm_obs_text("  The  User  likes BASIL.\n") == "The User likes BASIL."
    assert _norm_obs_text(None) == ""


def test_create_matching_shown_observation_is_duplicate() -> None:
    shown = _shown(_FakeObs(id="11111111-aaaa", text="User waters the herbs early in the morning."))
    # Same text with only-whitespace differences still matches.
    target = _duplicate_create_target("User waters the   herbs early in the morning.", shown, set())
    assert target is not None
    assert target.startswith("shown observation 11111111")


def test_create_differing_only_in_case_is_not_duplicate() -> None:
    # Case-folding would lose information (e.g. acronyms), so a case-only difference
    # is treated as novel rather than silently dropped.
    shown = _shown(_FakeObs(id="22222222-bbbb", text="The user prefers TLS."))
    assert _duplicate_create_target("The user prefers tls.", shown, set()) is None


def test_create_matching_inresponse_update_is_duplicate() -> None:
    update_texts = {_norm_obs_text("Mint is kept in its own separate bed.")}
    target = _duplicate_create_target("Mint is kept in its own separate bed.", {}, update_texts)
    assert target == "an UPDATE in this response"


def test_novel_create_is_not_duplicate() -> None:
    shown = _shown(_FakeObs(id="22222222-bbbb", text="User waters the herbs early in the morning."))
    assert _duplicate_create_target("Rosemary is drought-tolerant.", shown, set()) is None
    assert _duplicate_create_target("", {}, set()) is None


# ── semantic dedup (_dedup_reconcile_create) ──────────────────────────────────
#
# Mocks the obs-anchored ANN probe and the LLM so the decision logic is tested without
# a DB or a real model. The embedding is passed in (the caller embeds once).

_TWIN_ID = "33333333-3333-4333-8333-333333333333"
_EMB = [0.1, 0.2, 0.3]


def _obs(text: str, sim: float, oid: str = _TWIN_ID) -> RetrievalResult:
    return RetrievalResult(id=oid, text=text, fact_type="observation", similarity=sim)


def _ctx(threshold: float = 0.97, run_cache=None):
    """Return (kwargs, conn_mock, llm_mock) for a _dedup_reconcile_create call."""
    conn = AsyncMock()
    llm = types.SimpleNamespace(call=AsyncMock())
    kwargs = dict(
        conn=conn,
        memory_engine=types.SimpleNamespace(embeddings=object()),
        bank_id="bank1",
        config=types.SimpleNamespace(consolidation_dedup_threshold=threshold),
        dedup_llm_config=llm,
        create_text="YouTube content in Uzbek is very rich.",
        create_embedding=_EMB,
        create_source_ids=[uuid.uuid4()],
        tags=["t1"],
        run_cache=run_cache,
    )
    return kwargs, conn, llm


def _patch_probe(results):
    return patch(
        "hindsight_api.engine.search.retrieval.retrieve_semantic_bm25_combined",
        AsyncMock(return_value={"observation": (results, [])}),
    )


async def test_dedup_no_twin_above_threshold_returns_none() -> None:
    kwargs, conn, llm = _ctx(threshold=0.97)
    with _patch_probe([_obs("something loosely related", 0.81)]):
        result = await _dedup_reconcile_create(**kwargs)
    assert result is None
    llm.call.assert_not_called()  # below threshold → no LLM call
    conn.execute.assert_not_called()  # no merge


async def test_dedup_llm_keep_does_not_merge() -> None:
    kwargs, conn, llm = _ctx()
    llm.call.return_value = _DedupDecision(action="keep", reason="different language")
    with _patch_probe([_obs("Uzbek content on YouTube is described as very rich.", 0.98)]):
        result = await _dedup_reconcile_create(**kwargs)
    assert result is None
    llm.call.assert_awaited_once()
    conn.execute.assert_not_called()  # kept distinct → no merge


async def test_dedup_llm_merge_folds_into_twin() -> None:
    kwargs, conn, llm = _ctx()
    kwargs["create_source_ids"] = [uuid.uuid4(), uuid.uuid4()]
    llm.call.return_value = _DedupDecision(action="merge", text="Uzbek content on YouTube is very rich.")
    with _patch_probe([_obs("Uzbek content on YouTube is described as very rich.", 0.99)]):
        result = await _dedup_reconcile_create(**kwargs)
    assert result == _TWIN_ID  # merged into the twin; caller skips the CREATE
    conn.execute.assert_awaited_once()
    args = conn.execute.await_args.args
    assert args[1] == "Uzbek content on YouTube is very rich."  # merged text persisted
    assert args[2] == kwargs["create_source_ids"]  # new source facts folded in
    assert args[3] == uuid.UUID(_TWIN_ID)  # onto the twin row


async def test_dedup_picks_highest_above_threshold_skips_below() -> None:
    # Only the >=threshold candidate is considered; a 0.95 result is ignored at threshold 0.97.
    kwargs, conn, llm = _ctx(threshold=0.97)
    llm.call.return_value = _DedupDecision(action="keep")
    with _patch_probe([_obs("near but distinct", 0.95), _obs("the real twin", 0.98)]):
        await _dedup_reconcile_create(**kwargs)
    # the twin passed to the LLM is the >=0.97 one, not the 0.95
    sent = llm.call.await_args.kwargs["messages"][0]["content"]
    assert "the real twin" in sent
    assert "near but distinct" not in sent


async def test_dedup_finds_twin_in_run_cache_when_db_misses() -> None:
    # The DB probe returns nothing (same-run rows aren't visible to the ANN index), but the
    # in-memory run cache holds an earlier same-run create with an identical embedding.
    cache_id = "44444444-4444-4444-8444-444444444444"
    run_cache = [_RunObs(id=cache_id, text="Uzbek content on YouTube is rich.", embedding=_EMB)]
    kwargs, conn, llm = _ctx(run_cache=run_cache)
    llm.call.return_value = _DedupDecision(action="merge", text="Uzbek content on YouTube is rich.")
    with _patch_probe([]):  # DB finds nothing
        result = await _dedup_reconcile_create(**kwargs)
    assert result == cache_id  # twin came from the run cache
    conn.execute.assert_awaited_once()
    assert conn.execute.await_args.args[3] == uuid.UUID(cache_id)
