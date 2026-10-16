"""Add discovered_samples table.

Revision ID: 20260723_0003
Revises: 20260723_0002
Create Date: 2026-07-23
"""

from alembic import op
import sqlalchemy as sa

revision = "20260723_0003"
down_revision = "20260723_0002"
branch_labels = None
depends_on = None


def _id_column() -> sa.Column:
    return sa.Column("id", sa.String(length=36), primary_key=True)


def upgrade() -> None:
    op.create_table(
        "discovered_samples",
        _id_column(),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column(
            "table_id",
            sa.String(36),
            sa.ForeignKey("discovered_tables.id"),
            nullable=False,
        ),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("sampled_columns", sa.JSON(), nullable=False),
        sa.Column("max_bytes_per_value", sa.Integer(), nullable=False),
        sa.Column("rows", sa.JSON(), nullable=False),
        sa.Column("sampled_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_id", "table_id", name="uq_discovered_sample"),
    )
    op.create_index("ix_discovered_samples_job_id", "discovered_samples", ["job_id"])
    op.create_index("ix_discovered_samples_table_id", "discovered_samples", ["table_id"])


def downgrade() -> None:
    op.drop_index("ix_discovered_samples_table_id", "discovered_samples")
    op.drop_index("ix_discovered_samples_job_id", "discovered_samples")
    op.drop_table("discovered_samples")
