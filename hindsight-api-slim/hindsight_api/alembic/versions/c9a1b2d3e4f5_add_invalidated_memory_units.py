"""Add invalidated_memory_units table for curation (edit/invalidate).

Curation keeps the recall hot-path (``memory_units``) clean by *moving*
invalidated facts into a sibling archive table rather than flagging them in
place. If a row is in ``memory_units`` it is live; if it is in
``invalidated_memory_units`` it has been retired. Recall/consolidation/graph
queries never need a state predicate — the rows simply aren't there.

The archive mirrors ``memory_units`` column-for-column (so a row round-trips
losslessly on revert) plus:
- ``invalidation_reason``  optional free text recorded on invalidate
- ``invalidated_at``       when it was retired
- ``entity_ids``           snapshot of the unit's entity associations, so revert
                           can restore them (``unit_entities`` is cascade-deleted
                           when the live row is removed)

Revision ID: c9a1b2d3e4f5
Revises: d3e4f5a6b7c8
Create Date: 2026-06-03
"""

from collections.abc import Sequence

from alembic import context, op

from hindsight_api.alembic._dialect import run_for_dialect

revision: str = "c9a1b2d3e4f5"
down_revision: str | Sequence[str] | None = "d3e4f5a6b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _pg_schema_prefix() -> str:
    schema = context.config.get_main_option("target_schema")
    return f'"{schema}".' if schema else ""


def _pg_upgrade() -> None:
    schema = _pg_schema_prefix()
    # LIKE ... INCLUDING DEFAULTS clones every memory_units column (incl. the
    # embedding vector) so an invalidated row can move back verbatim. We
    # deliberately omit indexes/constraints — the archive is cold storage, not a
    # recall surface; only the lookups below need indexing.
    op.execute(
        f"CREATE TABLE IF NOT EXISTS {schema}invalidated_memory_units (LIKE {schema}memory_units INCLUDING DEFAULTS)"
    )
    op.execute(
        f"ALTER TABLE {schema}invalidated_memory_units "
        f"ADD COLUMN IF NOT EXISTS invalidation_reason TEXT, "
        f"ADD COLUMN IF NOT EXISTS invalidated_at TIMESTAMPTZ DEFAULT now(), "
        f"ADD COLUMN IF NOT EXISTS entity_ids UUID[]"
    )
    op.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS idx_invalidated_mu_id ON {schema}invalidated_memory_units (id)")
    op.execute(
        f"CREATE INDEX IF NOT EXISTS idx_invalidated_mu_bank "
        f"ON {schema}invalidated_memory_units (bank_id, invalidated_at)"
    )
    # Deleting a document (or bank) should clear its archived facts too, mirroring
    # the memory_units → documents cascade.
    op.execute(
        f"""
        DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'invalidated_mu_document_fkey') THEN
            ALTER TABLE {schema}invalidated_memory_units
                ADD CONSTRAINT invalidated_mu_document_fkey
                FOREIGN KEY (document_id, bank_id)
                REFERENCES {schema}documents(id, bank_id) ON DELETE CASCADE;
        END IF; END $$;
        """
    )


def _pg_downgrade() -> None:
    schema = _pg_schema_prefix()
    op.execute(f"DROP TABLE IF EXISTS {schema}invalidated_memory_units")


def upgrade() -> None:
    # PG-only: Oracle gets the table from the baseline snapshot, matching the
    # convention used by sibling column/index migrations in this tree.
    run_for_dialect(pg=_pg_upgrade)


def downgrade() -> None:
    run_for_dialect(pg=_pg_downgrade)
