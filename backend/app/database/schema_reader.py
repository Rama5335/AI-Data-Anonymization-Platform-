"""Read-only MSSQL schema reader; never executes DDL or DML.

Each method targets a single, fixed catalog query. The agent (see
``app.agents.schema``) composes these into a full per-table metadata
record and persists it to the application database.
"""

from dataclasses import dataclass, field

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL


@dataclass(frozen=True)
class ColumnInfo:
    """A single column discovered via INFORMATION_SCHEMA.COLUMNS."""

    name: str
    ordinal_position: int
    data_type: str
    is_nullable: bool
    column_default: str | None
    character_maximum_length: int | None
    numeric_precision: int | None
    numeric_scale: int | None


@dataclass(frozen=True)
class ForeignKeyInfo:
    """An outbound foreign key from a discovered column."""

    fk_name: str
    column_name: str
    referenced_schema: str
    referenced_table: str
    referenced_column: str


@dataclass(frozen=True)
class IndexInfo:
    """An index on a discovered table."""

    name: str
    is_unique: bool
    is_primary: bool
    column_names: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ConstraintInfo:
    """A table-level constraint that is not a PK or FK."""

    name: str
    constraint_type: str  # CHECK, UNIQUE


class MssqlSchemaReader:
    """Read-only inspection of per-table metadata for a single MSSQL database."""

    def __init__(self, connection_url: URL | str, timeout_seconds: int = 15) -> None:
        self._engine: Engine = create_engine(
            connection_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            connect_args={"timeout": timeout_seconds},
        )

    def dispose(self) -> None:
        self._engine.dispose()

    def read_columns(self, schema_name: str, table_name: str) -> list[ColumnInfo]:
        """Return every column for the given table, in ordinal order."""
        query = text(
            """
            SELECT
                COLUMN_NAME, ORDINAL_POSITION, DATA_TYPE, IS_NULLABLE,
                COLUMN_DEFAULT, CHARACTER_MAXIMUM_LENGTH,
                NUMERIC_PRECISION, NUMERIC_SCALE
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = :schema_name AND TABLE_NAME = :table_name
            ORDER BY ORDINAL_POSITION
            """
        )
        with self._engine.connect() as conn:
            rows = (
                conn.execute(query, {"schema_name": schema_name, "table_name": table_name})
                .mappings()
                .all()
            )
        return [
            ColumnInfo(
                name=row["COLUMN_NAME"],
                ordinal_position=int(row["ORDINAL_POSITION"]),
                data_type=row["DATA_TYPE"],
                is_nullable=row["IS_NULLABLE"].upper() == "YES",
                column_default=row["COLUMN_DEFAULT"],
                character_maximum_length=row["CHARACTER_MAXIMUM_LENGTH"],
                numeric_precision=row["NUMERIC_PRECISION"],
                numeric_scale=row["NUMERIC_SCALE"],
            )
            for row in rows
        ]

    def read_primary_key_columns(self, schema_name: str, table_name: str) -> list[str]:
        """Return the ordered list of columns that make up the table's PK."""
        query = text(
            """
            SELECT kcu.COLUMN_NAME
            FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
                ON tc.CONSTRAINT_NAME = kcu.CONSTRAINT_NAME
                AND tc.TABLE_SCHEMA = kcu.TABLE_SCHEMA
            WHERE tc.TABLE_SCHEMA = :schema_name
                AND tc.TABLE_NAME = :table_name
                AND tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
            ORDER BY kcu.ORDINAL_POSITION
            """
        )
        with self._engine.connect() as conn:
            return [
                row[0]
                for row in conn.execute(
                    query,
                    {"schema_name": schema_name, "table_name": table_name},
                ).all()
            ]

    def read_foreign_keys(self, schema_name: str, table_name: str) -> list[ForeignKeyInfo]:
        """Return every outbound foreign key for the given table."""
        query = text(
            """
            SELECT
                fk.name AS fk_name,
                c.name AS column_name,
                rs.name AS referenced_schema,
                rt.name AS referenced_table,
                rc.name AS referenced_column
            FROM sys.foreign_keys fk
            JOIN sys.foreign_key_columns fkc
                ON fk.object_id = fkc.constraint_object_id
            JOIN sys.columns c
                ON fkc.parent_object_id = c.object_id
                AND fkc.parent_column_id = c.column_id
            JOIN sys.tables rt
                ON fk.referenced_object_id = rt.object_id
            JOIN sys.schemas rs
                ON rt.schema_id = rs.schema_id
            JOIN sys.columns rc
                ON fkc.referenced_object_id = rc.object_id
                AND fkc.referenced_column_id = rc.column_id
            WHERE fk.parent_object_id = OBJECT_ID(
                QUOTENAME(:schema_name) + '.' + QUOTENAME(:table_name)
            )
            ORDER BY fk.name, fkc.constraint_column_id
            """
        )
        with self._engine.connect() as conn:
            rows = (
                conn.execute(
                    query,
                    {"schema_name": schema_name, "table_name": table_name},
                )
                .mappings()
                .all()
            )
        return [
            ForeignKeyInfo(
                fk_name=row["fk_name"],
                column_name=row["column_name"],
                referenced_schema=row["referenced_schema"],
                referenced_table=row["referenced_table"],
                referenced_column=row["referenced_column"],
            )
            for row in rows
        ]

    def read_indexes(self, schema_name: str, table_name: str) -> list[IndexInfo]:
        """Return every non-hypothetical index for the given table."""
        query = text(
            """
            SELECT
                i.name AS index_name,
                i.is_unique,
                i.is_primary_key,
                STRING_AGG(c.name, ',') WITHIN GROUP (ORDER BY ic.key_ordinal) AS cols
            FROM sys.indexes i
            JOIN sys.index_columns ic
                ON i.object_id = ic.object_id AND i.index_id = ic.index_id
            JOIN sys.columns c
                ON ic.object_id = c.object_id AND ic.column_id = c.column_id
            WHERE i.object_id = OBJECT_ID(
                QUOTENAME(:schema_name) + '.' + QUOTENAME(:table_name)
            )
                AND i.is_hypothetical = 0
                AND i.name IS NOT NULL
            GROUP BY i.name, i.is_unique, i.is_primary_key
            ORDER BY i.name
            """
        )
        with self._engine.connect() as conn:
            rows = (
                conn.execute(
                    query,
                    {"schema_name": schema_name, "table_name": table_name},
                )
                .mappings()
                .all()
            )
        return [
            IndexInfo(
                name=row["index_name"],
                is_unique=bool(row["is_unique"]),
                is_primary=bool(row["is_primary_key"]),
                column_names=row["cols"].split(",") if row["cols"] else [],
            )
            for row in rows
        ]

    def read_constraints(self, schema_name: str, table_name: str) -> list[ConstraintInfo]:
        """Return CHECK and UNIQUE constraints for the given table.

        Primary keys are reported via ``read_primary_key_columns``; this method
        returns only the remaining kinds the user might care about.
        """
        query = text(
            """
            SELECT CONSTRAINT_NAME, CONSTRAINT_TYPE
            FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS
            WHERE TABLE_SCHEMA = :schema_name
                AND TABLE_NAME = :table_name
                AND CONSTRAINT_TYPE IN ('CHECK', 'UNIQUE')
            ORDER BY CONSTRAINT_NAME
            """
        )
        with self._engine.connect() as conn:
            rows = (
                conn.execute(
                    query,
                    {"schema_name": schema_name, "table_name": table_name},
                )
                .mappings()
                .all()
            )
        return [
            ConstraintInfo(
                name=row["CONSTRAINT_NAME"],
                constraint_type=row["CONSTRAINT_TYPE"],
            )
            for row in rows
        ]
