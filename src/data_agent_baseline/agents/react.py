from __future__ import annotations

import json
import re
from dataclasses import dataclass

from data_agent_baseline.agents.model import ModelAdapter, ModelMessage, ModelStep
from data_agent_baseline.agents.prompt import (
    REACT_SYSTEM_PROMPT,
    build_observation_prompt,
    build_system_prompt,
    build_task_prompt,
)
from data_agent_baseline.agents.answer_repair import (
    REPAIR_ACTION,
    build_answer_repair_observation,
    project_helper_columns_from_answer,
)
from data_agent_baseline.agents.runtime import AgentRunResult, AgentRuntimeState, StepRecord
from data_agent_baseline.benchmark.schema import PublicTask
from data_agent_baseline.tools.registry import ToolExecutionResult, ToolRegistry


@dataclass(frozen=True, slots=True)
class ReActAgentConfig:
    max_steps: int = 16


INVALID_RESPONSE_ASSISTANT_MESSAGE = (
    "The previous response was invalid and could not be executed. "
    "Follow the observation repair instructions exactly."
)
MAX_HISTORY_STEPS_IN_CONTEXT = 8
SUMMARY_PREVIEW_ROWS = 3
NEAR_STEP_LIMIT_ACTION = "near_step_limit_submit"
QUERY_ACTIONS = {"execute_context_sql", "execute_data_query"}


def _strip_json_fence(raw_response: str) -> str:
    text = raw_response.strip()
    fence_match = re.search(r"```json\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if fence_match is not None:
        return fence_match.group(1).strip()
    generic_fence_match = re.search(r"```\s*(.*?)\s*```", text, flags=re.DOTALL)
    if generic_fence_match is not None:
        return generic_fence_match.group(1).strip()
    return text


def _load_single_json_object(text: str) -> dict[str, object]:
    payload, end = json.JSONDecoder().raw_decode(text)
    remainder = text[end:].strip()
    if remainder:
        cleaned_remainder = re.sub(r"(?:\\[nrt])+", "", remainder).strip()
        if cleaned_remainder:
            raise ValueError("Model response must contain only one JSON object.")
    if not isinstance(payload, dict):
        raise ValueError("Model response must be a JSON object.")
    return payload


def parse_model_step(raw_response: str) -> ModelStep:
    normalized = _strip_json_fence(raw_response)
    payload = _load_single_json_object(normalized)

    thought = payload.get("thought", "")
    action = payload.get("action")
    action_input = payload.get("action_input", {})
    if not isinstance(thought, str):
        raise ValueError("thought must be a string.")
    if not isinstance(action, str) or not action:
        raise ValueError("action must be a non-empty string.")
    if not isinstance(action_input, dict):
        raise ValueError("action_input must be a JSON object.")

    return ModelStep(
        thought=thought,
        action=action,
        action_input=action_input,
        raw_response=raw_response,
    )


def _build_error_observation(exc: Exception, raw_response: str) -> dict[str, object]:
    return {
        "ok": False,
        "error": str(exc),
        "error_type": type(exc).__name__,
        "invalid_response_preview": raw_response[:1000],
        "repair_instruction": (
            "The previous response was invalid JSON or did not match the action protocol. "
            "Return only one minimal JSON object next. Do not repeat the invalid response. "
            "`action_input` must be a JSON object. For execute_python, use "
            '`action_input`: {"code": "..."} and escape newlines inside the JSON string.'
        ),
        "required_format": {
            "thought": "short reason",
            "action": "one registered tool name",
            "action_input": {},
        },
        "execute_python_example": {
            "thought": "Run Python to inspect the data.",
            "action": "execute_python",
            "action_input": {
                "code": "import json\nprint('ok')",
            },
        },
    }


def _build_tool_error_observation(model_step: ModelStep, exc: Exception) -> dict[str, object]:
    return {
        "ok": False,
        "tool": model_step.action,
        "failed_action": model_step.action,
        "failed_action_input": model_step.action_input,
        "error": str(exc),
        "error_type": type(exc).__name__,
        "repair_instruction": (
            "The tool call failed. Do not repeat the same tool call with the same input. "
            "Fix the path/schema/query using profile_context, list_context, read_csv, read_json, "
            "or inspect_sqlite_schema, or switch to execute_python/retrieve_context when that is simpler. "
            "If a SQL query keeps failing, first run a smaller query that inspects columns or distinct values."
        ),
        "fallback_examples": [
            {"action": "list_context", "action_input": {"max_depth": 4}},
            {"action": "read_csv", "action_input": {"path": "csv/example.csv", "max_rows": 10}},
            {
                "action": "execute_python",
                "action_input": {"code": "import os\nprint(sorted(os.listdir('.')))"},
            },
        ],
    }


def _latest_question_contract(steps: list[StepRecord]) -> dict[str, object] | None:
    """直近の question_contract を返す。"""

    for step in reversed(steps):
        if step.action != "question_contract":
            continue
        content = step.observation.get("content")
        if not isinstance(content, dict):
            continue
        contract = content.get("contract")
        if isinstance(contract, dict):
            return contract
    return None


def _latest_validation_candidate(steps: list[StepRecord]) -> dict[str, object] | None:
    """直近の validate_answer 入力から提出候補を返す。"""

    for step in reversed(steps):
        if step.action != "validate_answer" or not step.ok:
            continue
        columns = step.action_input.get("columns")
        rows = step.action_input.get("rows")
        if isinstance(columns, list) and columns and isinstance(rows, list):
            return {"columns": columns, "rows": rows}
    return None


def _latest_query_candidate(steps: list[StepRecord]) -> dict[str, object] | None:
    """直近の成功 query 結果から提出候補を返す。"""

    for step in reversed(steps):
        if step.action not in QUERY_ACTIONS or not step.ok:
            continue
        content = step.observation.get("content")
        if not isinstance(content, dict):
            continue
        columns = content.get("columns")
        rows = content.get("rows")
        if isinstance(columns, list) and columns and isinstance(rows, list) and rows:
            return {"columns": columns, "rows": rows[:1000]}
    return None


def _build_near_step_limit_observation(
    *,
    step_index: int,
    max_steps: int,
    candidate: dict[str, object],
) -> dict[str, object]:
    """残り step が少ないときに提出優先を促す observation を作る。"""

    return {
        "ok": False,
        "tool": NEAR_STEP_LIMIT_ACTION,
        "content": {
            "reason": "The task is near the max_steps limit. Missing submissions score zero.",
            "remaining_model_steps_after_this": max_steps - step_index,
            "candidate_columns": candidate.get("columns"),
            "candidate_row_count": len(candidate.get("rows", []))
            if isinstance(candidate.get("rows"), list)
            else None,
            "candidate_preview_rows": candidate.get("rows", [])[:SUMMARY_PREVIEW_ROWS]
            if isinstance(candidate.get("rows"), list)
            else [],
            "repair_instruction": (
                "Stop exploring. Use the best candidate table already found. "
                "If the candidate has not been validated, call validate_answer now with these columns and rows. "
                "If it has been validated, call answer now. Remove only clearly helper columns when the requested "
                "output attributes are obvious; otherwise submit the candidate rather than leaving the task unanswered."
            ),
        },
    }


def _copy_action_input_with_contract(
    action: str,
    action_input: dict[str, object],
    steps: list[StepRecord],
) -> dict[str, object]:
    """validate_answer に直近 question_contract を内部注入する。"""

    if action != "validate_answer":
        return action_input
    contract = _latest_question_contract(steps)
    if contract is None:
        return action_input
    augmented = dict(action_input)
    augmented["_question_contract"] = contract
    return augmented


def _short_json(value: object, *, max_chars: int = 500) -> str:
    """summary 用に JSON 値を短く整形する。"""

    rendered = json.dumps(value, ensure_ascii=False, default=str)
    if len(rendered) <= max_chars:
        return rendered
    return rendered[:max_chars] + "... [truncated]"


def _content_summary(step: StepRecord) -> str:
    """古い step の observation から再利用すべき事実を短く抽出する。"""

    content = step.observation.get("content")
    if not isinstance(content, dict):
        return ""

    if step.action == "question_contract":
        contract = content.get("contract")
        if isinstance(contract, dict):
            focused = {
                key: contract.get(key)
                for key in (
                    "requested_output_attributes",
                    "metric_or_formula",
                    "grain",
                    "grouping",
                    "ranking",
                    "tie_rule",
                    "join_keys",
                    "helper_attributes",
                    "ambiguities_checked",
                )
            }
            warnings = content.get("warnings")
            return f", contract={_short_json(focused)}, warnings={_short_json(warnings, max_chars=240)}"

    if step.action in {"execute_context_sql", "execute_data_query"}:
        focused = {
            "columns": content.get("columns"),
            "row_count": content.get("row_count"),
            "truncated": content.get("truncated"),
            "preview_rows": content.get("rows", [])[:SUMMARY_PREVIEW_ROWS]
            if isinstance(content.get("rows"), list)
            else [],
        }
        return f", result={_short_json(focused)}"

    if step.action in {"read_csv", "read_json", "inspect_sqlite_schema"}:
        focused = {
            key: content.get(key)
            for key in ("path", "columns", "row_count", "top_level_type", "top_level_keys", "tables")
            if key in content
        }
        if focused:
            return f", profile={_short_json(focused, max_chars=500)}"

    if step.action == "validate_answer":
        focused = {
            "column_count": content.get("column_count"),
            "row_count": content.get("row_count"),
            "warnings": content.get("warnings"),
        }
        return f", validation={_short_json(focused, max_chars=650)}"

    return ""


def _execute_tool_step(
    *,
    tools: ToolRegistry,
    task: PublicTask,
    model_step: ModelStep,
    step_index: int,
    action_input: dict[str, object],
) -> tuple[StepRecord, ToolExecutionResult]:
    """tool を実行し、StepRecord と ToolExecutionResult を返す。"""

    tool_result = tools.execute(task, model_step.action, action_input)
    observation = {
        "ok": tool_result.ok,
        "tool": model_step.action,
        "content": tool_result.content,
    }
    step_record = StepRecord(
        step_index=step_index,
        thought=model_step.thought,
        action=model_step.action,
        action_input=action_input,
        raw_response=model_step.raw_response,
        observation=observation,
        ok=tool_result.ok,
    )
    return step_record, tool_result


def _build_auto_answer_step(
    *,
    tools: ToolRegistry,
    task: PublicTask,
    model_step: ModelStep,
    step_index: int,
    action_input: dict[str, object],
) -> tuple[StepRecord, ToolExecutionResult]:
    """step 上限付近で候補 table を answer として自動提出する。"""

    tool_result = tools.execute(task, "answer", action_input)
    observation = {
        "ok": tool_result.ok,
        "tool": "answer",
        "content": tool_result.content
        | {
            "auto_submitted": True,
            "auto_submit_reason": "near max_steps; submitting the best available candidate to avoid a missing prediction",
        },
    }
    return (
        StepRecord(
            step_index=step_index,
            thought=model_step.thought,
            action="answer",
            action_input=action_input,
            raw_response=model_step.raw_response,
            observation=observation,
            ok=tool_result.ok,
        ),
        tool_result,
    )


def _build_step_summary(step: StepRecord) -> str:
    if step.action == "__error__":
        failed_action = step.observation.get("failed_action")
        if failed_action:
            return (
                f"- step {step.step_index}: tool error, action={failed_action}, "
                f"error={step.observation.get('error_type', 'unknown')}"
            )
        return (
            f"- step {step.step_index}: invalid model response, "
            f"error={step.observation.get('error_type', 'unknown')}"
        )
    tool_name = str(step.observation.get("tool", step.action))
    ok_flag = "ok" if step.ok else "fail"
    return (
        f"- step {step.step_index}: action={step.action}, tool={tool_name}, "
        f"status={ok_flag}{_content_summary(step)}"
    )


class ReActAgent:
    def __init__(
        self,
        *,
        model: ModelAdapter,
        tools: ToolRegistry,
        config: ReActAgentConfig | None = None,
        system_prompt: str | None = None,
    ) -> None:
        self.model = model
        self.tools = tools
        self.config = config or ReActAgentConfig()
        self.system_prompt = system_prompt or REACT_SYSTEM_PROMPT

    def _build_messages(self, task: PublicTask, state: AgentRuntimeState) -> list[ModelMessage]:
        system_content = build_system_prompt(
            self.tools.describe_for_prompt(),
            system_prompt=self.system_prompt,
        )
        messages = [ModelMessage(role="system", content=system_content)]
        messages.append(ModelMessage(role="user", content=build_task_prompt(task)))
        prior_steps = state.steps[:-MAX_HISTORY_STEPS_IN_CONTEXT]
        if prior_steps:
            summary_lines = [
                "Earlier steps summary. Reuse these facts instead of repeating the same tool calls unless needed:",
                *[_build_step_summary(step) for step in prior_steps],
            ]
            messages.append(ModelMessage(role="user", content="\n".join(summary_lines)))

        for step in state.steps[-MAX_HISTORY_STEPS_IN_CONTEXT:]:
            assistant_content = (
                INVALID_RESPONSE_ASSISTANT_MESSAGE
                if step.action == "__error__"
                else step.raw_response
            )
            messages.append(ModelMessage(role="assistant", content=assistant_content))
            messages.append(
                ModelMessage(role="user", content=build_observation_prompt(step.observation))
            )
        return messages

    def run(self, task: PublicTask) -> AgentRunResult:
        state = AgentRuntimeState()
        for step_index in range(1, self.config.max_steps + 1):
            raw_response = self.model.complete(self._build_messages(task, state))
            try:
                model_step = parse_model_step(raw_response)
            except Exception as exc:
                observation = _build_error_observation(exc, raw_response)
                state.steps.append(
                    StepRecord(
                        step_index=step_index,
                        thought="",
                        action="__error__",
                        action_input={},
                        raw_response=raw_response,
                        observation=observation,
                        ok=False,
                    )
                )
                continue

            try:
                if step_index >= self.config.max_steps - 1 and model_step.action not in {
                    "answer",
                    "validate_answer",
                }:
                    validated_candidate = _latest_validation_candidate(state.steps)
                    if validated_candidate is not None:
                        step_record, tool_result = _build_auto_answer_step(
                            tools=self.tools,
                            task=task,
                            model_step=model_step,
                            step_index=step_index,
                            action_input=validated_candidate,
                        )
                        state.steps.append(step_record)
                        if tool_result.is_terminal:
                            state.answer = tool_result.answer
                            break

                    query_candidate = _latest_query_candidate(state.steps)
                    if query_candidate is not None:
                        if step_index >= self.config.max_steps:
                            step_record, tool_result = _build_auto_answer_step(
                                tools=self.tools,
                                task=task,
                                model_step=model_step,
                                step_index=step_index,
                                action_input=query_candidate,
                            )
                            state.steps.append(step_record)
                            if tool_result.is_terminal:
                                state.answer = tool_result.answer
                                break
                        state.steps.append(
                            StepRecord(
                                step_index=step_index,
                                thought=model_step.thought,
                                action=NEAR_STEP_LIMIT_ACTION,
                                action_input=model_step.action_input,
                                raw_response=raw_response,
                                observation=_build_near_step_limit_observation(
                                    step_index=step_index,
                                    max_steps=self.config.max_steps,
                                    candidate=query_candidate,
                                ),
                                ok=False,
                            )
                        )
                        continue

                if model_step.action == "answer":
                    projected_answer = project_helper_columns_from_answer(
                        steps=state.steps,
                        action_input=model_step.action_input,
                    )
                    if projected_answer is not None:
                        step_record, tool_result = _build_auto_answer_step(
                            tools=self.tools,
                            task=task,
                            model_step=model_step,
                            step_index=step_index,
                            action_input=projected_answer,
                        )
                        step_record.observation["content"]["auto_submit_reason"] = (
                            "projected helper columns after validate_answer warning"
                        )
                        state.steps.append(step_record)
                        if tool_result.is_terminal:
                            state.answer = tool_result.answer
                            break

                    repair_observation = build_answer_repair_observation(
                        steps=state.steps,
                        action_input=model_step.action_input,
                        step_index=step_index,
                        max_steps=self.config.max_steps,
                    )
                    if repair_observation is not None:
                        state.steps.append(
                            StepRecord(
                                step_index=step_index,
                                thought=model_step.thought,
                                action=REPAIR_ACTION,
                                action_input=model_step.action_input,
                                raw_response=raw_response,
                                observation=repair_observation,
                                ok=False,
                            )
                        )
                        continue

                action_input = _copy_action_input_with_contract(
                    model_step.action,
                    model_step.action_input,
                    state.steps,
                )
                step_record, tool_result = _execute_tool_step(
                    tools=self.tools,
                    task=task,
                    model_step=model_step,
                    step_index=step_index,
                    action_input=action_input,
                )
                state.steps.append(step_record)
                if (
                    model_step.action == "validate_answer"
                    and step_index >= self.config.max_steps
                    and tool_result.ok
                ):
                    auto_step, auto_result = _build_auto_answer_step(
                        tools=self.tools,
                        task=task,
                        model_step=model_step,
                        step_index=step_index + 1,
                        action_input={
                            "columns": action_input.get("columns"),
                            "rows": action_input.get("rows"),
                        },
                    )
                    state.steps.append(auto_step)
                    if auto_result.is_terminal:
                        state.answer = auto_result.answer
                        break
                if tool_result.is_terminal:
                    state.answer = tool_result.answer
                    break
            except Exception as exc:
                observation = _build_tool_error_observation(model_step, exc)
                state.steps.append(
                    StepRecord(
                        step_index=step_index,
                        thought=model_step.thought,
                        action="__error__",
                        action_input=model_step.action_input,
                        raw_response=raw_response,
                        observation=observation,
                        ok=False,
                    )
                )

        if state.answer is None and state.failure_reason is None:
            state.failure_reason = "Agent did not submit an answer within max_steps."

        return AgentRunResult(
            task_id=task.task_id,
            answer=state.answer,
            steps=list(state.steps),
            failure_reason=state.failure_reason,
        )
