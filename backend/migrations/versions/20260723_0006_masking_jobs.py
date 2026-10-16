"""Add masking_jobs and token_vault tables.

Revision ID: 20260723_0006
Revises: 20260723_0005
Create Date: 2026-07-23
"""

from alembic import op
import sqlalchemy as sa

revision = "20260723_0006"
down_revision = "20260723_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "masking_jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("salt_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("token_key_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("wiped_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_masking_jobs_job_id", "masking_jobs", ["job_id"])

    op.create_table(
        "token_vault",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("table_name", sa.String(length=128), nullable=False),
        sa.Column("column_name", sa.String(length=128), nullable=False),
        sa.Column("tokenized_value", sa.String(length=128), nullable=False),
        sa.Column("original_value", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "job_id",
            "table_name",
            "column_name",
            "tokenized_value",
            name="uq_token_vault_entry",
        ),
    )
    op.create_index("ix_token_vault_job_id", "token_vault", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_token_vault_job_id", "token_vault")
    op.drop_table("token_vault")
    op.drop_index("ix_masking_jobs_job_id", "masking_jobs")
    op.drop_table("masking_jobs")
