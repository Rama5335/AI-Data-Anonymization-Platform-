"""Read-only MSSQL row sampler; never executes DDL or DML.

Sampling rules (per Phase 5 plan):

* Always ``SELECT TOP N`` with no ``ORDER BY`` — no forced full scans.
* Always ``WITH (NOLOCK)`` — never blocks on a target table.
* Large-object columns (``text``/``ntext``/``xml``/``image``/``(N)VARCHAR(MAX)``/
  ``VARBINARY(MAX)``) are truncated at the SQL layer to the configured byte
  cap. The original column size is also fetched via ``LEN`` so the API can
  flag ``truncated: true`` and report ``original_length`` even after the
  value has been cut down.
* Bounded character/binary columns whose declared length already fits
  under the cap are read as-is — no spurious truncation flags.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL


DEFAULT_MAX_BYTES_PER_VALUE = 512
DEFAULT_ROW_COUNT = 5
MAX_ROW_COUNT = 1000


# Types whose size is interesting and which may be truncated.
_SIZEABLE_TYPES = frozenset(
    {
        "nvarchar",
        "varchar",
        "nchar",
        "char",
        "text",
        "ntext",
        "binary",
        "varbinary",
        "image",
        "xml",
    }
)


@dataclass(frozen=True)
class ColumnSpec:
    """A column the sampler can include in its ``SELECT`` projection."""

    name: str
    data_type: str
    character_maximum_length: int | None


def _coerce_cell(
    raw: Any,
    data_type: str,
    max_bytes: int,
    original_length: int | None,
) -> dict[str, Any]:
    """Convert a driver-returned value into a JSON-friendly cell dict.

    ``original_length`` is the pre-truncation size of the column (in
    characters for character types, in bytes for binary) as reported by
    SQL Server's ``LEN`` function. The ``truncated`` flag is derived
    from it: any cell whose original length exceeds the cap is flagged.
    """
    if raw is None:
        return {"value": None, "truncated": False, "original_length": original_length}

    dt = data_type.lower()
    if dt in {"varbinary", "binary", "image"}:
        data = bytes(raw)
        return {
            "value": data.hex(),
            "truncated": original_length is not None and original_length > max_bytes,
            "original_length": original_length if original_length is not None else len(data),
        }
    if isinstance(raw, bool):
        return {"value": raw, "truncated": False, "original_length": original_length}
    if isinstance(raw, (int, float)):
        return {"value": raw, "truncated": False, "original_length": original_length}
    if isinstance(raw, datetime):
        return {"value": raw.isoformat(), "truncated": False, "original_length": original_length}
    if isinstance(raw, bytes):
        return {
            "value": raw.hex(),
            "truncated": original_length is not None and original_length > max_bytes,
            "original_length": original_length if original_length is not None else len(raw),
        }
    if isinstance(raw, str):
        if original_length is None:
            original_length = len(raw.encode("utf-8"))
        return {
            "value": raw,
            "truncated": original_length > max_bytes,
            "original_length": original_length,
        }
    s = str(raw)
    return {"value": s, "truncated": False, "original_length": original_length}


def _quote_identifier(name: str) -> str:
    """Bracket-quote a SQL Server identifier."""
    escaped = name.replace("]", "]]")
    return f"[{escaped}]"


def _needs_truncation(col: ColumnSpec, max_bytes: int) -> bool:
    """Return True if a column may exceed the cap and needs SQL-side CAST."""
    dt = col.data_type.lower()
    if dt not in _SIZEABLE_TYPES:
        return False
    max_len = col.character_maximum_length
    if max_len is None or max_len < 0:
        return True
    if dt in {"nvarchar", "nchar"}:
        return max_len > max_bytes // 2
    return max_len > max_bytes


def _build_select_expression(col: ColumnSpec, max_bytes: int) -> str:
    """Return a SQL expression for selecting ``col`` with the cap enforced.

    Numeric, decimal, datetime, bit, and other fixed-size types are
    returned as-is. Bounded character/binary columns whose declared
    length already fits under the cap are also returned as-is.
    Anything that could exceed the cap is wrapped in a ``CAST``
    expression that bounds it at the SQL layer.
    """
    name = _quote_identifier(col.name)
    dt = col.data_type.lower()
    max_len = col.character_maximum_length

    if dt in {"text", "ntext", "xml"}:
        char_cap = max(1, max_bytes // 2)
        return f"CAST(CAST({name} AS NVARCHAR(MAX)) AS NVARCHAR({char_cap}))"
    if dt in {"nvarchar", "nchar"}:
        char_cap = max(1, max_bytes // 2)
        if max_len is None or max_len < 0 or max_len > char_cap:
            return f"CAST(CAST({name} AS NVARCHAR(MAX)) AS NVARCHAR({char_cap}))"
        return name
    if dt in {"varchar", "char"}:
        char_cap = max(1, max_bytes)
        if max_len is None or max_len < 0 or max_len > char_cap:
            return f"CAST(CAST({name} AS NVARCHAR(MAX)) AS NVARCHAR({char_cap}))"
        return name
    if dt in {"varbinary", "binary", "image"}:
        byte_cap = max(1, max_bytes)
        if max_len is None or max_len < 0 or max_len > byte_cap:
            return f"CAST(CAST({name} AS VARBINARY(MAX)) AS VARBINARY({byte_cap}))"
        return name
    return name


class MssqlSampler:
    """Read-only sampling for a single MSSQL database."""

    def __init__(self, connection_url: URL | str, timeout_seconds: int = 15) -> None:
        self._engine: Engine = create_engine(
            connection_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            connect_args={"timeout": timeout_seconds},
        )

    def dispose(self) -> None:
        """Release pooled connections."""
        self._engine.dispose()

    def fetch_samples(
        self,
        schema_name: str,
        table_name: str,
        columns: list[ColumnSpec],
        row_count: int = DEFAULT_ROW_COUNT,
        max_bytes_per_value: int = DEFAULT_MAX_BYTES_PER_VALUE,
    ) -> list[dict[str, Any]]:
        """Fetch up to ``row_count`` rows from a table, with per-cell metadata.

        Each returned row is a dict mapping the original column name to a
        cell dict with ``value``, ``truncated``, and ``original_length``
        keys. The caller is responsible for persisting this structure.
        """
        if row_count < 1:
            raise ValueError("row_count must be >= 1")
        if row_count > MAX_ROW_COUNT:
            raise ValueError(f"row_count must be <= {MAX_ROW_COUNT}")
        if not columns:
            raise ValueError("At least one column must be sampled.")
        if max_bytes_per_value < 1:
            raise ValueError("max_bytes_per_value must be >= 1")

        spec_by_name: dict[str, ColumnSpec] = {}
        select_parts: list[str] = []
        needs_orig: dict[str, bool] = {}
        for col in columns:
            spec_by_name[col.name] = col
            quoted = _quote_identifier(col.name)
            expr = _build_select_expression(col, max_bytes_per_value)
            select_parts.append(f"{expr} AS {quoted}")
            if col.data_type.lower() in _SIZEABLE_TYPES:
                orig_quoted = _quote_identifier(f"{col.name}__orig")
                select_parts.append(f"LEN({quoted}) AS {orig_quoted}")
                needs_orig[col.name] = True

        quoted_schema = _quote_identifier(schema_name)
        quoted_table = _quote_identifier(table_name)
        query_str = (
            f"SELECT TOP (:n) {', '.join(select_parts)} "
            f"FROM {quoted_schema}.{quoted_table} WITH (NOLOCK)"
        )
        query = text(query_str)

        with self._engine.connect() as conn:
            result = conn.execute(query, {"n": row_count})
            column_names = list(result.keys())
            rows = result.fetchall()

        orig_index = {name: idx for idx, name in enumerate(column_names) if name.endswith("__orig")}
        samples: list[dict[str, Any]] = []
        for row in rows:
            cells: dict[str, Any] = {}
            for idx, name in enumerate(column_names):
                if name.endswith("__orig"):
                    continue
                spec = spec_by_name.get(name)
                data_type = spec.data_type if spec is not None else "nvarchar"
                orig_value = None
                if name in needs_orig:
                    orig_idx = orig_index.get(f"{name}__orig")
                    if orig_idx is not None and row[orig_idx] is not None:
                        orig_value = int(row[orig_idx])
                cells[name] = _coerce_cell(row[idx], data_type, max_bytes_per_value, orig_value)
            samples.append(cells)
        return samples
