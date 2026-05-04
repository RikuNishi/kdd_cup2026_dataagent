from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_INPUT_ROOT = Path("data/public/input")
DEFAULT_OUTPUT_PATH = Path("artifacts/public_input_context_summary.json")
JSON_FULL_LOAD_LIMIT_BYTES = 10 * 1024 * 1024
TEXT_PREVIEW_LIMIT_CHARS = 300
DIFFICULTY_SORT_ORDER = {
    "easy": 0,
    "medium": 1,
    "hard": 2,
    "extreme": 3,
}


def task_sort_key(path: Path) -> tuple[int, str]:
    if path.name.startswith("task_"):
        suffix = path.name.removeprefix("task_")
        if suffix.isdigit():
            return int(suffix), path.name
    return 10**12, path.name


def difficulty_sort_key(difficulty: str) -> tuple[int, str]:
    """難易度を既知の順序で並べ、未知の値は末尾で名前順にする。"""
    normalized = difficulty.lower()
    return DIFFICULTY_SORT_ORDER.get(normalized, 10**12), normalized


def read_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def infer_data_format(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return "csv"
    if suffix == ".json":
        return "json"
    if suffix in {".db", ".sqlite", ".sqlite3"}:
        return "sqlite"
    if suffix in {".md", ".markdown"}:
        return "markdown"
    if suffix == ".txt":
        return "text"
    return "unknown"


def infer_context_folder(path: Path, context_dir: Path) -> str:
    """context からの第1階層フォルダ名でファイル種別を分類する。"""
    rel_path = path.relative_to(context_dir)
    if len(rel_path.parts) <= 1:
        return "root"
    return rel_path.parts[0]


def should_include_context_file(path: Path, context_dir: Path) -> bool:
    """集計対象にする context ファイルかどうかを判定する。"""
    rel_path = path.relative_to(context_dir).as_posix()
    return rel_path != "knowledge.md"


def profile_csv(path: Path, sample_limit: int) -> dict[str, Any]:
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
        sample_rows: list[list[str]] = []
        row_count = 0
        for row in reader:
            row_count += 1
            if len(sample_rows) < sample_limit:
                sample_rows.append(row)

    return {
        "column_count": len(header),
        "columns": header,
        "row_count": row_count,
        "sample_rows": sample_rows,
    }


def summarize_json_payload(payload: Any) -> dict[str, Any]:
    summary: dict[str, Any] = {
        # "top_level_type": type(payload).__name__,
    }

    if isinstance(payload, dict):
        summary["top_level_keys"] = list(payload.keys())
        first_value = next(iter(payload.values()), None)
        if isinstance(first_value, list):
            summary["first_collection_count"] = len(first_value)
            if first_value and isinstance(first_value[0], dict):
                summary["first_record_keys"] = list(first_value[0].keys())
        return summary

    if isinstance(payload, list):
        summary["record_count"] = len(payload)
        if payload and isinstance(payload[0], dict):
            summary["record_keys"] = list(payload[0].keys())
        return summary

    summary["value_preview"] = str(payload)[:TEXT_PREVIEW_LIMIT_CHARS]
    return summary


def profile_json(path: Path) -> dict[str, Any]:
    size_bytes = path.stat().st_size
    if size_bytes > JSON_FULL_LOAD_LIMIT_BYTES:
        return {
            "fully_loaded": False,
            "reason": f"larger than {JSON_FULL_LOAD_LIMIT_BYTES} bytes",
            "preview": path.read_text(encoding="utf-8", errors="replace")[:TEXT_PREVIEW_LIMIT_CHARS],
        }

    payload = read_json_file(path)
    return {
        "fully_loaded": True,
        **summarize_json_payload(payload),
    }


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def profile_sqlite(path: Path) -> dict[str, Any]:
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
            quoted_name = quote_identifier(str(table_name))
            columns = [
                {
                    "name": row[1],
                    "type": row[2],
                    "not_null": bool(row[3]),
                    "default": row[4],
                    "primary_key": bool(row[5]),
                }
                for row in conn.execute(f"PRAGMA table_info({quoted_name})").fetchall()
            ]
            row_count = conn.execute(f"SELECT COUNT(*) FROM {quoted_name}").fetchone()[0]
            tables.append(
                {
                    "name": table_name,
                    "column_count": len(columns),
                    "columns": columns,
                    "row_count": row_count,
                    "create_sql": create_sql,
                }
            )

    return {
        "table_count": len(tables),
        "tables": tables,
    }


def profile_text(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    headings = [
        line.lstrip("#").strip()
        for line in text.splitlines()
        if line.startswith("#") and line.lstrip("#").strip()
    ]
    return {
        # "char_count": len(text),
        "line_count": len(text.splitlines()),
        # "headings": headings[:30],
        "preview": text[:TEXT_PREVIEW_LIMIT_CHARS],
    }


def profile_context_file(path: Path, context_dir: Path, sample_limit: int) -> dict[str, Any]:
    rel_path = path.relative_to(context_dir).as_posix()
    data_format = infer_data_format(path)
    context_folder = infer_context_folder(path, context_dir)
    profile: dict[str, Any] = {
        "path": rel_path,
        "context_folder": context_folder,
        "data_format": data_format,
        # "extension": path.suffix.lower(),
        "size_bytes": path.stat().st_size,
    }

    try:
        if data_format == "csv":
            profile.update(profile_csv(path, sample_limit))
        elif data_format == "json":
            profile.update(profile_json(path))
        elif data_format == "sqlite":
            profile.update(profile_sqlite(path))
        elif data_format in {"markdown", "text"}:
            profile.update(profile_text(path))
    except Exception as exc:  # noqa: BLE001
        profile["profile_error"] = f"{type(exc).__name__}: {exc}"

    return profile


def summarize_task(task_dir: Path, sample_limit: int) -> dict[str, Any]:
    task_json_path = task_dir / "task.json"
    context_dir = task_dir / "context"
    task_payload = read_json_file(task_json_path)

    context_files = [
        profile_context_file(path, context_dir, sample_limit)
        for path in sorted(context_dir.rglob("*"))
        if path.is_file() and should_include_context_file(path, context_dir)
    ]
    format_counts = Counter(str(file_info["context_folder"]) for file_info in context_files)

    return {
        "task_id": task_payload.get("task_id", task_dir.name),
        # "task_dir": task_dir.as_posix(),
        "task_json": {
            # "path": task_json_path.relative_to(task_dir).as_posix(),
            # "keys": list(task_payload.keys()),
            "difficulty": task_payload.get("difficulty"),
            "question": task_payload.get("question"),
        },
        "context": {
            # "path": context_dir.relative_to(task_dir).as_posix(),
            # "file_count": len(context_files),
            # "data_formats": sorted(format_counts),
            "format_counts": dict(sorted(format_counts.items())),
            "files": context_files,
        },
    }


def build_summary(input_root: Path, sample_limit: int) -> dict[str, Any]:
    task_dirs = [
        path
        for path in input_root.iterdir()
        if path.is_dir() and path.name.startswith("task_") and (path / "task.json").is_file()
    ]
    task_dirs.sort(key=task_sort_key)

    tasks = [summarize_task(task_dir, sample_limit) for task_dir in task_dirs]
    all_format_counts: Counter[str] = Counter()
    difficulty_counts: Counter[str] = Counter()
    difficulty_format_counts: dict[str, Counter[str]] = {}
    for task in tasks:
        format_counts = task["context"]["format_counts"]
        difficulty = str(task["task_json"].get("difficulty") or "unknown")
        all_format_counts.update(format_counts)
        difficulty_counts[difficulty] += 1
        difficulty_format_counts.setdefault(difficulty, Counter()).update(format_counts)

    difficulty_summary = {
        difficulty: {
            "task_count": difficulty_counts[difficulty],
            "format_counts": dict(sorted(difficulty_format_counts[difficulty].items())),
        }
        for difficulty in sorted(difficulty_counts, key=difficulty_sort_key)
    }

    return {
        # "generated_at": datetime.now(timezone.utc).isoformat(),
        # "input_root": input_root.resolve().as_posix(),
        "task_count": len(tasks),
        "difficulty_counts": {
            difficulty: difficulty_counts[difficulty]
            for difficulty in sorted(difficulty_counts, key=difficulty_sort_key)
        },
        # "data_formats": sorted(all_format_counts),
        "format_counts": dict(sorted(all_format_counts.items())),
        "difficulty_summary": difficulty_summary,
        "tasks": tasks,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize task.json and context data formats under data/public/input.",
    )
    parser.add_argument(
        "--input-root",
        type=Path,
        default=DEFAULT_INPUT_ROOT,
        help=f"Directory containing task_* folders. Default: {DEFAULT_INPUT_ROOT}",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"Output JSON path. Default: {DEFAULT_OUTPUT_PATH}",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=1,
        help="Number of sample CSV rows to include per file. Default: 3",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_root = args.input_root.resolve()
    if not input_root.is_dir():
        raise SystemExit(f"Input root does not exist: {input_root}")

    summary = build_summary(input_root, sample_limit=max(args.sample_limit, 0))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {summary['task_count']} task summaries to {args.output}")


if __name__ == "__main__":
    main()
