"""タスク context の構造把握と文書検索を行う補助ツール。"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from data_agent_baseline.benchmark.schema import PublicTask

JSON_FULL_LOAD_LIMIT_BYTES = 10 * 1024 * 1024
TEXT_PREVIEW_LIMIT_CHARS = 512
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+")
CAMEL_BOUNDARY_PATTERN = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
QUESTION_COLUMN_SYNONYMS = {
    "cost": {"cost", "amount", "spent", "remaining"},
    "type": {"type", "category"},
    "number": {"number", "position", "rank", "round"},
    "race": {"race", "raceid", "race_id", "name"},
    "patient": {"patient", "id", "sex", "diagnosis"},
}
AMBIGUITY_TERMS = {"cost", "type", "number", "race", "patient"}
RANKING_TOKENS = {"lowest", "highest", "minimum", "maximum", "least", "most"}
AGGREGATION_TOKENS = {"total", "sum", "average", "avg", "overall", "expenditure"}
KEY_TOKEN_HINTS = {"id", "key", "ref"}


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
        if isinstance(payload.get("table"), str):
            base["table_name"] = payload["table"]
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


def _knowledge_profile(task: PublicTask) -> dict[str, Any] | None:
    """top-level knowledge.md の全文 profile を返す。"""

    path = task.context_dir / "knowledge.md"
    if not path.is_file():
        return None
    text = _safe_read_text(path)
    return {
        "path": "knowledge.md",
        "line_count": len(text.splitlines()),
        "char_count": len(text),
        "text": text,
    }


def _identifier_tokens(value: str) -> set[str]:
    """列名や table 名を比較するための token 集合を返す。"""

    expanded = CAMEL_BOUNDARY_PATTERN.sub("_", value)
    raw_tokens = TOKEN_PATTERN.findall(expanded)
    tokens: set[str] = set()
    for token in raw_tokens:
        lowered = token.lower()
        if not lowered:
            continue
        tokens.add(lowered)
        for part in re.split(r"[_\W]+", lowered):
            if part:
                tokens.add(part)
    normalized = re.sub(r"[^a-z0-9]", "", expanded.lower())
    if normalized:
        tokens.add(normalized)
    return tokens


def _source_name(entry: dict[str, Any], *, table_name: str | None = None) -> str:
    """profile entry から model に見せる source 名を作る。"""

    if table_name:
        return table_name
    if isinstance(entry.get("table_name"), str):
        return str(entry["table_name"])
    return Path(str(entry["path"])).stem


def _collect_structured_columns(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """profile 済みファイルから横断的な列 metadata を集める。"""

    columns: list[dict[str, Any]] = []
    for entry in files:
        file_type = entry.get("type")
        path = str(entry.get("path"))
        if file_type == "csv":
            for column in entry.get("columns", []):
                source = _source_name(entry)
                columns.append(
                    {
                        "source": source,
                        "path": path,
                        "column": str(column),
                        "qualified_name": f"{source}.{column}",
                        "type": "csv",
                        "tokens": sorted(_identifier_tokens(str(column))),
                    }
                )
        elif file_type == "json":
            source = _source_name(entry)
            for column in entry.get("record_columns", []):
                columns.append(
                    {
                        "source": source,
                        "path": path,
                        "column": str(column),
                        "qualified_name": f"{source}.{column}",
                        "type": "json",
                        "tokens": sorted(_identifier_tokens(str(column))),
                    }
                )
        elif file_type == "sqlite":
            for table in entry.get("tables", []):
                if not isinstance(table, dict):
                    continue
                table_name = str(table.get("name") or _source_name(entry))
                for column_info in table.get("columns", []):
                    if not isinstance(column_info, dict):
                        continue
                    column = str(column_info.get("name"))
                    columns.append(
                        {
                            "source": table_name,
                            "path": path,
                            "table": table_name,
                            "column": column,
                            "qualified_name": f"{table_name}.{column}",
                            "type": "sqlite",
                            "tokens": sorted(_identifier_tokens(column)),
                        }
                    )
    return columns


def _question_column_candidates(task: PublicTask, columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """質問語と列名 token の一致から候補列を返す。"""

    question_tokens = _tokens(task.question)
    expanded_tokens = set(question_tokens)
    for token in question_tokens:
        expanded_tokens.update(QUESTION_COLUMN_SYNONYMS.get(token, set()))

    candidates: list[dict[str, Any]] = []
    for term in sorted(question_tokens):
        term_targets = {term} | QUESTION_COLUMN_SYNONYMS.get(term, set())
        matches = [
            column["qualified_name"]
            for column in columns
            if term_targets & set(column["tokens"])
        ]
        if matches:
            candidates.append(
                {
                    "term": term,
                    "columns": sorted(dict.fromkeys(matches)),
                }
            )

    synonym_matches = [
        column["qualified_name"]
        for column in columns
        if expanded_tokens & set(column["tokens"])
    ]
    if synonym_matches:
        candidates.append(
            {
                "term": "__all_question_terms__",
                "columns": sorted(dict.fromkeys(synonym_matches)),
            }
        )
    return candidates


def _ambiguity_requirement(term: str, question_tokens: set[str], columns: list[str]) -> str:
    """曖昧語ごとの比較要求を英語で返す。"""

    if term == "cost":
        if question_tokens & RANKING_TOKENS and not question_tokens & AGGREGATION_TOKENS:
            return (
                "Compare row-level MIN/MAX cost against event-level SUM/AVG and monetary "
                "budget fields; do not aggregate unless the question asks for total, average, "
                "overall, or expenditure."
            )
        return "Compare monetary candidates such as cost, spent, amount, and remaining before choosing."
    if term == "number":
        return (
            "Compare every number-like candidate, especially position/rank/round/number, "
            "before filtering. If the phrase describes a person's state in an event "
            "(for example 'he was in ... number'), test participant-level position/rank "
            "before event-level round."
        )
    if term == "type":
        return "Compare type/category/description candidates before grouping or filtering."
    if term == "race":
        return "Compare race identifiers and race display names before selecting output columns."
    if term == "patient":
        return "Compare patient-level and examination-level columns before selecting patient attributes."
    return f"Compare candidates for {term}: {', '.join(columns[:6])}."


def _ambiguity_checklist(task: PublicTask, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """LLM に削らせない曖昧語 checklist を機械的に作る。"""

    question_tokens = _tokens(task.question)
    checklist: list[dict[str, Any]] = []
    for item in candidates:
        term = str(item.get("term"))
        if term == "__all_question_terms__":
            continue
        columns = [str(column) for column in item.get("columns", []) if str(column)]
        is_high_risk = term in AMBIGUITY_TERMS or len(columns) > 1
        if not is_high_risk:
            continue
        checklist.append(
            {
                "term": term,
                "candidate_columns": columns,
                "required_action": _ambiguity_requirement(term, question_tokens, columns),
                "notes_requirement": (
                    "validate_answer notes must state the selected candidate and the rejected "
                    "alternatives when this ambiguity affects the answer."
                ),
                "blocking_if_unresolved": True,
            }
        )
    return checklist


def _ambiguous_columns(columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同じ列名が複数 source に現れる箇所を返す。"""

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for column in columns:
        grouped[str(column["column"]).lower()].append(column)

    ambiguous = []
    for normalized_name, items in sorted(grouped.items()):
        sources = {str(item["source"]) for item in items}
        if len(sources) <= 1:
            continue
        ambiguous.append(
            {
                "name": normalized_name,
                "columns": [item["qualified_name"] for item in items],
            }
        )
    return ambiguous


def _column_key_forms(column_name: str) -> set[str]:
    """join 候補検出用に列名から key form を作る。"""

    lowered = column_name.lower()
    compact = re.sub(r"[^a-z0-9]", "", lowered)
    forms = {compact}
    if lowered.startswith("link_to_"):
        target = lowered.removeprefix("link_to_")
        forms.update({target, f"{target}id", f"{target}_id"})
    if compact.endswith("id") and len(compact) > 2:
        forms.add(compact[:-2])
    return {form for form in forms if form}


def _is_key_like(column_name: str) -> bool:
    """列名が join/cardinality を見る価値のある key 系かどうかを返す。"""

    tokens = _identifier_tokens(column_name)
    lowered = column_name.lower()
    return lowered.startswith("link_to_") or bool(tokens & KEY_TOKEN_HINTS) or lowered.endswith("id")


def _relationship_candidates(columns: list[dict[str, Any]]) -> list[dict[str, str]]:
    """列名ルールから source 間の join 候補を返す。"""

    results: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for left in columns:
        if not _is_key_like(str(left["column"])):
            continue
        left_forms = _column_key_forms(str(left["column"]))
        for right in columns:
            if left is right or left["source"] == right["source"]:
                continue
            if not _is_key_like(str(right["column"])):
                continue
            if not (left_forms & _column_key_forms(str(right["column"]))):
                continue
            key = (
                str(left["source"]),
                str(left["column"]),
                str(right["source"]),
                str(right["column"]),
            )
            reverse_key = (key[2], key[3], key[0], key[1])
            if key in seen or reverse_key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "source": key[0],
                    "column": key[1],
                    "target_source": key[2],
                    "target_column": key[3],
                }
            )
    return results


def _csv_column_counts(path: Path, columns: list[str]) -> dict[str, tuple[int, int]]:
    """CSV の複数列について総行数と distinct 数を 1 pass で返す。"""

    values = {column: set() for column in columns}
    row_count = 0
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            row_count += 1
            for column in columns:
                values[column].add(str(row.get(column, "")))
    return {column: (row_count, len(distinct_values)) for column, distinct_values in values.items()}


def _json_column_counts(path: Path, columns: list[str]) -> dict[str, tuple[int, int]]:
    """JSON records の複数列について総行数と distinct 数を返す。"""

    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("records") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        return {column: (0, 0) for column in columns}
    values = {column: set() for column in columns}
    row_count = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        row_count += 1
        for column in columns:
            values[column].add(str(record.get(column, "")))
    return {column: (row_count, len(distinct_values)) for column, distinct_values in values.items()}


def _sqlite_column_counts(path: Path, table: str, column: str) -> tuple[int, int]:
    """SQLite の指定列について総行数と distinct 数を返す。"""

    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        quoted_table = table.replace('"', '""')
        quoted_column = column.replace('"', '""')
        row_count, distinct_count = conn.execute(
            f'SELECT COUNT(*), COUNT(DISTINCT "{quoted_column}") FROM "{quoted_table}"'
        ).fetchone()
    return int(row_count), int(distinct_count)


def _cardinality_hints(task: PublicTask, columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """key/link 系列の重複度を計算して join リスクを返す。"""

    hints: list[dict[str, Any]] = []
    key_columns = [
        column
        for column in columns
        if _is_key_like(str(column["column"]))
    ]
    grouped_columns: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for column in key_columns:
        grouped_columns[(str(column["type"]), str(column["path"]))].append(column)

    def append_hint(column: dict[str, Any], row_count: int, distinct_count: int) -> None:
        duplicate_count = max(row_count - distinct_count, 0)
        hint: dict[str, Any] = {
            "source": column["source"],
            "column": column["column"],
            "qualified_name": column["qualified_name"],
            "row_count": row_count,
            "distinct_count": distinct_count,
            "duplicate_count": duplicate_count,
        }
        if duplicate_count:
            hint["risk"] = "one_to_many_or_many_to_one"
        hints.append(hint)

    def append_error(column: dict[str, Any], exc: Exception) -> None:
        hints.append(
            {
                "source": column["source"],
                "column": column["column"],
                "qualified_name": column["qualified_name"],
                "profile_error": str(exc),
            }
        )

    for (column_type, rel_path), items in grouped_columns.items():
        path = task.context_dir / rel_path
        if column_type in {"csv", "json"}:
            column_names = [str(column["column"]) for column in items]
            try:
                counts = (
                    _csv_column_counts(path, column_names)
                    if column_type == "csv"
                    else _json_column_counts(path, column_names)
                )
                for column in items:
                    row_count, distinct_count = counts[str(column["column"])]
                    append_hint(column, row_count, distinct_count)
            except Exception as exc:  # noqa: BLE001
                for column in items:
                    append_error(column, exc)
            continue

        for column in items:
            column_name = str(column["column"])
            try:
                table = str(column.get("table") or column["source"])
                row_count, distinct_count = _sqlite_column_counts(path, table, column_name)
                append_hint(column, row_count, distinct_count)
            except Exception as exc:  # noqa: BLE001
                append_error(column, exc)
    return hints


def build_context_profile(task: PublicTask) -> dict[str, Any]:
    """タスク context を全走査して、データ種別ごとの profile を返す。"""

    files: list[dict[str, Any]] = []
    modality_counter: Counter[str] = Counter()
    for path in sorted(task.context_dir.rglob("*")):
        if not path.is_file():
            continue
        rel_path = _relative_path(task, path)
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
                if rel_path == "knowledge.md":
                    modality_counter["knowledge"] += 1
                    entry["is_knowledge"] = True
                else:
                    modality_counter["doc"] += 1
                    entry["is_knowledge"] = False
            else:
                entry["type"] = "file"
        except Exception as exc:  # noqa: BLE001
            entry["type"] = entry.get("type", "file")
            entry["profile_error"] = str(exc)
        files.append(entry)

    structured_columns = _collect_structured_columns(files)
    question_column_candidates = _question_column_candidates(task, structured_columns)
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
        "knowledge": _knowledge_profile(task),
        "structured_columns": [
            {
                key: value
                for key, value in column.items()
                if key != "tokens"
            }
            for column in structured_columns
        ],
        "question_column_candidates": question_column_candidates,
        "ambiguity_checklist": _ambiguity_checklist(task, question_column_candidates),
        "ambiguous_columns": _ambiguous_columns(structured_columns),
        "relationship_candidates": _relationship_candidates(structured_columns),
        "cardinality_hints": _cardinality_hints(task, structured_columns),
        "strategy": choose_strategy(task.difficulty, modality_counter),
        "path_rules": [
            "Use only paths returned in this profile or by list_context.",
            "knowledge.md is normally at context/knowledge.md, not doc/knowledge.md.",
        ],
    }


def choose_strategy(difficulty: str, modality_counter: Counter[str]) -> str:
    """難易度と modality から推奨 solver 方針を返す。"""

    normalized = difficulty.lower()
    has_docs = bool(modality_counter.get("doc") or modality_counter.get("knowledge"))
    has_db = bool(modality_counter.get("db"))
    if normalized in {"hard", "extreme"} or (has_docs and normalized != "easy"):
        return "retrieve-first: collect relevant doc/knowledge chunks, then query structured data."
    if has_db:
        return "schema-first: inspect/query SQLite and join CSV/JSON with execute_data_query when needed."
    return "python-first: load CSV/JSON directly and compute the answer."


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
