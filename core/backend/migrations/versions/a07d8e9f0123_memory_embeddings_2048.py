"""Use the live-probed Ark Agent Plan 2048-dimension embedding contract.

Revision ID: a07d8e9f0123
Revises: 9f6c7d8e9012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a07d8e9f0123"
down_revision: str | None = "9f6c7d8e9012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _clear_and_change_dimension(dimension: int) -> None:
    # Vectors are derived data and cannot be cast between dimensions. Preserve
    # every Memory row, clear only derived vector fields, then backfill.
    op.drop_index("ix_memories_embedding_hnsw", table_name="memories")
    op.drop_constraint("ck_memories_embedding_metadata", "memories", type_="check")
    op.execute(
        "UPDATE memories SET embedding = NULL, embedding_model = NULL, "
        "embedding_dimensions = NULL WHERE embedding IS NOT NULL"
    )
    op.execute(
        f"ALTER TABLE memories ALTER COLUMN embedding TYPE vector({dimension})"
    )
    op.create_check_constraint(
        "ck_memories_embedding_metadata",
        "memories",
        "(embedding IS NULL AND embedding_dimensions IS NULL AND embedding_model IS NULL) "
        f"OR (embedding IS NOT NULL AND embedding_dimensions = {dimension} "
        "AND embedding_model IS NOT NULL)",
    )


def upgrade() -> None:
    _clear_and_change_dimension(2048)
    # pgvector HNSW supports vector through 2000 dimensions and halfvec
    # through 4000. Keep the authoritative float32 vector and index its
    # half-precision search expression instead of truncating dimensions.
    op.execute(
        "CREATE INDEX ix_memories_embedding_hnsw ON memories USING hnsw "
        "((embedding::halfvec(2048)) halfvec_cosine_ops) "
        "WHERE embedding IS NOT NULL"
    )


def downgrade() -> None:
    _clear_and_change_dimension(1024)
    op.create_index(
        "ix_memories_embedding_hnsw",
        "memories",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_where=sa.text("embedding IS NOT NULL"),
    )
