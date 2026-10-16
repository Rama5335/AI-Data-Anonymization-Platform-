"""Add User.role for basic RBAC.

Revision ID: 20260723_0007
Revises: 20260723_0006
Create Date: 2026-07-23
"""

from alembic import op
import sqlalchemy as sa

revision = "20260723_0007"
down_revision = "20260723_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("role", sa.String(length=32), nullable=False, server_default="user"),
    )


def downgrade() -> None:
    op.drop_column("users", "role")
