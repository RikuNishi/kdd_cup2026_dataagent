from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Protocol


DEFAULT_INPUT_ROOT = Path("data/public/input")
DEFAULT_OUTPUT_PATH = Path("artifacts/public_input_knowledge_summary.json")
TEXT_PREVIEW_LIMIT_CHARS = 500
DEFAULT_TOKENIZER_MODEL = "qwen3.5-35b-a3b"
DEFAULT_TOKENIZER_ENCODING = "cl100k_base"


class Tokenizer(Protocol):
    name: str

    def encode(self, text: str) -> list[int]:
        """テキストを token ID 列に変換する。"""
        ...


def task_sort_key(path: Path) -> tuple[int, str]:
    """task_<数字> を数字順に並べる。"""
    if path.name.startswith("task_"):
        suffix = path.name.removeprefix("task_")
        if suffix.isdigit():
            return int(suffix), path.name
    return 10**12, path.name


def read_json_file(path: Path) -> dict[str, Any]:
    """JSON ファイルを dict として読む。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected object JSON: {path}")
    return payload


def extract_markdown_headings(text: str) -> list[dict[str, Any]]:
    """Markdown 見出しを level と text の一覧にする。"""
    headings: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        if not stripped.startswith("#"):
            continue
        hashes = len(stripped) - len(stripped.lstrip("#"))
        if hashes == 0 or hashes > 6:
            continue
        title = stripped[hashes:].strip()
        if title:
            headings.append({"line": line_no, "level": hashes, "text": title})
    return headings


def load_tiktoken_encoding(tokenizer_model: str, tokenizer_encoding: str) -> Tokenizer:
    """tiktoken encoding を読み込む。Qwen 未対応時は指定 encoding に fallback する。"""
    try:
        import tiktoken
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "tiktoken is required. Install dependencies first, or run: "
            "python3 -m pip install tiktoken"
        ) from exc

    try:
        return tiktoken.encoding_for_model(tokenizer_model)
    except KeyError:
        return tiktoken.get_encoding(tokenizer_encoding)


def summarize_knowledge_md(
    task_dir: Path,
    include_content: bool,
    tokenizer: Tokenizer,
) -> dict[str, Any]:
    """1 task の knowledge.md 情報をまとめる。"""
    task_json_path = task_dir / "task.json"
    knowledge_path = task_dir / "context" / "knowledge.md"
    task_payload = read_json_file(task_json_path)

    record: dict[str, Any] = {
        "task_id": task_payload.get("task_id", task_dir.name),
        "difficulty": task_payload.get("difficulty"),
        "question": task_payload.get("question"),
        "path": knowledge_path.relative_to(task_dir).as_posix(),
        "exists": knowledge_path.is_file(),
    }
    if not knowledge_path.is_file():
        return record

    content = knowledge_path.read_text(encoding="utf-8", errors="replace")
    encoded = content.encode("utf-8")
    record.update(
        {
            "size_bytes": knowledge_path.stat().st_size,
            "char_count": len(content),
            "line_count": len(content.splitlines()),
            "token_count": len(tokenizer.encode(content)),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "headings": extract_markdown_headings(content),
            "preview": content[:TEXT_PREVIEW_LIMIT_CHARS],
        }
    )
    if include_content:
        record["content"] = content
    return record


def build_summary(
    input_root: Path,
    include_content: bool,
    tokenizer_model: str,
    tokenizer_encoding: str,
) -> dict[str, Any]:
    """公開 input 配下の全 task から knowledge.md 情報を収集する。"""
    tokenizer = load_tiktoken_encoding(tokenizer_model, tokenizer_encoding)
    task_dirs = [
        path
        for path in input_root.iterdir()
        if path.is_dir() and path.name.startswith("task_") and (path / "task.json").is_file()
    ]
    task_dirs.sort(key=task_sort_key)
    records = [summarize_knowledge_md(task_dir, include_content, tokenizer) for task_dir in task_dirs]

    missing = [record["task_id"] for record in records if not record["exists"]]
    token_counts = [
        int(record["token_count"])
        for record in records
        if record["exists"] and "token_count" in record
    ]
    return {
        "task_count": len(records),
        "knowledge_md_count": len(records) - len(missing),
        "missing_task_ids": missing,
        "tokenizer_model": tokenizer_model,
        "tokenizer_encoding": tokenizer.name,
        "token_count_summary": {
            "total": sum(token_counts),
            "min": min(token_counts) if token_counts else 0,
            "max": max(token_counts) if token_counts else 0,
            "average": round(sum(token_counts) / len(token_counts), 2) if token_counts else 0,
        },
        "records": records,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export knowledge.md metadata and contents from public input tasks.",
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
        "--metadata-only",
        action="store_true",
        help="Do not include full knowledge.md content in the output JSON.",
    )
    parser.add_argument(
        "--tokenizer-model",
        default=DEFAULT_TOKENIZER_MODEL,
        help=f"Model name passed to tiktoken.encoding_for_model. Default: {DEFAULT_TOKENIZER_MODEL}",
    )
    parser.add_argument(
        "--tokenizer-encoding",
        default=DEFAULT_TOKENIZER_ENCODING,
        help=(
            "Fallback tiktoken encoding when the model name is unknown. "
            f"Default: {DEFAULT_TOKENIZER_ENCODING}"
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_root = args.input_root.resolve()
    if not input_root.is_dir():
        raise SystemExit(f"Input root does not exist: {input_root}")

    summary = build_summary(
        input_root,
        include_content=not args.metadata_only,
        tokenizer_model=args.tokenizer_model,
        tokenizer_encoding=args.tokenizer_encoding,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"Wrote {summary['knowledge_md_count']} knowledge.md records "
        f"for {summary['task_count']} tasks to {args.output} "
        f"using {summary['tokenizer_encoding']}"
    )


if __name__ == "__main__":
    main()
