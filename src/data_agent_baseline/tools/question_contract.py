"""質問解釈を構造化して trace に残すための補助ツール。"""

from __future__ import annotations

from typing import Any

from data_agent_baseline.benchmark.schema import PublicTask

REQUIRED_CONTRACT_FIELDS = (
    "requested_output_attributes",
    "filters",
    "metric_or_formula",
    "grain",
    "grouping",
    "ranking",
    "tie_rule",
    "join_keys",
    "knowledge_rules_used",
    "helper_attributes",
    "ambiguities_checked",
)

LIST_FIELDS = {
    "requested_output_attributes",
    "filters",
    "join_keys",
    "knowledge_rules_used",
    "helper_attributes",
    "ambiguities_checked",
}


def _is_blank(value: Any) -> bool:
    """契約項目が未記入相当かどうかを返す。"""

    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip() or value.strip().lower() in {"unknown", "n/a", "none"}
    if isinstance(value, list):
        return not value
    if isinstance(value, dict):
        return not value
    return False


def _normalize_list(value: Any) -> list[str]:
    """文字列または配列を trace 用の文字列配列へ正規化する。"""

    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def build_question_contract(task: PublicTask, action_input: dict[str, Any]) -> dict[str, Any]:
    """モデルが宣言した質問解釈を検査し、警告付きで返す。"""

    warnings: list[str] = []
    contract: dict[str, Any] = {}
    for field in REQUIRED_CONTRACT_FIELDS:
        value = action_input.get(field)
        if field in LIST_FIELDS:
            value = _normalize_list(value)
        elif value is None:
            value = ""
        contract[field] = value
        if _is_blank(value):
            warnings.append(f"question_contract.{field} is empty; state the intended interpretation before querying.")

    if not contract["requested_output_attributes"]:
        warnings.append(
            "requested_output_attributes must name only the final columns to output, not helper ranking/filter values."
        )
    if not contract["helper_attributes"]:
        warnings.append(
            "helper_attributes should list values used only for filtering, joining, ranking, formula calculation, or tie checks."
        )
    if not contract["knowledge_rules_used"]:
        warnings.append(
            "If knowledge.md has no applicable rule, explicitly write 'none applicable' in knowledge_rules_used."
        )

    return {
        "question": task.question,
        "contract": contract,
        "warnings": warnings,
        "recommendation": (
            "Use this contract as the source of truth for SQL/Python. If a later query contradicts the contract, "
            "revise the contract or the query before validating the answer."
        ),
    }
