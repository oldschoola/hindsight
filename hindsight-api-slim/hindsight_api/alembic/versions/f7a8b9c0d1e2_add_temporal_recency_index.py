"""Add a recency-ordered expression index for bounded temporal retrieval

Revision ID: f7a8b9c0d1e2
Revises: d3e4f5a6b7c8
Create Date: 2026-06-04

The temporal retrieval entry-point query (retrieve_temporal_combined) takes the 50
most-recent in-window units per fact_type, ranked by
COALESCE(occurred_start, mentioned_at, occurred_end) DESC.

Without an index on that ordering expression, Postgres has to read every row the
date window matches and sort the whole set just to keep 50. On banks with dense or
near-uniform date metadata — e.g. a retain pipeline that stamps a large batch with a
single date — any recall window intersects (near-)all rows, so the query degrades to
a full sequential scan plus a disk-spilling sort (~30s on a 660k-row bank).

This partial expression index lets the planner walk rows in recency order per
(bank_id, fact_type) and stop after the LIMIT 50 is satisfied. NULLS LAST matches the
query's ORDER BY ... DESC NULLS LAST so the index can supply the ordering directly,
and the partial WHERE embedding IS NOT NULL mirrors the query predicate (temporal
recall only considers embedded units).

The index is created CONCURRENTLY so the migration does not block writes on
memory_units during production deployments. CONCURRENTLY requires running outside a
transaction block; see migrations.py for how this is handled safely.

This query path uses the pgvector `<=>` operator and is Postgres-only, so the Oracle
slot is intentionally absent.
"""

from collections.abc import Sequence

from alembic import context, op

from hindsight_api.alembic._dialect import run_for_dialect

revision: str = "f7a8b9c0d1e2"
down_revision: str | Sequence[str] | None = "d3e4f5a6b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _get_schema_prefix() -> str:
    schema = context.config.get_main_option("target_schema")
    return f'"{schema}".' if schema else ""


def _pg_upgrade() -> None:
    schema = _get_schema_prefix()
    # CREATE INDEX CONCURRENTLY cannot run inside a transaction block; an
    # autocommit_block runs each statement outside Alembic's migration transaction.
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_memory_units_temporal_recency "
            f"ON {schema}memory_units "
            f"(bank_id, fact_type, (COALESCE(occurred_start, mentioned_at, occurred_end)) DESC NULLS LAST) "
            f"WHERE embedding IS NOT NULL"
        )


def _pg_downgrade() -> None:
    schema = _get_schema_prefix()
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {schema}idx_memory_units_temporal_recency")


def upgrade() -> None:
    run_for_dialect(pg=_pg_upgrade)


def downgrade() -> None:
    run_for_dialect(pg=_pg_downgrade)
