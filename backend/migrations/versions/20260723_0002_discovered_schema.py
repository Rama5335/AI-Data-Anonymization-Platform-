"""Add discovered schema tables.

Revision ID: 20260723_0002
Revises: 20260722_0001
Create Date: 2026-07-23
"""

from alembic import op
import sqlalchemy as sa

revision = "20260723_0002"
down_revision = "20260722_0001"
branch_labels = None
depends_on = None


def _id_column() -> sa.Column:
    return sa.Column("id", sa.String(length=36), primary_key=True)


def upgrade() -> None:
    op.create_table(
        "discovered_tables",
        _id_column(),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("schema_name", sa.String(128), nullable=False),
        sa.Column("table_name", sa.String(128), nullable=False),
        sa.Column("object_type", sa.String(32), nullable=False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "job_id", "schema_name", "table_name", name="uq_discovered_table"
        ),
    )
    op.create_index(
        "ix_discovered_tables_job_id", "discovered_tables", ["job_id"]
    )

    op.create_table(
        "discovered_columns",
        _id_column(),
        sa.Column(
            "table_id",
            sa.String(36),
            sa.ForeignKey("discovered_tables.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("ordinal_position", sa.Integer(), nullable=False),
        sa.Column("data_type", sa.String(64), nullable=False),
        sa.Column("is_nullable", sa.Boolean(), nullable=False),
        sa.Column("column_default", sa.Text(), nullable=True),
        sa.Column("character_maximum_length", sa.Integer(), nullable=True),
        sa.Column("numeric_precision", sa.Integer(), nullable=True),
        sa.Column("numeric_scale", sa.Integer(), nullable=True),
        sa.Column("is_primary_key", sa.Boolean(), nullable=False),
        sa.Column("is_foreign_key", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("table_id", "name", name="uq_discovered_column"),
    )
    op.create_index(
        "ix_discovered_columns_table_id", "discovered_columns", ["table_id"]
    )

    op.create_table(
        "discovered_foreign_keys",
        _id_column(),
        sa.Column(
            "column_id",
            sa.String(36),
            sa.ForeignKey("discovered_columns.id"),
            nullable=False,
        ),
        sa.Column("fk_name", sa.String(128), nullable=False),
        sa.Column("referenced_schema", sa.String(128), nullable=False),
        sa.Column("referenced_table", sa.String(128), nullable=False),
        sa.Column("referenced_column", sa.String(128), nullable=False),
        sa.UniqueConstraint("column_id", name="uq_discovered_fk_column"),
    )
    op.create_index(
        "ix_discovered_foreign_keys_column_id",
        "discovered_foreign_keys",
        ["column_id"],
    )

    op.create_table(
        "discovered_indexes",
        _id_column(),
        sa.Column(
            "table_id",
            sa.String(36),
            sa.ForeignKey("discovered_tables.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("is_unique", sa.Boolean(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("column_names", sa.JSON(), nullable=False),
        sa.UniqueConstraint("table_id", "name", name="uq_discovered_index"),
    )
    op.create_index(
        "ix_discovered_indexes_table_id", "discovered_indexes", ["table_id"]
    )

    op.create_table(
        "discovered_constraints",
        _id_column(),
        sa.Column(
            "table_id",
            sa.String(36),
            sa.ForeignKey("discovered_tables.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("constraint_type", sa.String(32), nullable=False),
        sa.UniqueConstraint(
            "table_id", "name", "constraint_type", name="uq_discovered_constraint"
        ),
    )
    op.create_index(
        "ix_discovered_constraints_table_id",
        "discovered_constraints",
        ["table_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_discovered_constraints_table_id", "discovered_constraints")
    op.drop_table("discovered_constraints")
    op.drop_index("ix_discovered_indexes_table_id", "discovered_indexes")
    op.drop_table("discovered_indexes")
    op.drop_index("ix_discovered_foreign_keys_column_id", "discovered_foreign_keys")
    op.drop_table("discovered_foreign_keys")
    op.drop_index("ix_discovered_columns_table_id", "discovered_columns")
    op.drop_table("discovered_columns")
    op.drop_index("ix_discovered_tables_job_id", "discovered_tables")
    op.drop_table("discovered_tables")
