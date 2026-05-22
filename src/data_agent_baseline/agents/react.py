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
from data_agent_baseline.agents.runtime import AgentRunResult, AgentRuntimeState, StepRecord
from data_agent_baseline.benchmark.schema import AnswerTable, PublicTask
from data_agent_baseline.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class ReActAgentConfig:
    max_steps: int = 16


INVALID_RESPONSE_ASSISTANT_MESSAGE = (
    "The previous response was invalid and could not be executed. "
    "Follow the observation repair instructions exactly."
)
MAX_HISTORY_STEPS_IN_CONTEXT = 8


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


def _try_parse_python_output_as_table(output: str) -> AnswerTable | None:
    """execute_python の stdout から表形式データを厳格に推測して AnswerTable に変換する。

    誤検出（自由テキストの print 出力を CSV と誤認）を避けるため、
    以下の条件すべてを満たす場合のみ採用する:
      - 全行が同じ列数
      - 列名はすべて短い識別子 (英数字+_, 32文字以内, 空白なし)
      - データ行が 1-200 行の範囲
      - 自然文を示唆するキー文字 (':', '?', '"') を列名に含まない
    """
    import csv
    import io
    import re

    text = output.strip()
    if not text:
        return None
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) < 2 or len(lines) > 250:
        return None

    # ヘッダー行に明確な CSV ヘッダーらしい識別子があるかチェック
    header_line = lines[0].strip()
    if "," not in header_line and "\t" not in header_line:
        # 単一列ヘッダー扱いは曖昧なので、列名が短い識別子の場合のみ許す
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,31}", header_line):
            return None

    try:
        reader = csv.reader(io.StringIO(text))
        all_rows = list(reader)
    except Exception:
        return None
    if len(all_rows) < 2:
        return None

    columns = [str(c).strip() for c in all_rows[0]]
    if not columns or any(not c for c in columns):
        return None
    # 列名チェック: 短い識別子のみ許可
    ident_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-\(\) ]{0,63}$")
    for c in columns:
        if len(c) > 64 or not ident_re.match(c):
            return None
        if any(bad in c for bad in (":", "?", '"', "/", "\\", "[", "]", "{", "}")):
            return None

    rows: list[list[str]] = []
    for r in all_rows[1:]:
        if len(r) != len(columns):
            return None  # 列数が揃わない時点で正常な CSV ではない
        rows.append([str(v).strip() for v in r])
    if not rows or len(rows) > 200:
        return None

    return AnswerTable(columns=columns, rows=rows)


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
    return f"- step {step.step_index}: action={step.action}, tool={tool_name}, status={ok_flag}"


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

    @staticmethod
    def _salvage_answer(state: AgentRuntimeState) -> AnswerTable | None:
        """ステップ上限到達時、直近の validate_answer または execute_python 出力から回答を救済する。"""
        # 1) validate_answer の候補を優先
        for step in reversed(state.steps):
            if step.action != "validate_answer":
                continue
            action_input = step.action_input
            columns = action_input.get("columns")
            rows = action_input.get("rows")
            if (
                isinstance(columns, list)
                and columns
                and all(isinstance(c, str) for c in columns)
                and isinstance(rows, list)
                and rows
            ):
                return AnswerTable(columns=list(columns), rows=[list(r) for r in rows])
        # 2) execute_python の出力から表形式データを推測
        for step in reversed(state.steps):
            if step.action != "execute_python" or not step.ok:
                continue
            output = str(step.observation.get("content", {}).get("output", ""))
            if not output.strip():
                continue
            answer = _try_parse_python_output_as_table(output)
            if answer is not None:
                return answer
        # 3) 最終手段: 直近の成功した execute_python 出力から単一数値を抽出
        import re
        for step in reversed(state.steps):
            if step.action != "execute_python" or not step.ok:
                continue
            output = str(step.observation.get("content", {}).get("output", ""))
            if not output.strip():
                continue
            # "答え: 42" や "Result: 3.14" や最終行の数値
            lines = [l.strip() for l in output.strip().splitlines() if l.strip()]
            for line in reversed(lines):
                # "Answer: 42" / "Result: 3.14%" / "count: 7" パターン
                m = re.search(r"(?:answer|result|count|total|percentage|ratio)[:\s=]+([+-]?\d+\.?\d*)", line, re.IGNORECASE)
                if m:
                    return AnswerTable(columns=["answer"], rows=[[m.group(1)]])
            # 最終行が数値のみの場合
            last_line = lines[-1] if lines else ""
            m = re.fullmatch(r"([+-]?\d+\.?\d*%?)", last_line.strip())
            if m:
                val = m.group(1).rstrip("%")
                return AnswerTable(columns=["answer"], rows=[[val]])
        return None

    @staticmethod
    def _detect_tool_loop(state: AgentRuntimeState) -> str | None:
        """直近4ステップで同じツールが3回以上呼ばれたらループ検知メッセージを返す。"""
        recent = state.steps[-4:]
        if len(recent) < 3:
            return None
        actions = [s.action for s in recent if s.action != "__error__"]
        if len(actions) < 3:
            return None
        from collections import Counter
        counts = Counter(actions)
        for action, count in counts.items():
            if count >= 3:
                return action
        return None

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
        max_steps = self.config.max_steps
        for step_index in range(1, max_steps + 1):
            messages = self._build_messages(task, state)
            remaining = max_steps - step_index

            # --- ループ検知: 同じツール3回以上連続 ---
            looped_tool = self._detect_tool_loop(state)
            if looped_tool and remaining > 2:
                if looped_tool in ("retrieve_context", "read_doc", "read_json", "read_csv"):
                    messages.append(ModelMessage(
                        role="user",
                        content=(
                            f"LOOP DETECTED: You called `{looped_tool}` 3+ times recently. "
                            "Stop reading files repeatedly. Switch to `execute_python` to load "
                            "and process all data at once, then compute the answer. "
                            "If you already have enough data, call `validate_answer` now."
                        ),
                    ))
                elif looped_tool == "execute_python":
                    messages.append(ModelMessage(
                        role="user",
                        content=(
                            "LOOP DETECTED: You called `execute_python` 3+ times recently. "
                            "Your current approach is not working. Either:\n"
                            "1. Simplify your code and try a different parsing strategy, OR\n"
                            "2. Call `validate_answer` with your best available result now."
                        ),
                    ))
                elif looped_tool == "execute_context_sql":
                    messages.append(ModelMessage(
                        role="user",
                        content=(
                            "LOOP DETECTED: You called `execute_context_sql` 3+ times recently. "
                            "Switch to `execute_python` for more flexible data processing, "
                            "or call `validate_answer` with your best available result."
                        ),
                    ))
                else:
                    messages.append(ModelMessage(
                        role="user",
                        content=(
                            f"LOOP DETECTED: You called `{looped_tool}` 3+ times recently. "
                            "Change your approach. Try a different tool or submit your answer."
                        ),
                    ))

            if remaining <= 1:
                messages.append(ModelMessage(
                    role="user",
                    content=(
                        f"URGENT: This is step {step_index}/{max_steps}. "
                        "You have NO more steps after this. "
                        "Call `answer` NOW with your best available data. "
                        "If you have validated data, use that. "
                        "If you have any numeric result from execute_python, wrap it as a table. "
                        "If you have nothing, submit a single-cell table with your best guess."
                    ),
                ))
            elif remaining <= 2:
                messages.append(ModelMessage(
                    role="user",
                    content=(
                        f"WARNING: Step {step_index}/{max_steps} — only {remaining} steps left. "
                        "Call `validate_answer` NOW with whatever result you have, "
                        "then call `answer` on the next step."
                    ),
                ))
            elif remaining <= 4:
                messages.append(ModelMessage(
                    role="user",
                    content=(
                        f"NOTICE: Step {step_index}/{max_steps} — {remaining} steps remaining. "
                        "Start wrapping up. If you have computed any result, call `validate_answer` soon."
                    ),
                ))
            raw_response = self.model.complete(messages)
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
            # ステップ上限到達時、最後の validate_answer 候補があれば強制回答
            salvaged = self._salvage_answer(state)
            if salvaged is not None:
                state.answer = salvaged
                state.failure_reason = None
            else:
                state.failure_reason = "Agent did not submit an answer within max_steps."

        return AgentRunResult(
            task_id=task.task_id,
            answer=state.answer,
            steps=list(state.steps),
            failure_reason=state.failure_reason,
        )
