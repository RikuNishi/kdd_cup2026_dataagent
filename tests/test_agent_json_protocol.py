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


def _memory_response() -> str:
    return (
        '{"facts":["Use the structured CSV data."],'
        '"candidate_columns":["name and score are available."],'
        '"relationships":[],"risks":[],"validation_checks":["Validate the final table."]}'
    )


def _validate_response(value: str = "ok") -> str:
    return (
        '{"thought":"validate","action":"validate_answer",'
        f'"action_input":{{"columns":["result"],"rows":[["{value}"]],"notes":"No ambiguity; computed directly."}}}}'
    )


def _answer_response(value: str = "ok") -> str:
    return (
        '{"thought":"submit","action":"answer",'
        f'"action_input":{{"columns":["result"],"rows":[["{value}"]]}}}}'
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
    model = RecordingModelAdapter([_memory_response(), invalid_response, _validate_response(), _answer_response()])
    agent = ReActAgent(
        model=model,
        tools=create_default_tool_registry(),
        config=ReActAgentConfig(max_steps=3),
    )

    result = agent.run(_task(tmp_path))

    assert result.succeeded
    assert result.steps[0].action == "profile_context"
    assert result.steps[1].action == "__error__"
    assert result.steps[1].observation["error"] == "action_input must be a JSON object."
    assert "repair_instruction" in result.steps[1].observation

    repaired_call = model.calls[2]
    assistant_messages = [message.content for message in repaired_call if message.role == "assistant"]
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
        _memory_response(),
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
    assert result.task_memory is not None
    assert result.task_memory["facts"] == ["Use the structured CSV data."]
    react_call = model.calls[1]
    assert any("Persistent task memory" in message.content for message in react_call)


def test_agent_uses_fallback_memory_when_memory_json_is_invalid(tmp_path: Path) -> None:
    task = _task(tmp_path)
    (task.context_dir / "knowledge.md").write_text("# Knowledge\nUse observed data.", encoding="utf-8")
    responses = [
        "not-json",
        _validate_response(),
        _answer_response(),
    ]
    model = RecordingModelAdapter(responses)
    agent = ReActAgent(
        model=model,
        tools=create_default_tool_registry(),
        config=ReActAgentConfig(max_steps=2),
    )

    result = agent.run(task)

    assert result.succeeded
    assert result.task_memory is not None
    assert result.task_memory["source"] == "fallback_from_profile_context"
    assert "memory_error" in result.task_memory


def test_agent_preserves_deterministic_ambiguity_checklist_in_memory(tmp_path: Path) -> None:
    context_dir = tmp_path / "context"
    context_dir.mkdir()
    csv_dir = context_dir / "csv"
    csv_dir.mkdir()
    (csv_dir / "race.csv").write_text("position,round,number,name\n19,17,7,A\n", encoding="utf-8")
    task = PublicTask(
        record=TaskRecord(
            task_id="task_test",
            difficulty="easy",
            question="Which race was Alex in when track number was less than 20?",
        ),
        assets=TaskAssets(task_dir=tmp_path, context_dir=context_dir),
    )
    responses = [
        _memory_response(),
        (
            '{"thought":"validate","action":"validate_answer",'
            '"action_input":{"columns":["name"],"rows":[["A"]],"notes":"Compared position and round; no ambiguity affects this fixture."}}'
        ),
        '{"thought":"submit","action":"answer","action_input":{"columns":["name"],"rows":[["A"]]}}',
    ]
    model = RecordingModelAdapter(responses)
    agent = ReActAgent(
        model=model,
        tools=create_default_tool_registry(),
        config=ReActAgentConfig(max_steps=2),
    )

    result = agent.run(task)

    assert result.succeeded
    assert result.task_memory is not None
    checklist = result.task_memory["deterministic_ambiguity_checklist"]
    assert isinstance(checklist, list)
    number_item = next(item for item in checklist if item["term"] == "number")
    assert "race.position" in number_item["candidate_columns"]
    assert "race.round" in number_item["candidate_columns"]


def test_agent_answer_guard_requires_one_successful_validation_before_answer(
    tmp_path: Path,
) -> None:
    task = _task(tmp_path)
    responses = [
        _memory_response(),
        (
            '{"thought":"try submit before validation","action":"answer",'
            '"action_input":{"columns":["result"],"rows":[["ok"]]}}'
        ),
        _validate_response(),
        (
            '{"thought":"submit after validation","action":"answer",'
            '"action_input":{"columns":["result"],"rows":[["ok"]]}}'
        ),
    ]
    model = RecordingModelAdapter(responses)
    agent = ReActAgent(
        model=model,
        tools=create_default_tool_registry(),
        config=ReActAgentConfig(max_steps=3),
    )

    result = agent.run(task)

    assert result.succeeded
    assert [step.action for step in result.steps] == [
        "profile_context",
        "answer",
        "validate_answer",
        "answer",
    ]
    guarded_step = result.steps[1]
    assert guarded_step.observation["tool"] == "answer_guard"
    assert not guarded_step.ok
