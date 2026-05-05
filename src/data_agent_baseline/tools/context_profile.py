"""タスク context の構造把握と文書検索を行う補助ツール。"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from data_agent_baseline.benchmark.schema import PublicTask

JSON_FULL_LOAD_LIMIT_BYTES = 10 * 1024 * 1024
TEXT_PREVIEW_LIMIT_CHARS = 512
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+")


def _relative_path(task: PublicTask, path: Path) -> str:
    """context ルートからの相対パスを POSIX 形式で返す。"""

    return path.relative_to(task.context_dir).as_posix()


def _safe_read_text(path: Path, *, max_chars: int | None = None) -> str:
    """テキストファイルを不正文字を置換しながら読み込む。"""

    text = path.read_text(encoding="utf-8", errors="replace")
    if max_chars is None:
        return text
    return text[:max_chars]


def _count_csv_rows(path: Path) -> tuple[list[str], int, list[list[str]]]:
    """CSV の header、データ行数、先頭プレビューを返す。"""

    with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
        preview: list[list[str]] = []
        row_count = 0
        for row in reader:
            row_count += 1
            if len(preview) < 3:
                preview.append(row)
    return header, row_count, preview


def _profile_csv(path: Path) -> dict[str, Any]:
    """CSV ファイルの軽量 profile を作る。"""

    header, row_count, preview = _count_csv_rows(path)
    return {
        "type": "csv",
        "columns": header,
        "row_count": row_count,
        "preview_rows": preview,
    }


def _profile_sqlite(path: Path) -> dict[str, Any]:
    """SQLite の table schema と行数を取得する。"""

    tables: list[dict[str, Any]] = []
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        table_rows = conn.execute(
            """
            SELECT name, sql
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()
        for table_name, create_sql in table_rows:
            columns = [
                {"name": row[1], "type": row[2]}
                for row in conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
            ]
            row_count = conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
            tables.append(
                {
                    "name": table_name,
                    "columns": columns,
                    "row_count": row_count,
                    "create_sql": create_sql,
                }
            )
    return {
        "type": "sqlite",
        "tables": tables,
    }


def _approximate_json_keys(preview: str) -> list[str]:
    """巨大 JSON の preview から top-level らしい key を推定する。"""

    keys = []
    for match in re.finditer(r'"([^"\\]{1,80})"\s*:', preview):
        key = match.group(1)
        if key not in keys:
            keys.append(key)
        if len(keys) >= 20:
            break
    return keys


def _profile_json(path: Path) -> dict[str, Any]:
    """JSON の top-level shape を返す。巨大ファイルは preview ベースに留める。"""

    size = path.stat().st_size
    preview = _safe_read_text(path, max_chars=4096)
    base: dict[str, Any] = {
        "type": "json",
        "preview": preview[:TEXT_PREVIEW_LIMIT_CHARS],
    }
    if size > JSON_FULL_LOAD_LIMIT_BYTES:
        base.update(
            {
                "fully_loaded": False,
                "top_level_type": "unknown_large_json",
                "top_level_keys": _approximate_json_keys(preview),
            }
        )
        return base

    payload = json.loads(path.read_text(encoding="utf-8"))
    base["fully_loaded"] = True
    base["top_level_type"] = type(payload).__name__
    if isinstance(payload, dict):
        base["top_level_keys"] = list(payload.keys())[:30]
        if isinstance(payload.get("records"), list):
            records = payload["records"]
            base["record_count"] = len(records)
            if records and isinstance(records[0], dict):
                base["record_columns"] = list(records[0].keys())
        elif payload:
            first_value = next(iter(payload.values()))
            if isinstance(first_value, list):
                base["record_count"] = len(first_value)
    elif isinstance(payload, list):
        base["record_count"] = len(payload)
        if payload and isinstance(payload[0], dict):
            base["record_columns"] = list(payload[0].keys())
    return base


def _extract_headings(text: str, *, limit: int = 30) -> list[str]:
    """Markdown 風の見出しを抽出する。"""

    headings: list[str] = []
    for line in text.splitlines():
        match = HEADING_PATTERN.match(line)
        if match is None:
            continue
        headings.append(match.group(2).strip())
        if len(headings) >= limit:
            break
    return headings


def _profile_doc(path: Path) -> dict[str, Any]:
    """文書ファイルの見出しと preview を返す。"""

    text = _safe_read_text(path)
    return {
        "type": "document",
        "char_count": len(text),
        "headings": _extract_headings(text),
        "preview": text[:TEXT_PREVIEW_LIMIT_CHARS],
    }


def build_context_profile(task: PublicTask) -> dict[str, Any]:
    """タスク context を全走査して、データ種別ごとの profile を返す。"""

    files: list[dict[str, Any]] = []
    modality_counter: Counter[str] = Counter()
    for path in sorted(task.context_dir.rglob("*")):
        if not path.is_file():
            continue
        rel_path = _relative_path(task, path)
        if rel_path == "knowledge.md":
            continue
        suffix = path.suffix.lower()
        entry: dict[str, Any] = {
            "path": rel_path,
            "size_bytes": path.stat().st_size,
        }
        try:
            if suffix == ".csv":
                entry.update(_profile_csv(path))
                modality_counter["csv"] += 1
            elif suffix in {".db", ".sqlite", ".sqlite3"}:
                entry.update(_profile_sqlite(path))
                modality_counter["db"] += 1
            elif suffix == ".json":
                entry.update(_profile_json(path))
                modality_counter["json"] += 1
            elif suffix in {".md", ".txt"}:
                entry.update(_profile_doc(path))
                modality_counter["doc"] += 1
            else:
                entry["type"] = "file"
        except Exception as exc:  # noqa: BLE001
            entry["type"] = entry.get("type", "file")
            entry["profile_error"] = str(exc)
        files.append(entry)

    return {
        "task_id": task.task_id,
        "difficulty": task.difficulty,
        "question": task.question,
        "context_root": str(task.context_dir),
        "modalities": sorted(modality_counter),
        "modality_counts": dict(sorted(modality_counter.items())),
        "file_count": len(files),
        "total_size_bytes": sum(int(file_item["size_bytes"]) for file_item in files),
        "files": files,
        "strategy": choose_strategy(task.difficulty, modality_counter),
        "path_rules": [
            "Use only paths returned in this profile or by list_context.",
            "profile_context omits context/knowledge.md; call plan_knowledge before deciding whether definitions are needed.",
        ],
    }


def choose_strategy(difficulty: str, modality_counter: Counter[str]) -> str:
    """難易度と modality から推奨 solver 方針を返す。"""

    normalized = difficulty.lower()
    has_docs = bool(modality_counter.get("doc"))
    has_db = bool(modality_counter.get("db"))
    if normalized in {"hard", "extreme"} or (has_docs and normalized != "easy"):
        return "retrieve-first: collect relevant document chunks, then query structured data."
    if has_db:
        return "schema-first: inspect/query SQLite and join CSV/JSON with execute_data_query when needed."
    return "python-first: load CSV/JSON directly and compute the answer."


def _profile_sources_for_knowledge(profile: dict[str, Any], *, limit: int = 12) -> list[dict[str, Any]]:
    """knowledge 計画で参照した profile 情報を短く返す。"""

    sources: list[dict[str, Any]] = []
    raw_files = profile.get("files", [])
    if not isinstance(raw_files, list):
        return sources

    for raw_file in raw_files[:limit]:
        if not isinstance(raw_file, dict):
            continue
        source: dict[str, Any] = {
            "path": raw_file.get("path"),
            "type": raw_file.get("type", "file"),
        }
        for key in ("columns", "top_level_keys", "record_columns", "headings"):
            raw_values = raw_file.get(key)
            if isinstance(raw_values, list) and raw_values:
                source[key] = raw_values[:12]
        raw_tables = raw_file.get("tables")
        if isinstance(raw_tables, list) and raw_tables:
            source["tables"] = [
                {
                    "name": table.get("name"),
                    "columns": [
                        column.get("name")
                        for column in table.get("columns", [])
                        if isinstance(column, dict)
                    ][:12],
                }
                for table in raw_tables[:6]
                if isinstance(table, dict) and isinstance(table.get("columns", []), list)
            ]
        sources.append(source)
    return sources


def plan_knowledge_needs(task: PublicTask) -> dict[str, Any]:
    """質問と参照データ profile に添えて knowledge.md 全文を返す。"""

    profile = build_context_profile(task)
    knowledge_path = task.context_dir / "knowledge.md"
    if not knowledge_path.is_file():
        return {
            "task_id": task.task_id,
            "question": task.question,
            "knowledge_path": "knowledge.md",
            "knowledge_exists": False,
            "profile_summary": {
                "modalities": profile["modalities"],
                "file_count": profile["file_count"],
                "sources": _profile_sources_for_knowledge(profile),
            },
            "knowledge_content": "",
            "knowledge_char_count": 0,
            "recommended_next_steps": [
                "No context/knowledge.md was found. Continue with structured data and doc files from profile_context."
            ],
        }

    knowledge_content = _safe_read_text(knowledge_path)
    return {
        "task_id": task.task_id,
        "question": task.question,
        "knowledge_path": "knowledge.md",
        "knowledge_exists": True,
        "profile_summary": {
            "modalities": profile["modalities"],
            "file_count": profile["file_count"],
            "sources": _profile_sources_for_knowledge(profile),
        },
        "knowledge_content": knowledge_content,
        "knowledge_char_count": len(knowledge_content),
        "recommended_next_steps": [
            "Read the full knowledge_content and decide whether definitions, thresholds, code meanings, or filtering rules apply.",
            "Use the profile_summary sources to choose the next structured or document query.",
        ],
    }


def _tokens(text: str) -> set[str]:
    """検索用の単語集合を作る。"""

    stopwords = {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "have",
        "has",
        "how",
        "what",
        "which",
        "among",
        "where",
        "when",
        "who",
        "whose",
    }
    return {
        token.lower()
        for token in TOKEN_PATTERN.findall(text)
        if len(token) >= 3 and token.lower() not in stopwords
    }


def _iter_document_chunks(path: Path, rel_path: str, *, chunk_chars: int) -> list[dict[str, str]]:
    """文書を見出しまたは固定長で chunk 化する。"""

    text = _safe_read_text(path)
    chunks: list[dict[str, str]] = []
    current_heading = ""
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_lines
        body = "\n".join(current_lines).strip()
        if not body:
            current_lines = []
            return
        for start in range(0, len(body), chunk_chars):
            chunks.append(
                {
                    "path": rel_path,
                    "heading": current_heading,
                    "text": body[start : start + chunk_chars],
                }
            )
        current_lines = []

    for line in text.splitlines():
        match = HEADING_PATTERN.match(line)
        if match is not None:
            flush()
            current_heading = match.group(2).strip()
            current_lines.append(line)
        else:
            current_lines.append(line)
    flush()
    return chunks


def retrieve_context_chunks(
    task: PublicTask,
    *,
    query: str,
    keywords: list[str] | None = None,
    max_chunks: int = 5,
    max_chars_per_chunk: int = 2000,
) -> dict[str, Any]:
    """質問に関連する doc/knowledge chunk を単純な語彙一致で返す。"""

    query_tokens = _tokens(query)
    if keywords:
        query_tokens.update(_tokens(" ".join(keywords)))
    if not query_tokens:
        query_tokens = _tokens(task.question)

    scored_chunks: list[tuple[int, dict[str, str]]] = []
    searched_paths: list[str] = []
    for path in sorted(task.context_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".md", ".txt"}:
            continue
        rel_path = _relative_path(task, path)
        searched_paths.append(rel_path)
        for chunk in _iter_document_chunks(path, rel_path, chunk_chars=max_chars_per_chunk):
            chunk_tokens = _tokens(f"{chunk['heading']}\n{chunk['text']}")
            score = len(query_tokens & chunk_tokens)
            if score:
                scored_chunks.append((score, chunk))

    scored_chunks.sort(key=lambda item: (-item[0], item[1]["path"], item[1]["heading"]))
    selected = []
    for score, chunk in scored_chunks[:max_chunks]:
        selected.append(
            {
                "path": chunk["path"],
                "heading": chunk["heading"],
                "score": score,
                "text": chunk["text"][:max_chars_per_chunk],
            }
        )

    return {
        "query": query,
        "keywords": sorted(query_tokens),
        "searched_paths": searched_paths,
        "chunks": selected,
        "chunk_count": len(selected),
    }
