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
HELPER_COLUMN_PATTERN = re.compile(
    r"(^id$|_id$|id$|score|cost|rank|position|order|date|time|key|count|sum|total)",
    flags=re.IGNORECASE,
)
AVERAGE_COLUMN_PATTERN = re.compile(r"(^avg_|average|mean)", flags=re.IGNORECASE)
SEMANTIC_NOTE_PATTERNS = {
    "formula_or_aggregation": re.compile(r"\b(formula|aggregation|aggregate|sum|avg|average|count|ratio|divid|minus|difference|calculated|computed)\b", re.IGNORECASE),
    "grain": re.compile(r"\b(grain|row|entity|per |grouped by|group by|level)\b", re.IGNORECASE),
    "join_keys": re.compile(r"\b(join key|join keys|join|linked|foreign key|=)\b", re.IGNORECASE),
    "tie_check": re.compile(r"\b(tie|ties|tied|unique|no tie|checked)\b", re.IGNORECASE),
    "knowledge_rule": re.compile(r"\b(knowledge|rule|definition|none applicable|not applicable)\b", re.IGNORECASE),
}


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


def _question_tokens(question: str) -> set[str]:
    """質問文から列名照合用の token 集合を作る。"""

    return {token.lower() for token in re.findall(r"[A-Za-z0-9]+", question)}


def _column_tokens(column: str) -> set[str]:
    """列名から質問文照合用の token 集合を作る。"""

    return {
        token.lower()
        for token in re.split(r"[^A-Za-z0-9]+", column)
        if token and token.lower() not in {"avg", "average", "sum", "total", "count"}
    }


def _column_looks_requested(column: str, question: str) -> bool:
    """列名が質問で直接求められた属性らしいかを緩く判定する。"""

    q_tokens = _question_tokens(question)
    c_tokens = _column_tokens(column)
    if not c_tokens:
        return False
    if c_tokens <= q_tokens:
        if AVERAGE_COLUMN_PATTERN.search(column):
            return bool({"avg", "average", "mean"} & q_tokens)
        return True
    return False


def _has_helper_like_column(columns: list[str], question: str) -> bool:
    """回答列に helper 属性らしい列が含まれるかを返す。"""

    ranking_question = bool(TIE_HINT_PATTERN.search(question))
    for column in columns:
        normalized_column = column.strip().lower()
        if not normalized_column:
            continue
        if _column_looks_requested(normalized_column, question) and not (
            ranking_question and re.search(r"\b(cost|score|rank|position|date|time|total|count)\b", normalized_column)
        ):
            continue
        if HELPER_COLUMN_PATTERN.search(normalized_column):
            return True
    return False


def validate_answer_table(
    task: PublicTask,
    *,
    columns: Any,
    rows: Any,
    notes: str = "",
    question_contract: dict[str, Any] | None = None,
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
    requested_contract_columns: list[str] = []
    if isinstance(question_contract, dict):
        raw_requested = question_contract.get("requested_output_attributes")
        if isinstance(raw_requested, list):
            requested_contract_columns = [
                str(column).strip()
                for column in raw_requested
                if str(column).strip()
            ]
        if requested_contract_columns:
            normalized_contract_columns = {
                re.sub(r"[^a-z0-9]+", "", column.lower())
                for column in requested_contract_columns
            }
            normalized_answer_columns = {
                re.sub(r"[^a-z0-9]+", "", str(column).lower())
                for column in columns
            }
            if normalized_contract_columns != normalized_answer_columns:
                warnings.append(
                    "answer columns differ from the latest question_contract requested_output_attributes. "
                    f"contract={requested_contract_columns}; answer={columns}. Revise columns or update the "
                    "contract before submitting."
                )
        id_like_requested = [
            column
            for column in requested_contract_columns
            if re.search(r"(^id$|_id$|id$|customerid|personid|userid)", column, flags=re.IGNORECASE)
        ]
        if id_like_requested and not re.search(r"\b(id|identifier|number|no\\.|customer id|user id|person id)\b", question, flags=re.IGNORECASE):
            warnings.append(
                "question_contract requests identifier-like output columns, but the question does not explicitly "
                "ask for identifiers. Verify whether those IDs are helper attributes."
            )
    if row_count == 0:
        warnings.append(
            "answer has zero rows; before submitting, verify date coverage, alternate formats, spelling/case, "
            "related files, and join keys. Empty answers are valid only after those checks."
        )

    has_helper_like_column = column_count > 1 and _has_helper_like_column(columns, question)
    if SCALAR_QUESTION_PATTERN.search(question) and column_count > 1 and (
        column_count > 2 or has_helper_like_column
    ):
        warnings.append(
            "question appears scalar or narrow; separate requested output attributes from helper attributes. "
            "Remove values used only for filtering, joining, grouping, ranking, sorting, formula calculation, "
            "or verification unless the question directly asks for them."
        )
    elif has_helper_like_column:
        warnings.append(
            "separate requested output attributes from helper attributes. Remove values used only for filtering, "
            "joining, grouping, ranking, sorting, formula calculation, or verification unless the question directly "
            "asks for them."
        )

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
                "single name column contains space-joined values; if the source has first_name/last_name or similar "
                "separate fields, prefer those original source columns unless the question explicitly requires one "
                "combined string column."
            )

    if not notes.strip():
        warnings.append("notes are empty; include the exact source tool/query used to compute the candidate answer.")
    elif not SOURCE_EVIDENCE_PATTERN.search(notes):
        warnings.append(
            "notes do not mention a source data tool/query; verify the answer was computed from source data, not only observations."
        )
    if notes.strip():
        missing_semantic_notes = [
            name
            for name, pattern in SEMANTIC_NOTE_PATTERNS.items()
            if pattern.search(notes) is None
        ]
        if missing_semantic_notes:
            warnings.append(
                "notes should state semantic evidence before answer: "
                f"{', '.join(missing_semantic_notes)}. Include formula/aggregation, grain, join keys, "
                "tie check, and knowledge rule applied or none applicable."
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
            "Warnings do not block submission. If steps remain, revise the candidate to address them, especially by "
            "outputting only requested attributes, removing helper attributes, or preserving original source columns. "
            "If you are near the step limit, submit the best verified table rather than leaving the task unanswered."
            if not errors
            else "Fix validation errors before calling answer."
        ),
    }
