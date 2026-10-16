"""Unit tests for the URL builder and the TABLE->BASE TABLE mapping fix."""

from unittest.mock import MagicMock, patch

from app.database.url import build_mssql_url
from app.schemas.discovery import ConnectionDetails


def test_build_mssql_url_uses_allow_listed_query_options() -> None:
    details = ConnectionDetails(
        host="mssql.local",
        port=1433,
        auth_type="sql",
        username="sa",
        password="P@ssw0rd!",
        database="appdb",
        driver="ODBC Driver 18 for SQL Server",
        encrypt=True,
        trust_server_certificate=True,
        timeout_seconds=7,
    )
    url = build_mssql_url(details)
    assert url.drivername == "mssql+pyodbc"
    assert url.host == "mssql.local"
    assert url.port == 1433
    assert url.database == "appdb"
    assert url.username == "sa"
    assert url.password == "P@ssw0rd!"
    assert url.query["driver"] == "ODBC Driver 18 for SQL Server"
    assert url.query["Encrypt"] == "yes"
    assert url.query["TrustServerCertificate"] == "yes"
    assert url.query["Connection Timeout"] == "7"


def test_build_mssql_url_rejects_sql_without_password() -> None:
    details = ConnectionDetails(
        host="mssql.local",
        port=1433,
        auth_type="sql",
        username="sa",
        database="appdb",
    )
    import pytest

    with pytest.raises(ValueError):
        build_mssql_url(details)


def test_list_objects_translates_table_to_base_table() -> None:
    captured: dict[str, str] = {}

    def fake_execute(query: object, params: dict[str, str]) -> MagicMock:
        captured.update(params)
        result = MagicMock()
        result.mappings.return_value.all.return_value = []
        return result

    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value.execute.side_effect = fake_execute
    with patch("app.database.mssql.create_engine", return_value=engine):
        from app.database.mssql import MssqlDiscoveryClient

        client = MssqlDiscoveryClient("mssql+pyodbc://example")
        client.list_objects("TABLE")
    assert captured == {"catalog_type": "BASE TABLE"}
