"""Align persisted Goal status with the confirmed runtime contract.

Revision ID: b18e9f0a1234
Revises: a07d8e9f0123
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b18e9f0a1234"
down_revision: str | None = "a07d8e9f0123"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_goals_status", "goals", type_="check")
    op.execute("UPDATE goals SET status = 'achieved' WHERE status = 'completed'")
    # ``departed`` belongs to Actor state, never Goal state. Preserve the
    # terminal meaning of any historical row while bringing it into contract.
    op.execute("UPDATE goals SET status = 'abandoned' WHERE status = 'departed'")
    op.create_check_constraint(
        "ck_goals_status",
        "goals",
        "status IN ('active', 'blocked', 'achieved', 'abandoned')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_goals_status", "goals", type_="check")
    op.execute("UPDATE goals SET status = 'completed' WHERE status = 'achieved'")
    op.create_check_constraint(
        "ck_goals_status",
        "goals",
        "status IN ('active', 'blocked', 'completed', 'abandoned', 'departed')",
    )
