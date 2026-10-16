"""Add Approval unique constraint and excluded flag.

Revision ID: 20260723_0005
Revises: 20260723_0004
Create Date: 2026-07-23
"""

from alembic import op
import sqlalchemy as sa

revision = "20260723_0005"
down_revision = "20260723_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "approvals",
        sa.Column("excluded", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Deduplicate pre-existing rows so the unique constraint can be created.
    op.execute(
        """
        DELETE FROM approvals
        WHERE id NOT IN (
            SELECT MIN(id) FROM approvals
            GROUP BY classification_result_id
        )
        """
    )
    with op.batch_alter_table("approvals") as batch_op:
        batch_op.create_unique_constraint(
            "uq_approval_classification",
            ["classification_result_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("approvals") as batch_op:
        batch_op.drop_constraint("uq_approval_classification", type_="unique")
    op.drop_column("approvals", "excluded")
