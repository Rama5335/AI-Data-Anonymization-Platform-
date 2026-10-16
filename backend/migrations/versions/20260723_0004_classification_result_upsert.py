"""Add classification result attempts/ok/error and uniqueness.

Revision ID: 20260723_0004
Revises: 20260723_0003
Create Date: 2026-07-23
"""

from alembic import op
import sqlalchemy as sa

revision = "20260723_0004"
down_revision = "20260723_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "classification_results",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "classification_results",
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "classification_results",
        sa.Column("error", sa.Text(), nullable=True),
    )
    with op.batch_alter_table("classification_results") as batch_op:
        batch_op.create_unique_constraint(
            "uq_classification_result_column",
            ["job_id", "schema_name", "table_name", "column_name"],
        )


def downgrade() -> None:
    with op.batch_alter_table("classification_results") as batch_op:
        batch_op.drop_constraint("uq_classification_result_column", type_="unique")
    op.drop_column("classification_results", "error")
    op.drop_column("classification_results", "ok")
    op.drop_column("classification_results", "attempts")
