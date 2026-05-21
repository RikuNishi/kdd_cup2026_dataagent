from __future__ import annotations

import re
from typing import Any

from data_agent_baseline.agents.runtime import StepRecord

HELPER_WARNING_FRAGMENT = "separate requested output attributes from helper attributes"
REPAIR_ACTION = "answer_repair"

_GENERIC_HELPER_COLUMN_PATTERN = re.compile(
    r"(^id$|_id$|id$|score|cost|rank|position|order|date|time|key|count|sum|total)",
    flags=re.IGNORECASE,
)
_AVERAGE_COLUMN_PATTERN = re.compile(r"(^avg_|average|mean)", flags=re.IGNORECASE)


def _latest_validation_content(steps: list[StepRecord]) -> dict[str, Any] | None:
    """直近の validate_answer 結果を返す。"""

    for step in reversed(steps):
        if step.action != "validate_answer":
            continue
        content = step.observation.get("content")
        if isinstance(content, dict):
            return content
    return None


def _has_repaired_answer(steps: list[StepRecord]) -> bool:
    """この task ですでに回答 repair を行ったかを返す。"""

    return any(step.action == REPAIR_ACTION for step in steps)


def _answer_columns(action_input: dict[str, Any]) -> list[str]:
    """answer action_input から column 名を安全に取り出す。"""

    raw_columns = action_input.get("columns")
    if not isinstance(raw_columns, list):
        return []
    return [str(column) for column in raw_columns if isinstance(column, str)]


def _has_probable_helper_columns(columns: list[str], question: str) -> bool:
    """出力列が helper 属性らしいかを緩く判定する。"""

    normalized_question = question.lower()
    question_tokens = set(re.findall(r"[a-z0-9]+", normalized_question))
    ranking_question = bool(re.search(r"\b(tie|tied|same|lowest|highest|minimum|maximum|most|least)\b", normalized_question))
    for column in columns:
        normalized_column = column.strip().lower()
        if not normalized_column:
            continue
        column_tokens = {
            token
            for token in re.split(r"[^a-z0-9]+", normalized_column)
            if token and token not in {"avg", "average", "sum", "total", "count"}
        }
        if column_tokens and column_tokens <= question_tokens:
            if _AVERAGE_COLUMN_PATTERN.search(normalized_column) and not (
                {"avg", "average", "mean"} & question_tokens
            ):
                pass
            elif not (
                ranking_question
                and re.search(r"\b(cost|score|rank|position|date|time|total|count)\b", normalized_column)
            ):
                continue
        if not _GENERIC_HELPER_COLUMN_PATTERN.search(normalized_column):
            continue
        return True
    return False


def _is_probable_helper_column(column: str, question: str) -> bool:
    """列が出力ではなく helper らしいかを返す。"""

    normalized_column = column.strip().lower()
    if not normalized_column:
        return False
    if not _GENERIC_HELPER_COLUMN_PATTERN.search(normalized_column):
        return False

    normalized_question = question.lower()
    question_tokens = set(re.findall(r"[a-z0-9]+", normalized_question))
    column_tokens = {
        token
        for token in re.split(r"[^a-z0-9]+", normalized_column)
        if token and token not in {"avg", "average", "sum", "total", "count"}
    }
    ranking_question = bool(
        re.search(r"\b(tie|tied|same|lowest|highest|minimum|maximum|most|least|ranked?)\b", normalized_question)
    )
    if column_tokens and column_tokens <= question_tokens:
        if ranking_question and re.search(
            r"\b(cost|score|rank|position|date|time|total|count)\b",
            normalized_column,
        ):
            return True
        if normalized_column in {"id", "key"}:
            return True
        return False
    return True


def project_helper_columns_from_answer(
    *,
    steps: list[StepRecord],
    action_input: dict[str, Any],
) -> dict[str, Any] | None:
    """helper 警告がある answer を requested 列だけに射影できる場合は返す。"""

    validation = _latest_validation_content(steps)
    if validation is None:
        return None

    warnings = validation.get("warnings")
    if not isinstance(warnings, list):
        return None
    warning_text = "\n".join(str(warning) for warning in warnings)
    if HELPER_WARNING_FRAGMENT not in warning_text:
        return None

    columns = _answer_columns(action_input)
    raw_rows = action_input.get("rows")
    if not columns or not isinstance(raw_rows, list):
        return None

    question = str(validation.get("question", ""))
    keep_indexes = [
        index
        for index, column in enumerate(columns)
        if not _is_probable_helper_column(column, question)
    ]
    if not keep_indexes or len(keep_indexes) == len(columns):
        return None

    projected_rows: list[list[Any]] = []
    for row in raw_rows:
        if not isinstance(row, list) or len(row) != len(columns):
            return None
        projected_rows.append([row[index] for index in keep_indexes])

    return {
        "columns": [columns[index] for index in keep_indexes],
        "rows": projected_rows,
    }


def build_answer_repair_observation(
    *,
    steps: list[StepRecord],
    action_input: dict[str, Any],
    step_index: int,
    max_steps: int,
) -> dict[str, Any] | None:
    """answer 実行前に 1 回だけ余分列修正を促す observation を作る。

    形状エラーや空回答は扱わず、validate_answer が helper 属性 warning を出した直後の
    answer に対して、submit を一度だけ保留して列削減を促す。
    """

    if step_index >= max_steps:
        return None
    if _has_repaired_answer(steps):
        return None

    validation = _latest_validation_content(steps)
    if validation is None:
        return None

    warnings = validation.get("warnings")
    if not isinstance(warnings, list):
        return None
    warning_text = "\n".join(str(warning) for warning in warnings)
    if HELPER_WARNING_FRAGMENT not in warning_text:
        return None

    columns = _answer_columns(action_input)
    if not columns:
        return None

    question = str(validation.get("question", ""))
    should_repair = _has_probable_helper_columns(columns, question)
    if not should_repair:
        return None

    return {
        "ok": False,
        "tool": REPAIR_ACTION,
        "content": {
            "repair_reason": "answer still appears to include helper attributes after validate_answer warnings.",
            "repair_instruction": (
                "Revise the final answer once before submitting. Output only the requested attributes from the "
                "question. Remove values used only for filtering, joining, grouping, ranking, sorting, formula "
                "calculation, tie checking, or verification. Project the existing result to the requested output "
                "attributes when the correct values are already present. For requested natural-language items such "
                "as comments, messages, notes, descriptions, or post bodies, prefer the text/body/content field "
                "unless IDs, scores, dates, or the full record are explicitly requested. This repair will not repeat."
            ),
            "previous_warnings": warnings,
            "current_columns": columns,
            "question": question,
        },
    }
