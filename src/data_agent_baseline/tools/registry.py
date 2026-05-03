"""ベースラインエージェント向けのツール登録とプロンプト用メタデータ。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from data_agent_baseline.benchmark.schema import AnswerTable, PublicTask
from data_agent_baseline.tools.answer_validation import validate_answer_table
from data_agent_baseline.tools.context_profile import build_context_profile, retrieve_context_chunks
from data_agent_baseline.tools.data_query import execute_data_query
from data_agent_baseline.tools.filesystem import (
    list_context_tree,
    read_csv_preview,
    read_doc_preview,
    read_json_preview,
    resolve_context_path,
)
from data_agent_baseline.tools.python_exec import execute_python_code
from data_agent_baseline.tools.sqlite import execute_read_only_sql, inspect_sqlite_schema

EXECUTE_PYTHON_TIMEOUT_SECONDS = 30


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """ReAct エージェントに公開するツール定義。"""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    """ツールハンドラが返す実行結果の共通形式。"""

    ok: bool
    content: dict[str, Any]
    is_terminal: bool = False
    answer: AnswerTable | None = None


ToolHandler = Callable[[PublicTask, dict[str, Any]], ToolExecutionResult]


def _list_context(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    """`context/` 配下の一覧取得リクエストを処理する。"""

    max_depth = int(action_input.get("max_depth", 4))
    return ToolExecutionResult(ok=True, content=list_context_tree(task, max_depth=max_depth))


def _read_csv(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    """CSV プレビュー取得リクエストを処理する。"""

    path = str(action_input["path"])
    max_rows = int(action_input.get("max_rows", 20))
    return ToolExecutionResult(ok=True, content=read_csv_preview(task, path, max_rows=max_rows))


def _read_json(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    """JSON プレビュー取得リクエストを処理する。"""

    path = str(action_input["path"])
    max_chars = int(action_input.get("max_chars", 4000))
    return ToolExecutionResult(ok=True, content=read_json_preview(task, path, max_chars=max_chars))


def _read_doc(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    """テキスト文書のプレビュー取得リクエストを処理する。"""

    path = str(action_input["path"])
    max_chars = int(action_input.get("max_chars", 4000))
    return ToolExecutionResult(ok=True, content=read_doc_preview(task, path, max_chars=max_chars))


def _inspect_sqlite_schema(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    """SQLite スキーマ確認リクエストを処理する。"""

    path = resolve_context_path(task, str(action_input["path"]))
    return ToolExecutionResult(ok=True, content=inspect_sqlite_schema(path))


def _execute_context_sql(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    """読み取り専用 SQL 実行リクエストを処理する。"""

    path = resolve_context_path(task, str(action_input["path"]))
    sql = str(action_input["sql"])
    limit = int(action_input.get("limit", 200))
    return ToolExecutionResult(ok=True, content=execute_read_only_sql(path, sql, limit=limit))


def _execute_python(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    """Python 実行リクエストを処理する。"""

    code = str(action_input["code"])
    content = execute_python_code(
        context_root=task.context_dir,
        code=code,
        timeout_seconds=EXECUTE_PYTHON_TIMEOUT_SECONDS,
    )
    return ToolExecutionResult(ok=bool(content.get("success")), content=content)


def _profile_context(task: PublicTask, _: dict[str, Any]) -> ToolExecutionResult:
    """context profile 取得リクエストを処理する。"""

    return ToolExecutionResult(ok=True, content=build_context_profile(task))


def _retrieve_context(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    """文書 chunk 検索リクエストを処理する。"""

    query = str(action_input.get("query") or task.question)
    raw_keywords = action_input.get("keywords")
    keywords = [str(item) for item in raw_keywords] if isinstance(raw_keywords, list) else None
    max_chunks = int(action_input.get("max_chunks", 5))
    max_chars_per_chunk = int(action_input.get("max_chars_per_chunk", 2000))
    return ToolExecutionResult(
        ok=True,
        content=retrieve_context_chunks(
            task,
            query=query,
            keywords=keywords,
            max_chunks=max_chunks,
            max_chars_per_chunk=max_chars_per_chunk,
        ),
    )


def _execute_data_query(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    """DuckDB 横断クエリリクエストを処理する。"""

    sql = str(action_input["sql"])
    raw_sources = action_input.get("sources")
    sources = [str(item) for item in raw_sources] if isinstance(raw_sources, list) else None
    limit = int(action_input.get("limit", 200))
    return ToolExecutionResult(
        ok=True,
        content=execute_data_query(task, sql=sql, sources=sources, limit=limit),
    )


def _validate_answer(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    """最終回答候補の検査リクエストを処理する。"""

    content = validate_answer_table(
        task,
        columns=action_input.get("columns"),
        rows=action_input.get("rows"),
        notes=str(action_input.get("notes", "")),
    )
    return ToolExecutionResult(ok=bool(content["ok"]), content=content)


def _answer(_: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    """最終回答テーブルを検証し、提出結果として返す。"""

    columns = action_input.get("columns")
    rows = action_input.get("rows")
    if not isinstance(columns, list) or not columns or not all(isinstance(item, str) for item in columns):
        raise ValueError("answer.columns must be a non-empty list of strings.")
    if not isinstance(rows, list):
        raise ValueError("answer.rows must be a list.")

    normalized_rows: list[list[Any]] = []
    for row in rows:
        if not isinstance(row, list):
            raise ValueError("Each answer row must be a list.")
        if len(row) != len(columns):
            raise ValueError("Each answer row must match the number of columns.")
        normalized_rows.append(list(row))

    answer = AnswerTable(columns=list(columns), rows=normalized_rows)
    return ToolExecutionResult(
        ok=True,
        content={
            "status": "submitted",
            "column_count": len(columns),
            "row_count": len(normalized_rows),
        },
        is_terminal=True,
        answer=answer,
    )


@dataclass(slots=True)
class ToolRegistry:
    """ツール定義と実行ハンドラを保持するレジストリ。"""

    specs: dict[str, ToolSpec]
    handlers: dict[str, ToolHandler]

    def describe_for_prompt(self) -> str:
        """エージェント用プロンプトに埋め込む説明文へ整形する。"""

        lines = []
        for name in sorted(self.specs):
            spec = self.specs[name]
            lines.append(f"- {spec.name}: {spec.description}")
            rendered_schema = json.dumps(spec.input_schema, ensure_ascii=False)
            lines.append(f"  action_input JSON example: {rendered_schema}")
        return "\n".join(lines)

    def execute(self, task: PublicTask, action: str, action_input: dict[str, Any]) -> ToolExecutionResult:
        """ツール名に対応するハンドラを実行する。"""

        if action not in self.handlers:
            raise KeyError(f"Unknown tool: {action}")
        return self.handlers[action](task, action_input)


def create_default_tool_registry() -> ToolRegistry:
    """ベンチマークエージェントに公開する標準ツール群を構築する。"""

    specs = {
        "answer": ToolSpec(
            name="answer",
            description=(
                "Submit the final answer table and terminate the task. "
                "Use this only when the prediction is ready."
            ),
            input_schema={
                "columns": ["column_name"],
                "rows": [["value_1"]],
            },
        ),
        "execute_context_sql": ToolSpec(
            name="execute_context_sql",
            description=(
                "Run read-only SQL against a sqlite/db file inside `context/`. "
                "Only use this for .db, .sqlite, or .sqlite3 files. "
                "For CSV/JSON joins, prefer execute_data_query or execute_python. "
                "Only SELECT, WITH, and PRAGMA statements are allowed."
            ),
            input_schema={"path": "relative/path/to/file.sqlite", "sql": "SELECT ...", "limit": 200},
        ),
        "execute_data_query": ToolSpec(
            name="execute_data_query",
            description=(
                "Register selected CSV, JSON, and SQLite files from `context/` in DuckDB and "
                "run a read-only SQL query across them. If sources is omitted, all structured "
                "CSV/JSON/SQLite files are registered. Use the returned registered_sources "
                "metadata to repair table or alias names."
            ),
            input_schema={
                "sources": ["csv/example.csv", "json/example.json", "db/example.db"],
                "sql": "SELECT * FROM example LIMIT 5",
                "limit": 200,
            },
        ),
        "execute_python": ToolSpec(
            name="execute_python",
            description=(
                "Run Python code with the task `context/` directory as the working directory. "
                "`stdout` is returned as `output`, and `stderr` is returned as `stderr`. "
                f"The execution timeout is {EXECUTE_PYTHON_TIMEOUT_SECONDS} seconds."
            ),
            input_schema={
                "code": "import os\nprint(sorted(os.listdir('.')))",
            },
        ),
        "inspect_sqlite_schema": ToolSpec(
            name="inspect_sqlite_schema",
            description=(
                "Inspect the schema of a sqlite/db file inside `context/`, "
                "returning user-defined tables and their CREATE statements."
            ),
            input_schema={"path": "relative/path/to/file.sqlite"},
        ),
        "profile_context": ToolSpec(
            name="profile_context",
            description=(
                "Build a deterministic profile of the task context before planning: files, "
                "modalities, CSV columns, JSON shape, SQLite schemas, document headings, "
                "knowledge.md location, and a difficulty-aware strategy."
            ),
            input_schema={},
        ),
        "list_context": ToolSpec(
            name="list_context",
            description=(
                "List files and directories under `context/` up to the requested depth. "
                "Returned paths are relative to `context/`."
            ),
            input_schema={"max_depth": 4},
        ),
        "read_csv": ToolSpec(
            name="read_csv",
            description=(
                "Read a CSV file inside `context/` and return the header, "
                "preview rows, and total data row count."
            ),
            input_schema={"path": "relative/path/to/file.csv", "max_rows": 20},
        ),
        "read_doc": ToolSpec(
            name="read_doc",
            description=(
                "Preview a text document inside `context/`. "
                "Large files are truncated to the requested maximum character count."
            ),
            input_schema={"path": "relative/path/to/file.md", "max_chars": 4000},
        ),
        "retrieve_context": ToolSpec(
            name="retrieve_context",
            description=(
                "Retrieve relevant chunks from context/knowledge.md and doc/*.md using the "
                "question or explicit keywords. Use this before solving hard/extreme tasks "
                "or when business definitions, value ranges, or terminology are needed."
            ),
            input_schema={
                "query": "question or search phrase",
                "keywords": ["optional", "terms"],
                "max_chunks": 5,
                "max_chars_per_chunk": 2000,
            },
        ),
        "read_json": ToolSpec(
            name="read_json",
            description=(
                "Pretty-print and preview a JSON file inside `context/`. "
                "Large content is truncated to the requested maximum character count."
            ),
            input_schema={"path": "relative/path/to/file.json", "max_chars": 4000},
        ),
        "validate_answer": ToolSpec(
            name="validate_answer",
            description=(
                "Validate the candidate final answer table before calling answer. It checks "
                "shape errors and warns about empty answers, likely extra columns, possible "
                "tie/multiple-row omissions, ambiguous candidates, and numeric formatting risks. "
                "Warnings are non-blocking when ready_for_answer is true."
            ),
            input_schema={
                "columns": ["column_name"],
                "rows": [["value_1"]],
                "notes": "brief evidence or tie/aggregation check",
            },
        ),
    }
    handlers = {
        "answer": _answer,
        "execute_context_sql": _execute_context_sql,
        "execute_data_query": _execute_data_query,
        "execute_python": _execute_python,
        "inspect_sqlite_schema": _inspect_sqlite_schema,
        "list_context": _list_context,
        "profile_context": _profile_context,
        "read_csv": _read_csv,
        "read_doc": _read_doc,
        "read_json": _read_json,
        "retrieve_context": _retrieve_context,
        "validate_answer": _validate_answer,
    }
    return ToolRegistry(specs=specs, handlers=handlers)
