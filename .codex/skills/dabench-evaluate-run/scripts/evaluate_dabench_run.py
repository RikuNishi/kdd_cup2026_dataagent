#!/usr/bin/env python3
"""DataAgent-Bench の prediction.csv を公開 gold.csv に対して評価する。"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

NULL_TOKENS = {"", "null", "none", "nan", "nat", "<na>"}


@dataclass(frozen=True)
class TaskScore:
    """1 タスク分のスコアを保持する。"""

    task_id: str
    score: float
    recall: float
    matched_columns: int
    gold_columns: int
    predicted_columns: int
    extra_columns: int
    status: str


def read_csv(path: Path) -> list[list[str]]:
    """CSV を文字列セルの配列として読み込む。"""

    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.reader(handle))


def normalize_cell(value: object) -> str:
    """評価用にセル値を正規化する。"""

    text = "" if value is None else str(value)
    text = text.strip().replace("\r\n", "\n").replace("\r", "\n")
    if text.lower() in NULL_TOKENS:
        return ""
    try:
        decimal_value = Decimal(text)
    except InvalidOperation:
        return text
    if not decimal_value.is_finite():
        return ""
    rounded = decimal_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{rounded:.2f}"


def column_signatures(rows: list[list[str]]) -> Counter[tuple[str, ...]]:
    """ヘッダーを除いた列値から column signature を作る。"""

    if not rows:
        return Counter()
    column_count = len(rows[0])
    columns: list[list[str]] = [[] for _ in range(column_count)]
    for row in rows[1:]:
        for index in range(column_count):
            columns[index].append(normalize_cell(row[index] if index < len(row) else ""))
    return Counter(tuple(sorted(column)) for column in columns)


def score_task(prediction_path: Path, gold_path: Path, penalty_lambda: float) -> TaskScore:
    """1 タスクの prediction.csv と gold.csv を比較する。"""

    task_id = gold_path.parent.name
    gold_signatures = column_signatures(read_csv(gold_path))
    gold_columns = sum(gold_signatures.values())

    if not prediction_path.exists():
        return TaskScore(task_id, 0.0, 0.0, 0, gold_columns, 0, 0, "missing")

    prediction_signatures = column_signatures(read_csv(prediction_path))
    predicted_columns = sum(prediction_signatures.values())
    matched_columns = sum(
        min(count, gold_signatures.get(signature, 0))
        for signature, count in prediction_signatures.items()
    )
    extra_columns = max(predicted_columns - matched_columns, 0)
    recall = matched_columns / gold_columns if gold_columns else float(predicted_columns == 0)
    redundancy = extra_columns / predicted_columns if predicted_columns else 0.0
    score = max(recall - penalty_lambda * redundancy, 0.0)
    return TaskScore(
        task_id=task_id,
        score=round(score, 6),
        recall=round(recall, 6),
        matched_columns=matched_columns,
        gold_columns=gold_columns,
        predicted_columns=predicted_columns,
        extra_columns=extra_columns,
        status="ok",
    )


def evaluate(prediction_dir: Path, gold_dir: Path, penalty_lambda: float) -> dict[str, Any]:
    """prediction ディレクトリ全体を評価する。"""

    if penalty_lambda < 0:
        raise ValueError("penalty_lambda must be non-negative.")
    if not prediction_dir.is_dir():
        raise FileNotFoundError(f"Missing prediction directory: {prediction_dir}")
    if not gold_dir.is_dir():
        raise FileNotFoundError(f"Missing gold directory: {gold_dir}")

    gold_paths = sorted(gold_dir.glob("task_*/gold.csv"), key=lambda path: path.parent.name)
    tasks = [
        score_task(prediction_dir / gold_path.parent.name / "prediction.csv", gold_path, penalty_lambda)
        for gold_path in gold_paths
    ]
    task_count = len(tasks)
    scored_task_count = sum(task.status != "missing" for task in tasks)
    average_score = sum(task.score for task in tasks) / task_count if task_count else 0.0
    average_recall = sum(task.recall for task in tasks) / task_count if task_count else 0.0
    return {
        "task_count": task_count,
        "scored_task_count": scored_task_count,
        "missing_prediction_count": task_count - scored_task_count,
        "average_score": round(average_score, 6),
        "average_recall": round(average_recall, 6),
        "penalty_lambda": penalty_lambda,
        "tasks": [asdict(task) for task in tasks],
    }


def main() -> None:
    """CLI entrypoint。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--prediction-dir", required=True, type=Path)
    parser.add_argument("--gold-dir", default=Path("data/public/output"), type=Path)
    parser.add_argument("--penalty-lambda", default=0.1, type=float)
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args()

    result = evaluate(args.prediction_dir, args.gold_dir, args.penalty_lambda)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.output_json is not None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
