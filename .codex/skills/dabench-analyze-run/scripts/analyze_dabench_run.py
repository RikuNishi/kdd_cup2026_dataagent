#!/usr/bin/env python3
"""DABench 実行結果を採点し、trace と runtime log から改善候補を抽出する。"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

NULL_TOKENS = {"", "null", "none", "nan", "nat", "<na>"}
TASK_DIR_PATTERN = re.compile(r"task_(\d+)$")


@dataclass(frozen=True, slots=True)
class TaskScore:
    """1 タスク分の評価結果。"""

    task_id: str
    score: float
    recall: float
    matched_columns: int
    gold_columns: int
    predicted_columns: int
    extra_columns: int
    status: str
    prediction_path: str | None
    gold_path: str


@dataclass(frozen=True, slots=True)
class TraceSummary:
    """1 タスク分の trace 要約。"""

    task_id: str
    trace_path: str
    succeeded: bool
    failure_reason: str | None
    step_count: int
    answer_present: bool
    e2e_elapsed_seconds: float | None
    tool_sequence: list[str]
    tool_errors: list[str]


def task_sort_key(task_id: str) -> tuple[int, str]:
    """task id を数値優先で並べるためのキーを返す。"""

    match = TASK_DIR_PATTERN.match(task_id)
    if match is None:
        return (10**9, task_id)
    return (int(match.group(1)), task_id)


def read_csv_table(path: Path) -> list[list[str]]:
    """CSV を文字列セルの 2 次元配列として読み込む。"""

    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [[cell for cell in row] for row in csv.reader(handle)]


def normalize_numeric(value: str) -> str | None:
    """数値として解釈できる場合は小数 2 桁の文字列に正規化する。"""

    try:
        decimal_value = Decimal(value)
    except InvalidOperation:
        return None
    if not decimal_value.is_finite():
        return ""
    rounded = decimal_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{rounded:.2f}"


def normalize_datetime(value: str) -> str | None:
    """ISO 形式の日付・日時を評価用に正規化する。"""

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

    numeric = normalize_numeric(text)
    if numeric is not None:
        return numeric

    parsed_datetime = normalize_datetime(text)
    if parsed_datetime is not None:
        return parsed_datetime

    return text


def column_signatures(table: list[list[str]]) -> Counter[tuple[str, ...]]:
    """ヘッダーを除いた列値から column signature を作る。"""

    if not table:
        return Counter()
    column_count = len(table[0])
    if column_count == 0:
        return Counter()

    columns: list[list[str]] = [[] for _ in range(column_count)]
    for row in table[1:]:
        for index in range(column_count):
            columns[index].append(normalize_cell(row[index] if index < len(row) else ""))
    return Counter(tuple(sorted(column)) for column in columns)


def score_task(prediction_path: Path, gold_path: Path, penalty_lambda: float) -> TaskScore:
    """1 タスクの prediction.csv と gold.csv を比較して評価する。"""

    task_id = gold_path.parent.name
    gold_signatures = column_signatures(read_csv_table(gold_path))
    gold_columns = sum(gold_signatures.values())

    if not prediction_path.exists():
        return TaskScore(
            task_id=task_id,
            score=0.0,
            recall=0.0,
            matched_columns=0,
            gold_columns=gold_columns,
            predicted_columns=0,
            extra_columns=0,
            status="missing",
            prediction_path=None,
            gold_path=str(gold_path),
        )

    prediction_signatures = column_signatures(read_csv_table(prediction_path))
    predicted_columns = sum(prediction_signatures.values())
    matched_columns = sum(
        min(count, gold_signatures.get(signature, 0))
        for signature, count in prediction_signatures.items()
    )
    extra_columns = max(predicted_columns - matched_columns, 0)
    recall = matched_columns / gold_columns if gold_columns else float(predicted_columns == 0)
    redundancy_ratio = extra_columns / predicted_columns if predicted_columns else 0.0
    score = max(recall - penalty_lambda * redundancy_ratio, 0.0)

    return TaskScore(
        task_id=task_id,
        score=round(score, 6),
        recall=round(recall, 6),
        matched_columns=matched_columns,
        gold_columns=gold_columns,
        predicted_columns=predicted_columns,
        extra_columns=extra_columns,
        status="ok",
        prediction_path=str(prediction_path),
        gold_path=str(gold_path),
    )


def evaluate_run(prediction_dir: Path, gold_dir: Path, penalty_lambda: float) -> dict[str, Any]:
    """run 全体の prediction.csv を公開 gold.csv で評価する。"""

    if penalty_lambda < 0:
        raise ValueError("penalty_lambda must be non-negative.")
    if not prediction_dir.is_dir():
        raise FileNotFoundError(f"Missing prediction directory: {prediction_dir}")
    if not gold_dir.is_dir():
        raise FileNotFoundError(f"Missing gold directory: {gold_dir}")

    gold_paths = sorted(gold_dir.glob("task_*/gold.csv"), key=lambda path: task_sort_key(path.parent.name))
    tasks = [
        score_task(prediction_dir / gold_path.parent.name / "prediction.csv", gold_path, penalty_lambda)
        for gold_path in gold_paths
    ]
    task_count = len(tasks)
    scored_task_count = sum(1 for task in tasks if task.status != "missing")
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


def parse_runtime_log(logs_dir: Path) -> dict[str, Any]:
    """runtime.log から実行サマリと task 行を抽出する。"""

    runtime_path = logs_dir / "runtime.log"
    result: dict[str, Any] = {
        "path": str(runtime_path),
        "exists": runtime_path.exists(),
        "start": {},
        "end": {},
        "tasks": [],
        "status_counts": {},
    }
    if not runtime_path.exists():
        return result

    task_statuses: Counter[str] = Counter()
    task_line_pattern = re.compile(r"task=(\S+) status=(\S+) .*?failure=(.*)$")
    key_value_pattern = re.compile(r"(\w+)=([^ ]+)")

    with runtime_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if "submit-run start" in line:
                result["start"] = dict(key_value_pattern.findall(line))
            elif "submit-run end" in line:
                result["end"] = dict(key_value_pattern.findall(line))
            elif " task=" in line:
                task_match = task_line_pattern.search(line)
                values = dict(key_value_pattern.findall(line))
                if task_match is not None:
                    values["task"] = task_match.group(1)
                    values["status"] = task_match.group(2)
                    values["failure"] = task_match.group(3)
                if values:
                    task_statuses.update([str(values.get("status", "unknown"))])
                    result["tasks"].append(values)

    result["status_counts"] = dict(sorted(task_statuses.items()))
    return result


def summarize_trace(trace_path: Path) -> TraceSummary:
    """trace.json を読み、診断に必要な項目だけを抽出する。"""

    data = json.loads(trace_path.read_text(encoding="utf-8"))
    task_id = str(data.get("task_id") or trace_path.parent.name)
    steps = data.get("steps")
    if not isinstance(steps, list):
        steps = []

    tool_sequence: list[str] = []
    tool_errors: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        action = str(step.get("action") or "unknown")
        tool_sequence.append(action)
        observation = step.get("observation")
        ok = bool(step.get("ok", True))
        if isinstance(observation, dict):
            ok = bool(observation.get("ok", ok))
            tool_name = str(observation.get("tool") or action)
            if not ok:
                content = observation.get("content")
                message = ""
                if isinstance(content, dict):
                    message = str(content.get("error") or content.get("message") or "")
                elif content is not None:
                    message = str(content)
                tool_errors.append(f"{tool_name}: {message}".strip())
        elif not ok:
            tool_errors.append(action)

    elapsed = data.get("e2e_elapsed_seconds")
    elapsed_seconds = float(elapsed) if isinstance(elapsed, int | float) else None
    answer_present = isinstance(data.get("answer"), dict)

    return TraceSummary(
        task_id=task_id,
        trace_path=str(trace_path),
        succeeded=bool(data.get("succeeded")),
        failure_reason=data.get("failure_reason"),
        step_count=len(steps),
        answer_present=answer_present,
        e2e_elapsed_seconds=elapsed_seconds,
        tool_sequence=tool_sequence,
        tool_errors=tool_errors,
    )


def collect_traces(logs_dir: Path) -> dict[str, TraceSummary]:
    """logs ディレクトリ配下の trace.json を task id ごとに集める。"""

    if not logs_dir.is_dir():
        return {}
    traces: dict[str, TraceSummary] = {}
    for trace_path in sorted(logs_dir.glob("task_*/trace.json"), key=lambda path: task_sort_key(path.parent.name)):
        try:
            summary = summarize_trace(trace_path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            summary = TraceSummary(
                task_id=trace_path.parent.name,
                trace_path=str(trace_path),
                succeeded=False,
                failure_reason=f"Trace parse failed: {exc}",
                step_count=0,
                answer_present=False,
                e2e_elapsed_seconds=None,
                tool_sequence=[],
                tool_errors=[f"trace: {exc}"],
            )
        traces[summary.task_id] = summary
    return traces


def count_prediction_files(prediction_dir: Path) -> int:
    """prediction.csv の個数を数える。"""

    if not prediction_dir.is_dir():
        return 0
    return sum(1 for _ in prediction_dir.glob("task_*/prediction.csv"))


def classify_task(task: dict[str, Any], trace: TraceSummary | None) -> tuple[str, str, str]:
    """task の主な失点原因と次に見るべき領域を推定する。"""

    if task["status"] == "missing":
        if trace is None:
            return ("missing prediction / missing trace", "runtime/config", "src/data_agent_baseline/run/runner.py")
        if trace.failure_reason:
            return ("execution failure before answer", "agent loop", "src/data_agent_baseline/agents/react.py")
        if not trace.answer_present:
            return ("answer not submitted", "prompt", "src/data_agent_baseline/agents/prompt.py")
        return ("answer existed but prediction was not written", "runtime/config", "src/data_agent_baseline/run/runner.py")

    if trace is not None and trace.tool_errors:
        return ("tool error during reasoning", "tool", "src/data_agent_baseline/tools/registry.py")
    if task["score"] == 0 and task["predicted_columns"] > 0:
        return ("wrong values or wrong target columns", "prompt", "src/data_agent_baseline/agents/prompt.py")
    if task["extra_columns"] > 0:
        return ("extra predicted columns", "data handling", "src/data_agent_baseline/tools/registry.py")
    if 0 < task["recall"] < 1:
        return ("partial column match", "data handling", "src/data_agent_baseline/agents/prompt.py")
    if trace is not None and trace.step_count >= 10:
        return ("long reasoning path", "agent loop", "src/data_agent_baseline/agents/react.py")
    return ("needs manual trace review", "prompt", "src/data_agent_baseline/agents/prompt.py")


def build_recommendations(evaluation: dict[str, Any], traces: dict[str, TraceSummary], top_k: int) -> list[dict[str, Any]]:
    """低スコア task から改善候補を作る。"""

    tasks = sorted(
        evaluation["tasks"],
        key=lambda item: (
            item["score"],
            0 if item["status"] == "ok" else 1,
            -item["extra_columns"],
            task_sort_key(item["task_id"]),
        ),
    )
    recommendations: list[dict[str, Any]] = []
    for task in tasks[:top_k]:
        trace = traces.get(task["task_id"])
        reason, category, next_file = classify_task(task, trace)
        recommendations.append(
            {
                "task_id": task["task_id"],
                "score": task["score"],
                "recall": task["recall"],
                "status": task["status"],
                "reason": reason,
                "category": category,
                "next_file": next_file,
                "trace_path": trace.trace_path if trace is not None else None,
            }
        )
    return recommendations


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    """CLI 引数に基づいて分析レポートを構築する。"""

    evaluation = evaluate_run(args.prediction_dir, args.gold_dir, args.penalty_lambda)
    runtime = parse_runtime_log(args.logs_dir)
    traces = collect_traces(args.logs_dir)
    trace_values = list(traces.values())
    failure_reasons = Counter(trace.failure_reason or "-" for trace in trace_values if trace.failure_reason)
    tool_errors = Counter(error.split(":", 1)[0] for trace in trace_values for error in trace.tool_errors)
    answer_missing = sorted(
        [trace.task_id for trace in trace_values if not trace.answer_present],
        key=task_sort_key,
    )
    slow_tasks = sorted(
        [
            {
                "task_id": trace.task_id,
                "e2e_elapsed_seconds": trace.e2e_elapsed_seconds,
                "step_count": trace.step_count,
            }
            for trace in trace_values
            if trace.e2e_elapsed_seconds is not None
        ],
        key=lambda item: (-float(item["e2e_elapsed_seconds"]), task_sort_key(str(item["task_id"]))),
    )[: args.top_k]
    many_step_tasks = sorted(
        [
            {
                "task_id": trace.task_id,
                "step_count": trace.step_count,
                "e2e_elapsed_seconds": trace.e2e_elapsed_seconds,
            }
            for trace in trace_values
        ],
        key=lambda item: (-int(item["step_count"]), task_sort_key(str(item["task_id"]))),
    )[: args.top_k]

    return {
        "paths": {
            "prediction_dir": str(args.prediction_dir),
            "logs_dir": str(args.logs_dir),
            "gold_dir": str(args.gold_dir),
            "input_dir": str(args.input_dir),
        },
        "evaluation": evaluation,
        "run_files": {
            "prediction_count": count_prediction_files(args.prediction_dir),
            "trace_count": len(traces),
        },
        "runtime": runtime,
        "trace_analysis": {
            "failure_reasons": dict(failure_reasons.most_common()),
            "tool_errors": dict(tool_errors.most_common()),
            "answer_missing_tasks": answer_missing,
            "slow_tasks": slow_tasks,
            "many_step_tasks": many_step_tasks,
        },
        "recommendations": build_recommendations(evaluation, traces, args.top_k),
    }


def markdown_table(headers: list[str], rows: list[list[object]]) -> str:
    """Markdown table を組み立てる。"""

    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def format_markdown(report: dict[str, Any]) -> str:
    """分析レポートを Markdown で整形する。"""

    evaluation = report["evaluation"]
    run_files = report["run_files"]
    runtime = report["runtime"]
    trace_analysis = report["trace_analysis"]
    recommendations = report["recommendations"]

    lines = [
        "# DABench Run Analysis",
        "",
        "## Summary",
        "",
        markdown_table(
            ["Item", "Value"],
            [
                ["tasks", evaluation["task_count"]],
                ["scored_tasks", evaluation["scored_task_count"]],
                ["missing_predictions", evaluation["missing_prediction_count"]],
                ["prediction_files", run_files["prediction_count"]],
                ["trace_files", run_files["trace_count"]],
                ["average_score", f"{evaluation['average_score']:.6f}"],
                ["average_recall", f"{evaluation['average_recall']:.6f}"],
                ["penalty_lambda", f"{evaluation['penalty_lambda']:.6f}"],
            ],
        ),
        "",
        "ローカル近似評価です。hidden test の公式スコアではありません。",
        "",
        "## Runtime",
        "",
        markdown_table(
            ["Item", "Value"],
            [
                ["runtime_log", runtime["path"] if runtime["exists"] else "missing"],
                ["start_tasks", runtime.get("start", {}).get("tasks", "-")],
                ["end_attempted", runtime.get("end", {}).get("attempted", "-")],
                ["end_succeeded", runtime.get("end", {}).get("succeeded", "-")],
                ["end_failed", runtime.get("end", {}).get("failed", "-")],
                ["elapsed_seconds", runtime.get("end", {}).get("elapsed_seconds", "-")],
                ["status_counts", json.dumps(runtime.get("status_counts", {}), ensure_ascii=False)],
            ],
        ),
        "",
        "## Low Score Tasks",
        "",
    ]

    low_rows = [
        [
            item["task_id"],
            f"{item['score']:.6f}",
            f"{item['recall']:.6f}",
            item["status"],
            item["reason"],
            item["category"],
            item["next_file"],
        ]
        for item in recommendations
    ]
    lines.append(markdown_table(["Task", "Score", "Recall", "Status", "Reason", "Category", "Next file"], low_rows))

    lines.extend(["", "## Trace Analysis", ""])
    lines.append(
        markdown_table(
            ["Item", "Value"],
            [
                ["failure_reasons", json.dumps(trace_analysis["failure_reasons"], ensure_ascii=False)],
                ["tool_errors", json.dumps(trace_analysis["tool_errors"], ensure_ascii=False)],
                ["answer_missing_tasks", ", ".join(trace_analysis["answer_missing_tasks"]) or "-"],
            ],
        )
    )

    lines.extend(["", "## Slow Tasks", ""])
    lines.append(
        markdown_table(
            ["Task", "Elapsed", "Steps"],
            [
                [item["task_id"], item["e2e_elapsed_seconds"], item["step_count"]]
                for item in trace_analysis["slow_tasks"]
            ],
        )
    )

    lines.extend(["", "## Many Step Tasks", ""])
    lines.append(
        markdown_table(
            ["Task", "Steps", "Elapsed"],
            [
                [item["task_id"], item["step_count"], item["e2e_elapsed_seconds"]]
                for item in trace_analysis["many_step_tasks"]
            ],
        )
    )

    lines.extend(["", "## Task Scores", ""])
    task_rows = [
        [
            task["task_id"],
            f"{task['score']:.6f}",
            f"{task['recall']:.6f}",
            task["matched_columns"],
            task["gold_columns"],
            task["predicted_columns"],
            task["extra_columns"],
            task["status"],
        ]
        for task in evaluation["tasks"]
    ]
    lines.append(markdown_table(["Task", "Score", "Recall", "Matched", "Gold", "Pred", "Extra", "Status"], task_rows))
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    """CLI 引数を解析する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--prediction-dir", default=Path("dabench-output"), type=Path)
    parser.add_argument("--logs-dir", default=Path("dabench-logs"), type=Path)
    parser.add_argument("--gold-dir", default=Path("data/public/output"), type=Path)
    parser.add_argument("--input-dir", default=Path("data/public/input"), type=Path)
    parser.add_argument("--top-k", default=10, type=int)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--penalty-lambda", default=0.1, type=float)
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint。"""

    args = parse_args()
    if args.top_k < 1:
        raise ValueError("--top-k must be at least 1.")
    report = build_report(args)
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print(format_markdown(report), end="")


if __name__ == "__main__":
    main()
