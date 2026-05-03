from __future__ import annotations

import json
import re
from dataclasses import dataclass

from data_agent_baseline.agents.model import ModelAdapter, ModelMessage, ModelStep
from data_agent_baseline.agents.prompt import (
    REACT_SYSTEM_PROMPT,
    TASK_MEMORY_SYSTEM_PROMPT,
    build_observation_prompt,
    build_system_prompt,
    build_task_memory_message,
    build_task_memory_prompt,
    build_task_prompt,
)
from data_agent_baseline.agents.runtime import AgentRunResult, AgentRuntimeState, StepRecord
from data_agent_baseline.benchmark.schema import PublicTask
from data_agent_baseline.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class ReActAgentConfig:
    max_steps: int = 18


INVALID_RESPONSE_ASSISTANT_MESSAGE = (
    "The previous response was invalid and could not be executed. "
    "Follow the observation repair instructions exactly."
)
PROFILE_CONTEXT_RAW_RESPONSE = (
    '{"thought":"Profile context before reasoning.",'
    '"action":"profile_context","action_input":{}}'
)


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


def _normalize_string_list(value: object, *, limit: int = 8) -> list[str]:
    """memory JSON の list らしい値を短い文字列 list に正規化する。"""

    if not isinstance(value, list):
        return []
    items = []
    for item in value[:limit]:
        text = str(item).strip()
        if text:
            items.append(text)
    return items


def _format_columns(columns: object) -> str:
    """profile の column list を memory 用に短く整形する。"""

    if not isinstance(columns, list):
        return ""
    return ", ".join(str(column) for column in columns[:8])


def _fallback_task_memory(profile_content: dict[str, object]) -> dict[str, object]:
    """LLM memory 生成に失敗した場合の機械的 task memory を作る。"""

    candidate_columns = []
    for item in profile_content.get("question_column_candidates", []):
        if not isinstance(item, dict):
            continue
        term = str(item.get("term", "term"))
        columns = _format_columns(item.get("columns"))
        if columns:
            candidate_columns.append(f"{term}: {columns}")

    relationships = []
    for item in profile_content.get("relationship_candidates", []):
        if not isinstance(item, dict):
            continue
        relationships.append(
            f"{item.get('source')}.{item.get('column')} -> "
            f"{item.get('target_source')}.{item.get('target_column')}"
        )

    risks = []
    for item in profile_content.get("ambiguous_columns", []):
        if not isinstance(item, dict):
            continue
        columns = _format_columns(item.get("columns"))
        if columns:
            risks.append(f"Ambiguous column {item.get('name')}: {columns}")
    for item in profile_content.get("cardinality_hints", []):
        if not isinstance(item, dict) or not item.get("risk"):
            continue
        risks.append(
            f"{item.get('qualified_name')} has duplicates "
            f"({item.get('duplicate_count')} duplicate values); avoid collapsing one-to-many joins."
        )

    knowledge = profile_content.get("knowledge")
    facts = []
    if isinstance(knowledge, dict) and knowledge.get("path"):
        facts.append(f"{knowledge.get('path')} is available in profile_context and should guide definitions.")

    return {
        "facts": facts[:6],
        "candidate_columns": candidate_columns[:8],
        "relationships": relationships[:8],
        "risks": risks[:8],
        "deterministic_ambiguity_checklist": _normalize_ambiguity_checklist(profile_content),
        "validation_checks": [
            "Explain ambiguous column choices and tie checks in validate_answer notes."
        ],
        "source": "fallback_from_profile_context",
    }


def _normalize_ambiguity_checklist(profile_content: dict[str, object]) -> list[dict[str, object]]:
    """profile_context の機械的 ambiguity checklist を memory 用に正規化する。"""

    checklist = profile_content.get("ambiguity_checklist")
    if not isinstance(checklist, list):
        return []
    normalized: list[dict[str, object]] = []
    for item in checklist[:8]:
        if not isinstance(item, dict):
            continue
        term = str(item.get("term", "")).strip()
        if not term:
            continue
        raw_columns = item.get("candidate_columns", [])
        columns = [str(column) for column in raw_columns[:8]] if isinstance(raw_columns, list) else []
        normalized.append(
            {
                "term": term,
                "candidate_columns": columns,
                "required_action": str(item.get("required_action", "")).strip(),
                "notes_requirement": str(item.get("notes_requirement", "")).strip(),
                "blocking_if_unresolved": bool(item.get("blocking_if_unresolved")),
            }
        )
    return normalized


def _normalize_task_memory(payload: dict[str, object]) -> dict[str, object]:
    """LLM が返した task memory を固定 schema に寄せる。"""

    return {
        "facts": _normalize_string_list(payload.get("facts")),
        "candidate_columns": _normalize_string_list(payload.get("candidate_columns")),
        "relationships": _normalize_string_list(payload.get("relationships")),
        "risks": _normalize_string_list(payload.get("risks")),
        "deterministic_ambiguity_checklist": [],
        "validation_checks": _normalize_string_list(payload.get("validation_checks")),
        "source": str(payload.get("source") or "llm_task_memory"),
    }


def _merge_deterministic_memory(
    memory: dict[str, object],
    profile_content: dict[str, object],
) -> dict[str, object]:
    """LLM memory に機械的 checklist を必ず付与する。"""

    merged = dict(memory)
    checklist = _normalize_ambiguity_checklist(profile_content)
    merged["deterministic_ambiguity_checklist"] = checklist
    if checklist:
        validation_checks = list(merged.get("validation_checks", []))
        validation_checks.append(
            "For each deterministic ambiguity checklist item that affects the answer, "
            "compare candidates and mention selected and rejected alternatives in validate_answer notes."
        )
        merged["validation_checks"] = validation_checks[:10]
    return merged


def _build_answer_guard_observation(blocking_warnings: list[str]) -> dict[str, object]:
    """未解消 validation warning 後の answer を止める observation を作る。"""

    repair_instruction = (
        "Do not call answer yet. Re-check the risky point, run another tool if needed, "
        "then call validate_answer again with notes that resolve these blocking warnings."
    )
    if not blocking_warnings:
        blocking_warnings = ["validate_answer must be called and return ready_for_answer=true before answer."]
        repair_instruction = (
            "Do not call answer yet. First call validate_answer with the candidate table and "
            "notes that compare any ambiguous candidates. Only call answer after ready_for_answer=true."
        )
    return {
        "ok": False,
        "tool": "answer_guard",
        "content": {
            "ready_for_answer": False,
            "blocking_warnings": blocking_warnings,
            "repair_instruction": repair_instruction,
        },
    }


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
        if state.task_memory is not None:
            messages.append(
                ModelMessage(role="user", content=build_task_memory_message(state.task_memory))
            )
        for step in state.steps:
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

    def _profile_task(self, task: PublicTask) -> StepRecord:
        """ReAct 前に profile_context を必ず実行し、trace 用 step を返す。"""

        tool_result = self.tools.execute(task, "profile_context", {})
        observation = {
            "ok": tool_result.ok,
            "tool": "profile_context",
            "content": tool_result.content,
        }
        return StepRecord(
            step_index=1,
            thought="Profile context before reasoning.",
            action="profile_context",
            action_input={},
            raw_response=PROFILE_CONTEXT_RAW_RESPONSE,
            observation=observation,
            ok=tool_result.ok,
        )

    def _build_task_memory(self, task: PublicTask, profile_content: dict[str, object]) -> dict[str, object]:
        """profile_context から LLM task memory を作る。失敗時は fallback を返す。"""

        try:
            raw_response = self.model.complete(
                [
                    ModelMessage(role="system", content=TASK_MEMORY_SYSTEM_PROMPT),
                    ModelMessage(
                        role="user",
                        content=build_task_memory_prompt(task, profile_content),
                    ),
                ]
            )
            payload = _load_single_json_object(_strip_json_fence(raw_response))
            return _merge_deterministic_memory(_normalize_task_memory(payload), profile_content)
        except Exception as exc:  # noqa: BLE001
            memory = _fallback_task_memory(profile_content)
            memory["memory_error"] = str(exc)
            return memory

    def run(self, task: PublicTask) -> AgentRunResult:
        state = AgentRuntimeState()
        try:
            profile_step = self._profile_task(task)
            state.steps.append(profile_step)
            profile_content = profile_step.observation.get("content", {})
            if isinstance(profile_content, dict):
                state.task_memory = self._build_task_memory(task, profile_content)
            else:
                state.task_memory = _fallback_task_memory({})
        except Exception as exc:
            observation = _build_error_observation(exc, PROFILE_CONTEXT_RAW_RESPONSE)
            state.steps.append(
                StepRecord(
                    step_index=1,
                    thought="",
                    action="__error__",
                    action_input={},
                    raw_response=PROFILE_CONTEXT_RAW_RESPONSE,
                    observation=observation,
                    ok=False,
                )
            )
            state.task_memory = _fallback_task_memory({})

        for step_index in range(2, self.config.max_steps + 2):
            raw_response = self.model.complete(self._build_messages(task, state))
            try:
                model_step = parse_model_step(raw_response)
                if model_step.action == "answer" and state.last_validation_ready is not True:
                    blocking_warnings = list(state.last_validation_warnings)
                    observation = _build_answer_guard_observation(blocking_warnings)
                    state.steps.append(
                        StepRecord(
                            step_index=step_index,
                            thought=model_step.thought,
                            action=model_step.action,
                            action_input=model_step.action_input,
                            raw_response=raw_response,
                            observation=observation,
                            ok=False,
                        )
                    )
                    continue

                tool_result = self.tools.execute(task, model_step.action, model_step.action_input)
                observation = {
                    "ok": tool_result.ok,
                    "tool": model_step.action,
                    "content": tool_result.content,
                }
                step_record = StepRecord(
                    step_index=step_index,
                    thought=model_step.thought,
                    action=model_step.action,
                    action_input=model_step.action_input,
                    raw_response=raw_response,
                    observation=observation,
                    ok=tool_result.ok,
                )
                state.steps.append(step_record)
                if model_step.action == "validate_answer":
                    content = tool_result.content
                    state.last_validation_ready = bool(content.get("ready_for_answer"))
                    raw_blocking_warnings = content.get("blocking_warnings", [])
                    state.last_validation_warnings = (
                        [str(item) for item in raw_blocking_warnings]
                        if isinstance(raw_blocking_warnings, list)
                        else []
                    )
                if tool_result.is_terminal:
                    state.answer = tool_result.answer
                    break
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

        if state.answer is None and state.failure_reason is None:
            state.failure_reason = "Agent did not submit an answer within max_steps."

        return AgentRunResult(
            task_id=task.task_id,
            answer=state.answer,
            steps=list(state.steps),
            failure_reason=state.failure_reason,
            task_memory=state.task_memory,
        )
