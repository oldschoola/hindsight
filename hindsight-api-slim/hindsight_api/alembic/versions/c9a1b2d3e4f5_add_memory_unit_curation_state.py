"""Add curation state to memory_units (edit/invalidate).

Adds a soft-curation layer to ``memory_units``:

- ``state``               'valid' (default) | 'invalidated'. Invalidated rows are
                          excluded from recall, consolidation, and graph
                          maintenance but kept for audit (and reversible).
- ``invalidation_reason`` optional free text recorded when a memory is invalidated.
- ``invalidated_at``      timestamp the memory was invalidated (NULL while valid).

A partial index keeps the recall hot-path (``state = 'valid'``) cheap regardless
of how much invalidated history accumulates.

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
    op.execute(f"ALTER TABLE {schema}memory_units ADD COLUMN IF NOT EXISTS state TEXT NOT NULL DEFAULT 'valid'")
    op.execute(f"ALTER TABLE {schema}memory_units ADD COLUMN IF NOT EXISTS invalidation_reason TEXT")
    op.execute(f"ALTER TABLE {schema}memory_units ADD COLUMN IF NOT EXISTS invalidated_at TIMESTAMPTZ")
    op.execute(
        f"DO $$ BEGIN "
        f"IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_memory_units_state') THEN "
        f"ALTER TABLE {schema}memory_units ADD CONSTRAINT chk_memory_units_state "
        f"CHECK (state IN ('valid', 'invalidated')); "
        f"END IF; END $$;"
    )
    # Partial index covering the recall hot-path (active rows only).
    op.execute(
        f"CREATE INDEX IF NOT EXISTS idx_memory_units_active "
        f"ON {schema}memory_units (bank_id, fact_type) WHERE state = 'valid'"
    )


def _pg_downgrade() -> None:
    schema = _pg_schema_prefix()
    op.execute(f"DROP INDEX IF EXISTS {schema}idx_memory_units_active")
    op.execute(f"ALTER TABLE {schema}memory_units DROP CONSTRAINT IF EXISTS chk_memory_units_state")
    op.execute(f"ALTER TABLE {schema}memory_units DROP COLUMN IF EXISTS invalidated_at")
    op.execute(f"ALTER TABLE {schema}memory_units DROP COLUMN IF EXISTS invalidation_reason")
    op.execute(f"ALTER TABLE {schema}memory_units DROP COLUMN IF EXISTS state")


def _oracle_upgrade() -> None:
    # Oracle has no "ADD COLUMN IF NOT EXISTS"; guard via the data dictionary so
    # the migration stays idempotent on installs that already have the columns
    # (e.g. created from the updated baseline).
    op.execute(
        """
        DECLARE
            n NUMBER;
        BEGIN
            SELECT COUNT(*) INTO n FROM user_tab_columns
            WHERE table_name = 'MEMORY_UNITS' AND column_name = 'STATE';
            IF n = 0 THEN
                EXECUTE IMMEDIATE 'ALTER TABLE memory_units ADD (' ||
                    'state VARCHAR2(32) DEFAULT ''valid'' NOT NULL, ' ||
                    'invalidation_reason CLOB, ' ||
                    'invalidated_at TIMESTAMP WITH TIME ZONE)';
                EXECUTE IMMEDIATE 'ALTER TABLE memory_units ADD CONSTRAINT chk_mu_state ' ||
                    'CHECK (state IN (''valid'', ''invalidated''))';
            END IF;
        END;
        """
    )


def _oracle_downgrade() -> None:
    op.execute(
        """
        DECLARE
            n NUMBER;
        BEGIN
            SELECT COUNT(*) INTO n FROM user_tab_columns
            WHERE table_name = 'MEMORY_UNITS' AND column_name = 'STATE';
            IF n > 0 THEN
                EXECUTE IMMEDIATE 'ALTER TABLE memory_units DROP CONSTRAINT chk_mu_state';
                EXECUTE IMMEDIATE 'ALTER TABLE memory_units DROP (state, invalidation_reason, invalidated_at)';
            END IF;
        END;
        """
    )


def upgrade() -> None:
    run_for_dialect(pg=_pg_upgrade, oracle=_oracle_upgrade)


def downgrade() -> None:
    run_for_dialect(pg=_pg_downgrade, oracle=_oracle_downgrade)
