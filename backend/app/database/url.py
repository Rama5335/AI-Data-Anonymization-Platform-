"""Build SQLAlchemy URLs from validated connection request payloads."""

from sqlalchemy.engine import URL

from app.schemas.discovery import ConnectionDetails


def build_mssql_url(details: ConnectionDetails) -> URL:
    """Convert request-scoped connection details into a one-shot mssql+pyodbc URL.

    The returned URL is intentionally short-lived: callers should pass it
    straight to ``MssqlDiscoveryClient`` and let it be garbage-collected with
    the request. No URL is logged.
    """
    if details.auth_type == "sql":
        if not details.username or not details.password:
            raise ValueError("SQL authentication requires both username and password.")
        password = details.password.get_secret_value()
    else:
        password = None

    query: dict[str, str] = {
        "driver": details.driver,
        "Encrypt": "yes" if details.encrypt else "no",
        "TrustServerCertificate": ("yes" if details.trust_server_certificate else "no"),
        "Connection Timeout": str(details.timeout_seconds),
    }

    return URL.create(
        drivername="mssql+pyodbc",
        username=details.username,
        password=password,
        host=details.host,
        port=details.port,
        database=details.database,
        query=query,
    )
