"""公開デモ出力に対するローカル評価ロジック。"""

from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

NULL_TOKENS = {"", "null", "none", "nan", "nat", "<na>"}
TASK_DIR_PREFIX = "task_"


@dataclass(frozen=True, slots=True)
class TaskEvaluationResult:
    """1 タスク分の評価結果。"""

    task_id: str
    score: float
    recall: float
    matched_columns: int
    gold_columns: int
    predicted_columns: int
    extra_columns: int
    prediction_path: str | None
    gold_path: str
    missing_prediction: bool

    def to_dict(self) -> dict[str, Any]:
        """JSON 出力用の辞書に変換する。"""

        return asdict(self)


@dataclass(frozen=True, slots=True)
class RunEvaluationResult:
    """run 全体の評価結果。"""

    task_count: int
    scored_task_count: int
    missing_prediction_count: int
    average_score: float
    average_recall: float
    penalty_lambda: float
    tasks: list[TaskEvaluationResult]

    def to_dict(self) -> dict[str, Any]:
        """JSON 出力用の辞書に変換する。"""

        return {
            "task_count": self.task_count,
            "scored_task_count": self.scored_task_count,
            "missing_prediction_count": self.missing_prediction_count,
            "average_score": self.average_score,
            "average_recall": self.average_recall,
            "penalty_lambda": self.penalty_lambda,
            "tasks": [task.to_dict() for task in self.tasks],
        }


def _read_csv_table(path: Path) -> list[list[str]]:
    """CSV を文字列セルの 2 次元配列として読み込む。"""

    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [[cell for cell in row] for row in csv.reader(handle)]


def _normalize_numeric(value: str) -> str | None:
    """数値として解釈できる場合は小数 2 桁の文字列に正規化する。"""

    try:
        decimal_value = Decimal(value)
    except InvalidOperation:
        return None
    if not decimal_value.is_finite():
        return ""
    rounded = decimal_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{rounded:.2f}"


def _normalize_datetime(value: str) -> str | None:
    """ISO 形式の日付・日時を公式仕様に近い形へ正規化する。"""

    candidate = value
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None

    if parsed.hour == 0 and parsed.minute == 0 and parsed.second == 0 and parsed.microsecond == 0:
        if "T" not in value and " " not in value:
            return parsed.date().isoformat()

    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return parsed.isoformat()


def normalize_cell(value: object) -> str:
    """評価用にセル値を正規化する。"""

    text = "" if value is None else str(value)
    text = text.strip().replace("\r\n", "\n").replace("\r", "\n")
    if text.lower() in NULL_TOKENS:
        return ""

    numeric = _normalize_numeric(text)
    if numeric is not None:
        return numeric

    parsed_datetime = _normalize_datetime(text)
    if parsed_datetime is not None:
        return parsed_datetime

    return text


def _column_signatures(table: list[list[str]]) -> Counter[tuple[str, ...]]:
    """ヘッダーを除いた列値から column signature の Counter を作る。"""

    if not table:
        return Counter()

    header = table[0]
    column_count = len(header)
    if column_count == 0:
        return Counter()

    columns: list[list[str]] = [[] for _ in range(column_count)]
    for row in table[1:]:
        for index in range(column_count):
            cell = row[index] if index < len(row) else ""
            columns[index].append(normalize_cell(cell))

    return Counter(tuple(sorted(column)) for column in columns)


def score_prediction(
    *,
    prediction_path: Path | None,
    gold_path: Path,
    penalty_lambda: float,
) -> TaskEvaluationResult:
    """1 つの prediction.csv を gold.csv と比較して評価する。"""

    task_id = gold_path.parent.name
    gold_signatures = _column_signatures(_read_csv_table(gold_path))
    gold_columns = sum(gold_signatures.values())

    if prediction_path is None or not prediction_path.exists():
        return TaskEvaluationResult(
            task_id=task_id,
            score=0.0,
            recall=0.0,
            matched_columns=0,
            gold_columns=gold_columns,
            predicted_columns=0,
            extra_columns=0,
            prediction_path=None,
            gold_path=str(gold_path),
            missing_prediction=True,
        )

    prediction_signatures = _column_signatures(_read_csv_table(prediction_path))
    predicted_columns = sum(prediction_signatures.values())
    matched_columns = sum(
        min(count, gold_signatures.get(signature, 0))
        for signature, count in prediction_signatures.items()
    )
    extra_columns = max(predicted_columns - matched_columns, 0)

    recall = matched_columns / gold_columns if gold_columns else float(predicted_columns == 0)
    redundancy_ratio = extra_columns / predicted_columns if predicted_columns else 0.0
    score = max(recall - penalty_lambda * redundancy_ratio, 0.0)

    return TaskEvaluationResult(
        task_id=task_id,
        score=round(score, 6),
        recall=round(recall, 6),
        matched_columns=matched_columns,
        gold_columns=gold_columns,
        predicted_columns=predicted_columns,
        extra_columns=extra_columns,
        prediction_path=str(prediction_path),
        gold_path=str(gold_path),
        missing_prediction=False,
    )


def evaluate_run(
    *,
    run_dir: Path,
    gold_dir: Path,
    penalty_lambda: float = 0.1,
) -> RunEvaluationResult:
    """run ディレクトリ配下の prediction.csv を公開 gold.csv で評価する。"""

    if penalty_lambda < 0:
        raise ValueError("penalty_lambda must be non-negative.")
    if not run_dir.is_dir():
        raise FileNotFoundError(f"Missing run directory: {run_dir}")
    if not gold_dir.is_dir():
        raise FileNotFoundError(f"Missing gold directory: {gold_dir}")

    gold_paths = sorted(
        gold_dir.glob(f"{TASK_DIR_PREFIX}*/gold.csv"),
        key=lambda path: int(path.parent.name.removeprefix(TASK_DIR_PREFIX)),
    )
    task_results = [
        score_prediction(
            prediction_path=run_dir / gold_path.parent.name / "prediction.csv",
            gold_path=gold_path,
            penalty_lambda=penalty_lambda,
        )
        for gold_path in gold_paths
    ]

    task_count = len(task_results)
    scored_task_count = sum(1 for result in task_results if not result.missing_prediction)
    missing_prediction_count = task_count - scored_task_count
    average_score = (
        sum(result.score for result in task_results) / task_count if task_count else 0.0
    )
    average_recall = (
        sum(result.recall for result in task_results) / task_count if task_count else 0.0
    )

    return RunEvaluationResult(
        task_count=task_count,
        scored_task_count=scored_task_count,
        missing_prediction_count=missing_prediction_count,
        average_score=round(average_score, 6),
        average_recall=round(average_recall, 6),
        penalty_lambda=penalty_lambda,
        tasks=task_results,
    )


def write_evaluation_json(path: Path, result: RunEvaluationResult) -> None:
    """評価結果を JSON ファイルへ書き出す。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n")
