"""CSV/JSON/SQLite を DuckDB 上で横断クエリするツール。"""

from __future__ import annotations

import json
import re
import sqlite3
import tempfile
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from data_agent_baseline.benchmark.schema import PublicTask
from data_agent_baseline.tools.filesystem import resolve_context_path

READ_ONLY_PREFIXES = ("select", "with", "pragma", "describe", "show")
JSON_LOAD_LIMIT_BYTES = 512 * 1024 * 1024
IDENTIFIER_PATTERN = re.compile(r"[^A-Za-z0-9_]+")


def _quote_sql_string(value: str) -> str:
    """SQL 文字列リテラル用に single quote を escape する。"""

    return value.replace("'", "''")


def _json_safe(value: Any) -> Any:
    """DuckDB/Pandas の戻り値を trace JSON に書ける値へ変換する。"""

    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except (TypeError, ValueError):
            pass
    return str(value)


def _safe_identifier(raw_name: str, used: set[str]) -> str:
    """DuckDB view/schema 名に使える identifier を作る。"""

    candidate = IDENTIFIER_PATTERN.sub("_", raw_name).strip("_").lower()
    if not candidate or candidate[0].isdigit():
        candidate = f"t_{candidate}"
    base = candidate
    suffix = 2
    while candidate in used:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _sqlite_tables(path: Path) -> list[str]:
    """SQLite 内のユーザー定義 table 名を返す。"""

    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        return [
            str(row[0])
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            ).fetchall()
        ]


def _load_json_records(path: Path) -> tuple[str, pd.DataFrame]:
    """JSON を DataFrame 化する。records wrapper と配列を優先して扱う。"""

    if path.stat().st_size > JSON_LOAD_LIMIT_BYTES:
        raise ValueError(f"JSON file is too large to load safely: {path.name}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    table_name = path.stem
    records: Any
    if isinstance(payload, dict) and isinstance(payload.get("records"), list):
        table_name = str(payload.get("table") or table_name)
        records = payload["records"]
    elif isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        records = [payload]
    else:
        records = [{"value": payload}]
    return table_name, pd.DataFrame.from_records(records)


def _resolve_sources(task: PublicTask, requested_sources: list[str] | None) -> list[Path]:
    """対象 structured files を context 内から解決する。"""

    if requested_sources:
        return [resolve_context_path(task, source) for source in requested_sources]
    return [
        path
        for path in sorted(task.context_dir.rglob("*"))
        if path.is_file() and path.suffix.lower() in {".csv", ".json", ".db", ".sqlite", ".sqlite3"}
    ]


def _register_csv(conn: duckdb.DuckDBPyConnection, path: Path, alias: str) -> dict[str, Any]:
    """CSV を DuckDB view として登録する。"""

    sql_path = _quote_sql_string(path.as_posix())
    conn.execute(
        f"""
        CREATE VIEW "{alias}" AS
        SELECT * FROM read_csv_auto('{sql_path}', header=true, ignore_errors=true)
        """
    )
    count = conn.execute(f'SELECT COUNT(*) FROM "{alias}"').fetchone()[0]
    columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{alias}")').fetchall()]
    return {"path": path.as_posix(), "alias": alias, "type": "csv", "columns": columns, "row_count": count}


def _register_json(
    conn: duckdb.DuckDBPyConnection,
    path: Path,
    alias: str,
    used_identifiers: set[str],
) -> dict[str, Any]:
    """JSON records を DuckDB view として登録する。"""

    table_name, dataframe = _load_json_records(path)
    registered_aliases = [alias]
    conn.register(alias, dataframe)

    natural_alias = _safe_identifier(table_name, used_identifiers)
    if natural_alias != alias:
        conn.register(natural_alias, dataframe)
        registered_aliases.append(natural_alias)

    return {
        "path": path.as_posix(),
        "alias": alias,
        "aliases": registered_aliases,
        "type": "json",
        "columns": list(dataframe.columns),
        "row_count": len(dataframe),
    }


def _register_sqlite(
    conn: duckdb.DuckDBPyConnection,
    path: Path,
    schema_alias: str,
    used_identifiers: set[str],
) -> dict[str, Any]:
    """SQLite DB を DuckDB に attach し、単一 table は view alias も作る。"""

    conn.execute("LOAD sqlite")
    sql_path = _quote_sql_string(path.as_posix())
    conn.execute(f"ATTACH '{sql_path}' AS \"{schema_alias}\" (TYPE SQLITE, READ_ONLY)")

    table_infos: list[dict[str, Any]] = []
    tables = _sqlite_tables(path)
    for table_name in tables:
        columns = [
            row[1]
            for row in conn.execute(
                f'PRAGMA table_info("{schema_alias}"."{table_name}")'
            ).fetchall()
        ]
        row_count = conn.execute(
            f'SELECT COUNT(*) FROM "{schema_alias}"."{table_name}"'
        ).fetchone()[0]
        table_infos.append({"name": table_name, "columns": columns, "row_count": row_count})

    view_alias = None
    if len(tables) == 1:
        preferred_name = tables[0] or Path(path).stem
        view_alias = _safe_identifier(preferred_name, used_identifiers)
        conn.execute(
            f'CREATE VIEW "{view_alias}" AS SELECT * FROM "{schema_alias}"."{tables[0]}"'
        )
    return {
        "path": path.as_posix(),
        "alias": schema_alias,
        "view_alias": view_alias,
        "type": "sqlite",
        "tables": table_infos,
    }


def execute_data_query(
    task: PublicTask,
    *,
    sql: str,
    sources: list[str] | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """structured context files を DuckDB に登録して読み取り SQL を実行する。"""

    normalized_sql = sql.lstrip().lower()
    if not normalized_sql.startswith(READ_ONLY_PREFIXES):
        raise ValueError("Only read-only SQL statements are allowed.")

    source_paths = _resolve_sources(task, sources)
    if not source_paths:
        raise FileNotFoundError("No structured CSV/JSON/SQLite sources found in context.")

    limit = max(1, min(int(limit), 1000))
    registered: list[dict[str, Any]] = []
    used_identifiers: set[str] = set()

    with tempfile.TemporaryDirectory() as temp_dir:
        conn = duckdb.connect(database=":memory:")
        conn.execute(f"PRAGMA temp_directory='{_quote_sql_string(temp_dir)}'")
        try:
            for source_path in source_paths:
                rel_path = source_path.relative_to(task.context_dir.resolve()).as_posix()
                suffix = source_path.suffix.lower()
                if suffix == ".csv":
                    alias = _safe_identifier(source_path.stem, used_identifiers)
                    registered.append(_register_csv(conn, source_path, alias) | {"relative_path": rel_path})
                elif suffix == ".json":
                    alias = _safe_identifier(source_path.stem, used_identifiers)
                    registered.append(
                        _register_json(conn, source_path, alias, used_identifiers)
                        | {"relative_path": rel_path}
                    )
                elif suffix in {".db", ".sqlite", ".sqlite3"}:
                    sqlite_alias = _safe_identifier(f"{source_path.stem}_db", used_identifiers)
                    registered.append(
                        _register_sqlite(conn, source_path, sqlite_alias, used_identifiers)
                        | {"relative_path": rel_path}
                    )
                else:
                    raise ValueError(f"Unsupported source type: {rel_path}")

            cursor = conn.execute(sql)
            columns = [item[0] for item in cursor.description or []]
            rows = cursor.fetchmany(limit + 1)
        finally:
            conn.close()

    truncated = len(rows) > limit
    return {
        "registered_sources": _json_safe(registered),
        "columns": columns,
        "rows": [_json_safe(list(row)) for row in rows[:limit]],
        "row_count": min(len(rows), limit),
        "truncated": truncated,
    }
