"""Add backup_records table.

Persists backup metadata (path, size, verification result, owning
job, requesting user) so the safe-update gate can require a verified
backup before any target-data modification, and so backup attempts
are durable and auditable.

Revision ID: 20261015_0008
Revises: 20260723_0007
Create Date: 2026-10-15
"""

from alembic import op
import sqlalchemy as sa

revision = "20261015_0008"
down_revision = "20260723_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backup_records",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=True),
        sa.Column("database_name", sa.String(length=128), nullable=False),
        # ``backup_path`` participates in a UNIQUE constraint below, so it
        # must be a bounded string type. SQL Server cannot use TEXT /
        # VARCHAR(MAX) / NVARCHAR(MAX) as an index key column (error 1919),
        # which is exactly what sa.Text() maps to on MSSQL. 512 bytes is
        # far larger than any generated ``<backup_dir>/<db>_<ts>.bak`` path.
        sa.Column("backup_path", sa.String(length=512), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("verification_method", sa.String(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_by_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("backup_path", name="uq_backup_record_path"),
    )
    op.create_index("ix_backup_records_job_id", "backup_records", ["job_id"])
    op.create_index("ix_backup_records_database_name", "backup_records", ["database_name"])


def downgrade() -> None:
    op.drop_index("ix_backup_records_database_name", "backup_records")
    op.drop_index("ix_backup_records_job_id", "backup_records")
    op.drop_table("backup_records")
