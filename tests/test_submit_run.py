from __future__ import annotations

import json
from pathlib import Path

from data_agent_baseline.agents.model import ModelMessage
from data_agent_baseline.config import (
    AgentConfig,
    AppConfig,
    DatasetConfig,
    RunConfig,
    apply_model_env_overrides,
)
from data_agent_baseline.run.runner import run_submit_benchmark
from data_agent_baseline.tools.registry import create_default_tool_registry


class SequentialModelAdapter:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[list[ModelMessage]] = []

    def complete(self, messages: list[ModelMessage]) -> str:
        self.calls.append(list(messages))
        if not self.responses:
            raise RuntimeError("No scripted responses remaining.")
        return self.responses.pop(0)


def _write_task(root_dir: Path, task_id: str) -> str:
    task_dir = root_dir / task_id
    context_dir = task_dir / "context"
    context_dir.mkdir(parents=True)
    payload = {
        "task_id": task_id,
        "difficulty": "easy",
        "question": f"Return answer for {task_id}.",
    }
    rendered = json.dumps(payload, ensure_ascii=False)
    (task_dir / "task.json").write_text(rendered)
    return rendered


def _answer_response(value: str) -> str:
    return json.dumps(
        {
            "thought": "submit answer",
            "action": "answer",
            "action_input": {
                "columns": ["result"],
                "rows": [[value]],
            },
        }
    )


def _validate_response(value: str) -> str:
    return json.dumps(
        {
            "thought": "validate answer",
            "action": "validate_answer",
            "action_input": {
                "columns": ["result"],
                "rows": [[value]],
                "notes": "No ambiguity; computed directly.",
            },
        }
    )


def _invalid_response() -> str:
    return '{"thought":"bad","action":"answer","action_input":"not-object"}'


def _memory_response() -> str:
    return json.dumps(
        {
            "facts": ["Use the task data."],
            "candidate_columns": [],
            "relationships": [],
            "risks": [],
            "validation_checks": [],
        }
    )


def _config(input_dir: Path, *, max_steps: int = 2) -> AppConfig:
    return AppConfig(
        dataset=DatasetConfig(root_path=input_dir),
        agent=AgentConfig(
            model="local-model",
            api_base="https://example.invalid/v1",
            api_key="local-key",
            max_steps=max_steps,
            temperature=0.0,
        ),
        run=RunConfig(max_workers=1, task_timeout_seconds=0),
    )


def test_model_env_overrides_take_precedence(monkeypatch) -> None:
    config = _config(Path("/tmp/input"))
    monkeypatch.setenv("MODEL_API_URL", "https://model.internal/v1")
    monkeypatch.setenv("MODEL_API_KEY", "eval-key")
    monkeypatch.setenv("MODEL_NAME", "qwen3.5-35b-a3b")

    updated = apply_model_env_overrides(config)

    assert updated.agent.api_base == "https://model.internal/v1"
    assert updated.agent.api_key == "eval-key"
    assert updated.agent.model == "qwen3.5-35b-a3b"
    assert updated.agent.max_steps == config.agent.max_steps
    assert updated.agent.request_timeout_seconds == config.agent.request_timeout_seconds
    assert updated.agent.max_retries == config.agent.max_retries


def test_submit_run_writes_prediction_to_output_and_trace_to_logs(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    logs_dir = tmp_path / "logs"
    original_task_json = _write_task(input_dir, "task_1")
    model = SequentialModelAdapter([_memory_response(), _validate_response("ok"), _answer_response("ok")])

    artifacts = run_submit_benchmark(
        config=_config(input_dir),
        output_dir=output_dir,
        logs_dir=logs_dir,
        model=model,
        tools=create_default_tool_registry(),
    )

    prediction_path = output_dir / "task_1" / "prediction.csv"
    trace_path = logs_dir / "task_1" / "trace.json"
    assert len(artifacts) == 1
    assert artifacts[0].succeeded
    assert prediction_path.read_text() == "result\nok\n"
    assert trace_path.exists()
    assert (input_dir / "task_1" / "task.json").read_text() == original_task_json


def test_submit_run_continues_after_task_failure(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    logs_dir = tmp_path / "logs"
    _write_task(input_dir, "task_1")
    _write_task(input_dir, "task_2")
    model = SequentialModelAdapter(
        [
            _memory_response(),
            _invalid_response(),
            _invalid_response(),
            _memory_response(),
            _validate_response("second-ok"),
            _answer_response("second-ok"),
        ]
    )

    artifacts = run_submit_benchmark(
        config=_config(input_dir),
        output_dir=output_dir,
        logs_dir=logs_dir,
        model=model,
        tools=create_default_tool_registry(),
    )

    assert [artifact.task_id for artifact in artifacts] == ["task_1", "task_2"]
    assert not artifacts[0].succeeded
    assert artifacts[1].succeeded
    assert not (output_dir / "task_1" / "prediction.csv").exists()
    assert (output_dir / "task_2" / "prediction.csv").read_text() == "result\nsecond-ok\n"
    assert (logs_dir / "task_1" / "trace.json").exists()
    assert (logs_dir / "task_2" / "trace.json").exists()
