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
RANKING_PATTERN = re.compile(r"\b(lowest|highest|minimum|maximum|most|least)\b", re.IGNORECASE)
TIE_EVIDENCE_PATTERN = re.compile(r"\b(tie|tied|no tie|all ties|next)\b", re.IGNORECASE)
DUPLICATE_EVIDENCE_PATTERN = re.compile(
    r"\b(duplicate rows are intentional|distinct records|distinct races|different ids|different raceids)\b",
    re.IGNORECASE,
)
SOURCE_CHOICE_PATTERN = re.compile(r"\b(source table|source column)\b", re.IGNORECASE)
COMPARISON_EVIDENCE_PATTERN = re.compile(
    r"\b(compare|compared|candidate|candidates|alternative|alternatives|reject|rejected|not|instead|vs|versus|rather than|over)\b",
    re.IGNORECASE,
)
AGGREGATED_COST_PATTERN = re.compile(
    r"\b(sum|summed|summing|total|aggregate|aggregated)\s*\(?\s*(?:[a-z_]+\.)?cost\b",
    re.IGNORECASE,
)
AGGREGATION_EVIDENCE_PATTERN = re.compile(r"\b(sum|summed|summing|total|aggregate|aggregated)\b", re.IGNORECASE)
ROW_LEVEL_COST_PATTERN = re.compile(
    r"\b(row-level|individual|per expense|expense row|non-aggregated|min\s*\(\s*(?:[a-z_]+\.)?cost|minimum\s+(?:[a-z_]+\.)?cost)\b",
    re.IGNORECASE,
)
TOTAL_COST_QUESTION_PATTERN = re.compile(r"\b(total|sum|overall|aggregate|aggregated|expenditure)\b", re.IGNORECASE)
AMBIGUOUS_TERM_EVIDENCE = {
    "cost": {"cost", "amount", "spent", "remaining"},
    "type": {"type", "category"},
    "number": {"number", "position", "rank", "round"},
    "severe": {"severe", "knowledge", "where", "=", "value", "level"},
}
AMBIGUOUS_OUTPUT_COLUMNS = {
    "diagnosis",
    "type",
    "category",
    "cost",
    "amount",
    "spent",
    "number",
    "position",
    "round",
}


def _is_numeric_like(value: Any) -> bool:
    """値が数値らしい文字列かどうかを返す。"""

    try:
        float(str(value).strip())
    except ValueError:
        return False
    return True


def _normalized_row(row: list[Any]) -> tuple[str, ...]:
    """重複検出用に row を文字列 tuple へ正規化する。"""

    return tuple("" if value is None else str(value).strip() for value in row)


def _evidence_term_count(text: str, terms: set[str]) -> int:
    """notes に含まれる根拠語の個数を返す。"""

    lowered = text.lower()
    return sum(1 for term in terms if term in lowered)


def _has_column_reference(text: str, term: str) -> bool:
    """notes に table.column 形式の根拠が含まれるかを返す。"""

    return re.search(rf"\b[a-zA-Z_][\w]*\.{re.escape(term)}\b", text) is not None


def _has_ambiguous_term_evidence(term: str, notes: str, evidence_terms: set[str]) -> bool:
    """曖昧語に対する notes の根拠が十分かを判定する。"""

    lowered = notes.lower()
    evidence_count = _evidence_term_count(lowered, evidence_terms)
    if term == "cost":
        return (
            evidence_count >= 3
            or (
                evidence_count >= 2
                and (
                    _has_column_reference(lowered, "cost")
                    or _has_column_reference(lowered, "spent")
                    or "not " in lowered
                    or "instead" in lowered
                )
            )
        )
    if term in {"type", "number"}:
        return evidence_count >= 2 or _has_column_reference(lowered, term)
    return evidence_count >= 1


def _has_source_choice(notes: str, columns: list[str]) -> bool:
    """曖昧な出力列の source choice が notes にあるかを返す。"""

    lowered = notes.lower()
    if SOURCE_CHOICE_PATTERN.search(lowered):
        return True
    return all(_has_column_reference(lowered, column.lower()) for column in columns)


def _mentioned_terms(text: str, terms: set[str]) -> set[str]:
    """notes に出現する候補語を返す。"""

    lowered = text.lower()
    return {term for term in terms if re.search(rf"\b{re.escape(term)}\b", lowered)}


def _has_candidate_comparison(term: str, notes: str) -> bool:
    """曖昧候補を比較・棄却した痕跡が notes にあるかを返す。"""

    if not COMPARISON_EVIDENCE_PATTERN.search(notes):
        return False
    if term == "cost":
        return len(_mentioned_terms(notes, {"cost", "spent", "amount", "remaining"})) >= 2
    if term == "number":
        return len(_mentioned_terms(notes, {"position", "positiontext", "rank", "round", "number"})) >= 2
    if term == "type":
        return len(_mentioned_terms(notes, {"type", "category", "description"})) >= 2
    return True


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
    blocking_warnings: list[str] = []

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
    comparable_rows = [
        _normalized_row(row)
        for row in normalized_rows
        if isinstance(row, list)
    ]
    duplicate_row_count = len(comparable_rows) - len(set(comparable_rows))
    if duplicate_row_count > 0:
        message = f"duplicate answer rows detected ({duplicate_row_count}); verify duplicates are intentional."
        warnings.append(message)

    if row_count == 0:
        warnings.append("answer has zero rows; only use this when the observed data truly has no matches.")

    if SCALAR_QUESTION_PATTERN.search(question) and column_count > 2:
        warnings.append("question appears scalar or narrow; extra columns can create redundancy penalty.")

    if MULTI_ROW_QUESTION_PATTERN.search(question) and row_count == 1:
        warnings.append("question may request multiple rows; verify that only one result is valid.")

    combined_text = f"{question}\n{notes}"
    if TIE_HINT_PATTERN.search(combined_text) and row_count == 1:
        warnings.append("ranking/tie language detected; verify all tied rows are included.")
    if RANKING_PATTERN.search(question) and not TIE_EVIDENCE_PATTERN.search(notes):
        message = "ranking question detected; notes should state whether ties were checked."
        warnings.append(message)

    lowered_question = question.lower()
    for term, evidence_terms in AMBIGUOUS_TERM_EVIDENCE.items():
        if re.search(rf"\b{re.escape(term)}\b", lowered_question) is None:
            continue
        if not _has_ambiguous_term_evidence(term, notes, evidence_terms):
            message = (
                f"ambiguous term '{term}' detected; notes should explain candidate columns "
                "or knowledge evidence."
            )
            warnings.append(message)
        if term in {"cost", "number", "type"} and not _has_candidate_comparison(term, notes):
            message = (
                f"ambiguous term '{term}' requires candidate comparison; notes should mention "
                "the selected candidate and rejected alternatives."
            )
            warnings.append(message)

    if (
        re.search(r"\bcost\b", lowered_question)
        and RANKING_PATTERN.search(question)
        and not TOTAL_COST_QUESTION_PATTERN.search(question)
        and (AGGREGATED_COST_PATTERN.search(notes) or AGGREGATION_EVIDENCE_PATTERN.search(notes))
        and not ROW_LEVEL_COST_PATTERN.search(notes)
    ):
        message = (
            "ranking cost question appears to use aggregated cost; notes should compare "
            "row-level MIN/MAX cost against aggregate total cost before answering."
        )
        warnings.append(message)

    ambiguous_answer_columns = [
        str(column)
        for column in columns
        if isinstance(column, str) and column.strip().lower() in AMBIGUOUS_OUTPUT_COLUMNS
    ]
    if ambiguous_answer_columns and not _has_source_choice(notes, ambiguous_answer_columns):
        message = (
            "ambiguous answer columns detected; notes should state source table/column choice "
            f"for {', '.join(ambiguous_answer_columns)}."
        )
        warnings.append(message)

    lowered_column_names = " ".join(str(column).lower() for column in columns)
    if (
        re.search(r"\b(what|which)\s+is\s+the\s+comment\b|\bwhat\s+is\s+the\s+comment\b", lowered_question)
        and "text" not in lowered_column_names
        and "comment" not in lowered_column_names
    ):
        warnings.append(
            "question appears to ask for the comment itself; verify the answer includes comment text, not only id/score."
        )

    numeric_cells = [
        value
        for row in normalized_rows
        if isinstance(row, list)
        for value in row
        if _is_numeric_like(value)
    ]
    if numeric_cells and any("e" in str(value).lower() for value in numeric_cells):
        warnings.append("scientific notation detected; prefer a plain decimal string with sufficient precision.")

    if errors:
        blocking_warnings = list(errors)

    ready_for_answer = not errors
    if errors:
        recommendation = "Fix validation errors before calling answer."
    elif warnings:
        recommendation = "Warnings are non-blocking for competition scoring; re-check them if step budget allows, otherwise call answer."
    else:
        recommendation = "Call answer with this table if ready_for_answer is true."
    return {
        "ok": not errors,
        "ready_for_answer": ready_for_answer,
        "errors": errors,
        "warnings": warnings,
        "blocking_warnings": blocking_warnings,
        "column_count": column_count,
        "row_count": row_count,
        "question": question,
        "recommendation": recommendation,
    }
