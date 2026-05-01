from __future__ import annotations

from pathlib import Path

from data_agent_baseline.agents.model import ModelMessage
from data_agent_baseline.agents.react import ReActAgent, ReActAgentConfig, parse_model_step
from data_agent_baseline.benchmark.schema import PublicTask, TaskAssets, TaskRecord
from data_agent_baseline.tools.registry import create_default_tool_registry


class RecordingModelAdapter:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[list[ModelMessage]] = []

    def complete(self, messages: list[ModelMessage]) -> str:
        self.calls.append(list(messages))
        if not self.responses:
            raise RuntimeError("No scripted responses remaining.")
        return self.responses.pop(0)


def _task(tmp_path: Path) -> PublicTask:
    context_dir = tmp_path / "context"
    context_dir.mkdir()
    return PublicTask(
        record=TaskRecord(
            task_id="task_test",
            difficulty="easy",
            question="Return a test answer.",
        ),
        assets=TaskAssets(task_dir=tmp_path, context_dir=context_dir),
    )


def test_parse_model_step_accepts_execute_python_object_input() -> None:
    raw_response = (
        "```json\n"
        '{"thought":"compute","action":"execute_python",'
        '"action_input":{"code":"import json\\nprint(\\"ok\\")"}}'
        "\n```"
    )

    step = parse_model_step(raw_response)

    assert step.action == "execute_python"
    assert step.action_input == {"code": 'import json\nprint("ok")'}


def test_agent_error_observation_guides_repair_and_does_not_replay_invalid_assistant(
    tmp_path: Path,
) -> None:
    invalid_response = (
        "```json\n"
        '{"thought":"bad","action":"execute_python","action_input":"print(\\"bad\\")"}'
        "\n```"
    )
    final_response = (
        "```json\n"
        '{"thought":"done","action":"answer",'
        '"action_input":{"columns":["result"],"rows":[["ok"]]}}'
        "\n```"
    )
    model = RecordingModelAdapter([invalid_response, final_response])
    agent = ReActAgent(
        model=model,
        tools=create_default_tool_registry(),
        config=ReActAgentConfig(max_steps=2),
    )

    result = agent.run(_task(tmp_path))

    assert result.succeeded
    assert result.steps[0].action == "__error__"
    assert result.steps[0].observation["error"] == "action_input must be a JSON object."
    assert "repair_instruction" in result.steps[0].observation

    second_call = model.calls[1]
    assistant_messages = [message.content for message in second_call if message.role == "assistant"]
    assert invalid_response not in assistant_messages
    assert any("previous response was invalid" in content for content in assistant_messages)


def test_tool_descriptions_render_json_examples() -> None:
    descriptions = create_default_tool_registry().describe_for_prompt()

    assert "action_input JSON example" in descriptions
    assert '"code": "import os\\nprint(sorted(os.listdir(\'.\')))"' in descriptions


def test_agent_v2_tool_flow_profiles_queries_validates_and_answers(tmp_path: Path) -> None:
    task = _task(tmp_path)
    csv_dir = task.context_dir / "csv"
    csv_dir.mkdir()
    (csv_dir / "value_scores.csv").write_text("name,score\nAda,10\nLin,20\n", encoding="utf-8")
    responses = [
        '{"thought":"profile first","action":"profile_context","action_input":{}}',
        (
            '{"thought":"query structured data","action":"execute_data_query",'
            '"action_input":{"sources":["csv/value_scores.csv"],'
            '"sql":"SELECT name FROM value_scores ORDER BY score DESC LIMIT 1","limit":10}}'
        ),
        (
            '{"thought":"validate candidate","action":"validate_answer",'
            '"action_input":{"columns":["name"],"rows":[["Lin"]],"notes":"Top score is unique."}}'
        ),
        (
            '{"thought":"submit","action":"answer",'
            '"action_input":{"columns":["name"],"rows":[["Lin"]]}}'
        ),
    ]
    model = RecordingModelAdapter(responses)
    agent = ReActAgent(
        model=model,
        tools=create_default_tool_registry(),
        config=ReActAgentConfig(max_steps=4),
    )

    result = agent.run(task)

    assert result.succeeded
    assert [step.action for step in result.steps] == [
        "profile_context",
        "execute_data_query",
        "validate_answer",
        "answer",
    ]
    assert result.answer is not None
    assert result.answer.rows == [["Lin"]]
