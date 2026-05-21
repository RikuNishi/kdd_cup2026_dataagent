from __future__ import annotations

from pathlib import Path

from data_agent_baseline.agents.model import ModelMessage
from data_agent_baseline.agents.react import ReActAgent, ReActAgentConfig, _build_step_summary, parse_model_step
from data_agent_baseline.agents.runtime import StepRecord
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


def test_tool_error_observation_recommends_switching_tools(tmp_path: Path) -> None:
    bad_tool_response = (
        '{"thought":"try missing file","action":"read_csv",'
        '"action_input":{"path":"csv/missing.csv","max_rows":5}}'
    )
    final_response = (
        '{"thought":"done","action":"answer",'
        '"action_input":{"columns":["result"],"rows":[["ok"]]}}'
    )
    model = RecordingModelAdapter([bad_tool_response, final_response])
    agent = ReActAgent(
        model=model,
        tools=create_default_tool_registry(),
        config=ReActAgentConfig(max_steps=2),
    )

    result = agent.run(_task(tmp_path))

    assert result.succeeded
    assert result.steps[0].action == "__error__"
    assert result.steps[0].observation["failed_action"] == "read_csv"
    assert "Do not repeat the same tool call" in str(result.steps[0].observation["repair_instruction"])
    assert "execute_python" in str(result.steps[0].observation["fallback_examples"])


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
        '{"thought":"plan knowledge","action":"plan_knowledge","action_input":{}}',
        (
            '{"thought":"lock contract","action":"question_contract",'
            '"action_input":{"requested_output_attributes":["name"],'
            '"filters":[],"metric_or_formula":"highest score",'
            '"grain":"member row","grouping":"none","ranking":"score desc",'
            '"tie_rule":"top one only after checking ties","join_keys":[],'
            '"knowledge_rules_used":["none applicable"],'
            '"helper_attributes":["score"],'
            '"ambiguities_checked":["name is the requested output"]}}'
        ),
        (
            '{"thought":"query structured data","action":"execute_data_query",'
            '"action_input":{"sources":["csv/value_scores.csv"],'
            '"sql":"SELECT name FROM value_scores ORDER BY score DESC LIMIT 1","limit":10}}'
        ),
        (
            '{"thought":"validate candidate","action":"validate_answer",'
            '"action_input":{"columns":["name"],"rows":[["Lin"]],'
            '"notes":"Computed with execute_data_query. Formula highest score, grain member row, join keys none, checked ties unique, knowledge rule none applicable."}}'
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
        config=ReActAgentConfig(max_steps=6),
    )

    result = agent.run(task)

    assert result.succeeded
    assert [step.action for step in result.steps] == [
        "profile_context",
        "plan_knowledge",
        "question_contract",
        "execute_data_query",
        "validate_answer",
        "answer",
    ]
    assert result.answer is not None
    assert result.answer.rows == [["Lin"]]


def test_step_summary_preserves_contract_and_query_facts() -> None:
    contract_step = StepRecord(
        step_index=3,
        thought="lock contract",
        action="question_contract",
        action_input={},
        raw_response="{}",
        observation={
            "tool": "question_contract",
            "content": {
                "contract": {
                    "requested_output_attributes": ["name"],
                    "metric_or_formula": "highest score",
                    "grain": "member row",
                    "grouping": "none",
                    "ranking": "score desc",
                    "tie_rule": "include ties",
                    "join_keys": [],
                    "helper_attributes": ["score"],
                    "ambiguities_checked": ["name vs id"],
                },
                "warnings": [],
            },
        },
        ok=True,
    )
    query_step = StepRecord(
        step_index=4,
        thought="query",
        action="execute_data_query",
        action_input={},
        raw_response="{}",
        observation={
            "tool": "execute_data_query",
            "content": {
                "columns": ["name"],
                "rows": [["Lin"], ["Ada"], ["Grace"], ["Katherine"]],
                "row_count": 4,
                "truncated": False,
            },
        },
        ok=True,
    )

    assert "requested_output_attributes" in _build_step_summary(contract_step)
    query_summary = _build_step_summary(query_step)
    assert "row_count" in query_summary
    assert "preview_rows" in query_summary
    assert "Katherine" not in query_summary


def test_agent_projects_helper_columns_before_answer(tmp_path: Path) -> None:
    task = PublicTask(
        record=TaskRecord(
            task_id="task_test",
            difficulty="easy",
            question="Which event has the lowest cost?",
        ),
        assets=TaskAssets(task_dir=tmp_path, context_dir=tmp_path / "context"),
    )
    task.context_dir.mkdir()
    responses = [
        (
            '{"thought":"validate candidate","action":"validate_answer",'
            '"action_input":{"columns":["event_name","cost"],'
            '"rows":[["November Speaker",6.0]],'
            '"notes":"Computed with execute_data_query and checked the minimum cost tie."}}'
        ),
        (
            '{"thought":"submit with helper column","action":"answer",'
            '"action_input":{"columns":["event_name","cost"],"rows":[["November Speaker",6.0]]}}'
        ),
        (
            '{"thought":"remove helper column","action":"answer",'
            '"action_input":{"columns":["event_name"],"rows":[["November Speaker"]]}}'
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
        "validate_answer",
        "answer",
    ]
    assert result.steps[1].observation["content"]["auto_submitted"]
    assert "projected helper columns" in str(result.steps[1].observation)
    assert result.answer is not None
    assert result.answer.columns == ["event_name"]


def test_agent_near_step_limit_prioritizes_submission(tmp_path: Path) -> None:
    task = _task(tmp_path)
    csv_dir = task.context_dir / "csv"
    csv_dir.mkdir()
    (csv_dir / "values.csv").write_text("name,score\nAda,10\nLin,20\n", encoding="utf-8")
    responses = [
        (
            '{"thought":"query candidate","action":"execute_data_query",'
            '"action_input":{"sources":["csv/values.csv"],'
            '"sql":"SELECT name FROM values ORDER BY score DESC LIMIT 1","limit":10}}'
        ),
        '{"thought":"keep exploring","action":"list_context","action_input":{"max_depth":3}}',
        (
            '{"thought":"validate now","action":"validate_answer",'
            '"action_input":{"columns":["name"],"rows":[["Lin"]],'
            '"notes":"Computed with execute_data_query. Formula highest score, grain row, join keys none, checked ties unique, knowledge rule none applicable."}}'
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
        "execute_data_query",
        "near_step_limit_submit",
        "validate_answer",
        "answer",
    ]
    assert result.steps[-1].observation["content"]["auto_submitted"]
    assert result.answer is not None
    assert result.answer.rows == [["Lin"]]
