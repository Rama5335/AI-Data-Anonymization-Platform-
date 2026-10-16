"""Read-only MSSQL discovery client; never executes DDL or DML."""

from dataclasses import dataclass

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL


@dataclass(frozen=True)
class DatabaseObject:
    """A discoverable schema object."""

    schema_name: str
    name: str
    object_type: str


class MssqlDiscoveryClient:
    """Safely inspect MSSQL metadata through fixed, read-only catalog queries."""

    def __init__(self, connection_url: URL | str, timeout_seconds: int = 15) -> None:
        self._engine: Engine = create_engine(
            connection_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            connect_args={"timeout": timeout_seconds},
        )

    def test_connection(self) -> None:
        """Open and immediately validate a connection without changing target state."""
        with self._engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    def list_databases(self) -> list[str]:
        """Return online, non-snapshot user databases only."""
        query = text(
            "SELECT [name] FROM sys.databases "
            "WHERE [state] = 0 AND [source_database_id] IS NULL "
            "ORDER BY [name]"
        )
        return self._scalar_list(query)

    def list_schemas(self) -> list[str]:
        """Return user-defined schemas from the selected database."""
        return self._scalar_list(
            text("SELECT [name] FROM sys.schemas WHERE [principal_id] <> 1 ORDER BY [name]")
        )

    def list_objects(self, object_type: str) -> list[DatabaseObject]:
        """Return tables or views using a fixed allow-list of object types.

        INFORMATION_SCHEMA.TABLES.TABLE_TYPE stores the value ``BASE TABLE``
        (not ``TABLE``) for user tables, so the public ``"TABLE"`` alias is
        translated internally before the catalog query runs.
        """
        if object_type not in {"TABLE", "VIEW"}:
            raise ValueError("Object type must be TABLE or VIEW.")
        catalog_type = "BASE TABLE" if object_type == "TABLE" else "VIEW"
        query = text(
            "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE "
            "FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE = :catalog_type "
            "ORDER BY TABLE_SCHEMA, TABLE_NAME"
        )
        with self._engine.connect() as connection:
            rows = connection.execute(query, {"catalog_type": catalog_type}).mappings().all()
        return [
            DatabaseObject(
                schema_name=row["TABLE_SCHEMA"],
                name=row["TABLE_NAME"],
                object_type=row["TABLE_TYPE"],
            )
            for row in rows
        ]

    def dispose(self) -> None:
        """Release pooled connections."""
        self._engine.dispose()

    def _scalar_list(self, query: object) -> list[str]:
        with self._engine.connect() as connection:
            return [str(value) for value in connection.execute(query).scalars().all()]
