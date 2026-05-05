"""最終回答候補の形状と採点上のリスクを検査するツール。"""

from __future__ import annotations

import re
from typing import Any

from data_agent_baseline.benchmark.schema import PublicTask

SCALAR_QUESTION_PATTERN = re.compile(
    r"\b(what|which|how many|how much|average|percentage|ratio|count|number of)\b",
    flags=re.IGNORECASE,
)
MULTI_ROW_QUESTION_PATTERN = re.compile(
    r"\b(list|all|each|every|members|schools|countries|transactions|people|patients)\b",
    flags=re.IGNORECASE,
)
TIE_HINT_PATTERN = re.compile(r"\b(tie|tied|same|lowest|highest|minimum|maximum|most|least)\b", re.IGNORECASE)
NAME_QUESTION_PATTERN = re.compile(r"\b(name|names|person|people|patient|patients|member|members)\b", re.IGNORECASE)
SOURCE_EVIDENCE_PATTERN = re.compile(
    r"\b(execute_data_query|execute_context_sql|execute_python|retrieve_context|read_doc|read_csv|read_json|"
    r"sql|python|query|computed|calculated|grouped|filtered|joined|source|table|csv|json|db|doc)\b",
    flags=re.IGNORECASE,
)


def _is_numeric_like(value: Any) -> bool:
    """値が数値らしい文字列かどうかを返す。"""

    try:
        float(str(value).strip())
    except ValueError:
        return False
    return True


def _numeric_value(value: Any) -> float | None:
    """値を数値へ変換できる場合だけ float を返す。"""

    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _looks_like_joined_name(value: Any) -> bool:
    """first/last name などを結合した値らしいかを緩く判定する。"""

    text = str(value).strip()
    if not text:
        return False
    return bool(re.search(r"\S+\s+\S+", text))


def validate_answer_table(
    task: PublicTask,
    *,
    columns: Any,
    rows: Any,
    notes: str = "",
) -> dict[str, Any]:
    """answer tool に渡す前の候補 table を検査し、警告とエラーを返す。"""

    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(columns, list) or not columns:
        errors.append("columns must be a non-empty list.")
        columns = []
    elif not all(isinstance(column, str) and column.strip() for column in columns):
        errors.append("every column name must be a non-empty string.")

    if not isinstance(rows, list):
        errors.append("rows must be a list.")
        rows = []

    normalized_rows = rows if isinstance(rows, list) else []
    for index, row in enumerate(normalized_rows):
        if not isinstance(row, list):
            errors.append(f"row {index} is not a list.")
            continue
        if columns and len(row) != len(columns):
            errors.append(f"row {index} has {len(row)} cells but {len(columns)} columns were provided.")

    if len(set(columns)) != len(columns):
        warnings.append("duplicate column names are present; column names are ignored by scoring but reduce readability.")

    question = task.question
    row_count = len(normalized_rows)
    column_count = len(columns)
    if row_count == 0:
        warnings.append("answer has zero rows; only use this when the observed data truly has no matches.")

    if SCALAR_QUESTION_PATTERN.search(question) and column_count > 2:
        warnings.append("question appears scalar or narrow; extra columns can create redundancy penalty.")

    if MULTI_ROW_QUESTION_PATTERN.search(question) and row_count == 1:
        warnings.append("question may request multiple rows; verify that only one result is valid.")

    combined_text = f"{question}\n{notes}"
    if TIE_HINT_PATTERN.search(combined_text) and row_count == 1:
        warnings.append("ranking/tie language detected; verify all tied rows are included.")

    numeric_cells = [
        value
        for row in normalized_rows
        if isinstance(row, list)
        for value in row
        if _is_numeric_like(value)
    ]
    if numeric_cells and any("e" in str(value).lower() for value in numeric_cells):
        warnings.append("scientific notation detected; prefer a plain decimal string with sufficient precision.")

    numeric_values = [
        numeric_value
        for value in numeric_cells
        if (numeric_value := _numeric_value(value)) is not None
    ]
    if numeric_values and all(numeric_value == 0 for numeric_value in numeric_values):
        warnings.append(
            "all numeric answer values are zero; verify source formatting, labels, dates, nulls, and code/value mappings."
        )

    column_names = [column.lower().strip() for column in columns]
    single_name_column = column_count == 1 and any("name" in column_name for column_name in column_names)
    if NAME_QUESTION_PATTERN.search(question) and single_name_column:
        joined_name_cells = [
            value
            for row in normalized_rows
            if isinstance(row, list)
            for value in row
            if _looks_like_joined_name(value)
        ]
        if joined_name_cells:
            warnings.append(
                "single name column contains space-joined values; verify you did not concatenate first_name/last_name "
                "or other source columns unless explicitly required."
            )

    if not notes.strip():
        warnings.append("notes are empty; include the exact source tool/query used to compute the candidate answer.")
    elif not SOURCE_EVIDENCE_PATTERN.search(notes):
        warnings.append(
            "notes do not mention a source data tool/query; verify the answer was computed from source data, not only observations."
        )

    return {
        "ok": not errors,
        "ready_for_answer": not errors,
        "errors": errors,
        "warnings": warnings,
        "column_count": column_count,
        "row_count": row_count,
        "question": question,
        "recommendation": (
            "Call answer with this table if warnings were checked against observed data."
            if not errors
            else "Fix validation errors before calling answer."
        ),
    }
