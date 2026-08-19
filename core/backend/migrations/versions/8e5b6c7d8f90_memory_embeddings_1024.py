"""Move memory embeddings to the production Ark 1024-dimension contract."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8e5b6c7d8f90"
down_revision: Union[str, None] = "34039a40f40d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _change_dimension(dimension: int) -> None:
    # Embeddings are derived data.  Existing vectors cannot be cast between
    # dimensions, so clear them before changing the pgvector type; the
    # independent backfill command repopulates them with the configured model.
    op.drop_index(
        "ix_memories_embedding_hnsw",
        table_name="memories",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_where=sa.text("embedding IS NOT NULL"),
    )
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
        f"(embedding IS NULL AND embedding_dimensions IS NULL AND embedding_model IS NULL) "
        f"OR (embedding IS NOT NULL AND embedding_dimensions = {dimension} "
        "AND embedding_model IS NOT NULL)",
    )
    op.create_index(
        "ix_memories_embedding_hnsw",
        "memories",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_where=sa.text("embedding IS NOT NULL"),
    )


def upgrade() -> None:
    _change_dimension(1024)


def downgrade() -> None:
    _change_dimension(384)
