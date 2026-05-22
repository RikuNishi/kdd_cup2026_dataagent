"""SQLite ファイルのスキーマ確認と読み取り専用クエリ実行を行うツール。"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def _connect_read_only(path: Path) -> sqlite3.Connection:
    """SQLite データベースを読み取り専用モードで開く。"""

    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def inspect_sqlite_schema(path: Path) -> dict[str, object]:
    """ユーザー定義テーブルの schema と先頭行 preview を返す。"""

    with _connect_read_only(path) as conn:
        rows = conn.execute(
            """
            SELECT name, sql
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()
        tables: list[dict[str, object]] = []
        for name, create_sql in rows:
            table_info = conn.execute(f'PRAGMA table_info("{name}")').fetchall()
            columns = [
                {
                    "name": row[1],
                    "type": row[2],
                    "not_null": bool(row[3]),
                    "default_value": row[4],
                    "primary_key": bool(row[5]),
                }
                for row in table_info
            ]
            row_count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            preview_cursor = conn.execute(f'SELECT * FROM "{name}" LIMIT 5')
            preview_columns = [item[0] for item in preview_cursor.description or []]
            preview_rows = [list(row) for row in preview_cursor.fetchall()]
            tables.append(
                {
                    "name": name,
                    "create_sql": create_sql,
                    "columns": columns,
                    "row_count": row_count,
                    "preview_columns": preview_columns,
                    "preview_rows": preview_rows,
                }
            )
    return {
        "path": str(path),
        "tables": tables,
    }


def execute_read_only_sql(path: Path, sql: str, *, limit: int = 200) -> dict[str, object]:
    """読み取り専用 SQL を実行し、件数制限付きの結果プレビューを返す。"""

    normalized_sql = sql.lstrip().lower()
    if not normalized_sql.startswith(("select", "with", "pragma")):
        raise ValueError("Only read-only SQL statements are allowed.")

    with _connect_read_only(path) as conn:
        cursor = conn.execute(sql)
        column_names = [item[0] for item in cursor.description or []]
        rows = cursor.fetchmany(limit + 1)

    truncated = len(rows) > limit
    limited_rows = rows[:limit]
    return {
        "path": str(path),
        "columns": column_names,
        "rows": [list(row) for row in limited_rows],
        "row_count": len(limited_rows),
        "truncated": truncated,
    }
