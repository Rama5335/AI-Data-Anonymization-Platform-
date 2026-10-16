from unittest.mock import MagicMock, patch

import pytest

from app.database.mssql import MssqlDiscoveryClient


def test_discovery_uses_read_only_connection_test() -> None:
    engine = MagicMock()
    with patch("app.database.mssql.create_engine", return_value=engine):
        client = MssqlDiscoveryClient("mssql+pyodbc://example")
        client.test_connection()
    engine.connect.return_value.__enter__.return_value.execute.assert_called_once()


def test_object_type_is_allow_listed() -> None:
    with patch("app.database.mssql.create_engine"):
        client = MssqlDiscoveryClient("mssql+pyodbc://example")
    with pytest.raises(ValueError):
        client.list_objects("PROCEDURE")
