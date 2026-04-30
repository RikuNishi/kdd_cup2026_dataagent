from __future__ import annotations

import csv
from pathlib import Path

from data_agent_baseline.evaluation import evaluate_run, normalize_cell, score_prediction


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerows(rows)


def test_normalize_cell_handles_numeric_and_null_values() -> None:
    assert normalize_cell("4200000") == "4200000.00"
    assert normalize_cell("0.005") == "0.01"
    assert normalize_cell(" NaN ") == ""
    assert normalize_cell("East Asia") == "East Asia"


def test_score_prediction_ignores_column_names_and_row_order(tmp_path: Path) -> None:
    gold_path = tmp_path / "gold" / "task_1" / "gold.csv"
    prediction_path = tmp_path / "run" / "task_1" / "prediction.csv"
    _write_csv(
        gold_path,
        [
            ["name", "amount"],
            ["A", "1"],
            ["B", "2"],
        ],
    )
    _write_csv(
        prediction_path,
        [
            ["different_name", "different_amount"],
            ["B", "2.00"],
            ["A", "1.00"],
        ],
    )

    result = score_prediction(
        prediction_path=prediction_path,
        gold_path=gold_path,
        penalty_lambda=0.1,
    )

    assert result.score == 1.0
    assert result.recall == 1.0
    assert result.matched_columns == 2
    assert result.extra_columns == 0


def test_evaluate_run_penalizes_extra_columns_and_missing_predictions(tmp_path: Path) -> None:
    gold_dir = tmp_path / "gold"
    run_dir = tmp_path / "run"
    _write_csv(gold_dir / "task_1" / "gold.csv", [["wanted"], ["A"], ["B"]])
    _write_csv(gold_dir / "task_2" / "gold.csv", [["wanted"], ["C"]])
    _write_csv(
        run_dir / "task_1" / "prediction.csv",
        [
            ["wanted", "extra"],
            ["B", "X"],
            ["A", "Y"],
        ],
    )

    result = evaluate_run(run_dir=run_dir, gold_dir=gold_dir, penalty_lambda=0.1)

    assert result.task_count == 2
    assert result.scored_task_count == 1
    assert result.missing_prediction_count == 1
    assert result.tasks[0].score == 0.95
    assert result.tasks[1].missing_prediction
    assert result.average_score == 0.475
