"""Allow distinct command IDs to carry the same command payload.

Revision ID: 9f6c7d8e9012
Revises: 8e5b6c7d8f90
"""

from collections.abc import Sequence

from alembic import op

revision: str = "9f6c7d8e9012"
down_revision: str | None = "8e5b6c7d8f90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_command_records_fingerprint",
        "command_records",
        type_="unique",
    )


def downgrade() -> None:
    # Older code treated identical payloads under distinct command IDs as the
    # same command.  Retain one row per fingerprint before restoring that rule.
    op.execute(
        "DELETE FROM command_records newer USING command_records older "
        "WHERE newer.run_id = older.run_id "
        "AND newer.fingerprint = older.fingerprint "
        "AND newer.command_id > older.command_id"
    )
    op.create_unique_constraint(
        "uq_command_records_fingerprint",
        "command_records",
        ["run_id", "fingerprint"],
    )
