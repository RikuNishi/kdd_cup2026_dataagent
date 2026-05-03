from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

from data_agent_baseline.benchmark.schema import PublicTask, TaskAssets, TaskRecord
from data_agent_baseline.tools.answer_validation import validate_answer_table
from data_agent_baseline.tools.context_profile import build_context_profile, retrieve_context_chunks
from data_agent_baseline.tools.data_query import execute_data_query


def _task(tmp_path: Path, *, question: str = "List all matching names.") -> PublicTask:
    context_dir = tmp_path / "context"
    context_dir.mkdir()
    return PublicTask(
        record=TaskRecord(task_id="task_test", difficulty="hard", question=question),
        assets=TaskAssets(task_dir=tmp_path, context_dir=context_dir),
    )


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerows(rows)


def _write_sqlite(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE scores (id INTEGER, score INTEGER)")
        conn.executemany("INSERT INTO scores VALUES (?, ?)", [(1, 10), (2, 20)])


def test_context_profile_lists_structured_and_document_assets(tmp_path: Path) -> None:
    task = _task(tmp_path)
    _write_csv(task.context_dir / "csv" / "members.csv", [["id", "name"], ["1", "Ada"]])
    (task.context_dir / "json").mkdir()
    (task.context_dir / "json" / "clubs.json").write_text(
        json.dumps({"table": "clubs", "records": [{"id": 1, "club": "Math"}]}),
        encoding="utf-8",
    )
    _write_sqlite(task.context_dir / "db" / "scores.db")
    (task.context_dir / "doc").mkdir()
    (task.context_dir / "doc" / "guide.md").write_text("# Guide\nUseful details", encoding="utf-8")
    (task.context_dir / "knowledge.md").write_text("# Knowledge\nDefinitions", encoding="utf-8")

    profile = build_context_profile(task)

    assert profile["task_id"] == "task_test"
    assert set(profile["modalities"]) == {"csv", "db", "doc", "json", "knowledge"}
    paths = {item["path"] for item in profile["files"]}
    assert "knowledge.md" in paths
    assert "doc/guide.md" in paths
    csv_entry = next(item for item in profile["files"] if item["path"] == "csv/members.csv")
    assert csv_entry["columns"] == ["id", "name"]
    db_entry = next(item for item in profile["files"] if item["path"] == "db/scores.db")
    assert db_entry["tables"][0]["name"] == "scores"
    assert profile["knowledge"]["text"] == "# Knowledge\nDefinitions"


def test_context_profile_adds_solver_hints(tmp_path: Path) -> None:
    task = _task(
        tmp_path,
        question="Which event has the lowest cost and type number?",
    )
    _write_csv(
        task.context_dir / "csv" / "budget.csv",
        [
            ["budget_id", "amount", "spent", "link_to_event"],
            ["b1", "10", "6", "e1"],
            ["b2", "20", "7", "e1"],
        ],
    )
    (task.context_dir / "json").mkdir()
    (task.context_dir / "json" / "event.json").write_text(
        json.dumps(
            {
                "table": "event",
                "records": [
                    {"event_id": "e1", "event_name": "October Meeting", "type": "Meeting", "round": 1}
                ],
            }
        ),
        encoding="utf-8",
    )

    profile = build_context_profile(task)

    candidate_terms = {item["term"] for item in profile["question_column_candidates"]}
    assert {"cost", "type", "number"} <= candidate_terms
    checklist_terms = {item["term"] for item in profile["ambiguity_checklist"]}
    assert {"cost", "type", "number"} <= checklist_terms
    cost_check = next(item for item in profile["ambiguity_checklist"] if item["term"] == "cost")
    assert "row-level" in cost_check["required_action"]
    relationships = profile["relationship_candidates"]
    assert any(
        item["column"] == "link_to_event" and item["target_column"] == "event_id"
        for item in relationships
    )
    cardinality = profile["cardinality_hints"]
    link_hint = next(item for item in cardinality if item["qualified_name"] == "budget.link_to_event")
    assert link_hint["duplicate_count"] == 1
    assert link_hint["risk"] == "one_to_many_or_many_to_one"


def test_context_profile_flags_ambiguous_columns(tmp_path: Path) -> None:
    task = _task(tmp_path, question="List patient diagnosis.")
    (task.context_dir / "json").mkdir()
    (task.context_dir / "json" / "Patient.json").write_text(
        json.dumps(
            {
                "table": "Patient",
                "records": [{"ID": 1, "SEX": "F", "Diagnosis": "SLE"}],
            }
        ),
        encoding="utf-8",
    )
    (task.context_dir / "json" / "Examination.json").write_text(
        json.dumps(
            {
                "table": "Examination",
                "records": [{"ID": 1, "Diagnosis": "SLE+Psy", "Thrombosis": 2}],
            }
        ),
        encoding="utf-8",
    )

    profile = build_context_profile(task)

    ambiguous = {
        item["name"]: item["columns"]
        for item in profile["ambiguous_columns"]
    }
    assert "diagnosis" in ambiguous
    assert "Patient.Diagnosis" in ambiguous["diagnosis"]
    assert "Examination.Diagnosis" in ambiguous["diagnosis"]


def test_retrieve_context_reads_top_level_knowledge_and_doc_chunks(tmp_path: Path) -> None:
    task = _task(tmp_path, question="Which creatinine level is abnormal?")
    (task.context_dir / "doc").mkdir()
    (task.context_dir / "knowledge.md").write_text(
        "# Lab Rules\nCreatinine is abnormal above the reference range.",
        encoding="utf-8",
    )
    (task.context_dir / "doc" / "patient.md").write_text(
        "# Patient Notes\nAge and creatinine observations are recorded here.",
        encoding="utf-8",
    )

    result = retrieve_context_chunks(task, query="abnormal creatinine", max_chunks=3)

    paths = {chunk["path"] for chunk in result["chunks"]}
    assert "knowledge.md" in paths
    assert "doc/patient.md" in paths


def test_execute_data_query_joins_csv_json_and_sqlite(tmp_path: Path) -> None:
    task = _task(tmp_path)
    _write_csv(task.context_dir / "csv" / "members.csv", [["id", "name"], ["1", "Ada"], ["2", "Lin"]])
    (task.context_dir / "json").mkdir()
    (task.context_dir / "json" / "clubs.json").write_text(
        json.dumps(
            {
                "table": "clubs",
                "records": [{"id": 1, "club": "Math"}, {"id": 2, "club": "Robotics"}],
            }
        ),
        encoding="utf-8",
    )
    _write_sqlite(task.context_dir / "db" / "scores.db")

    result = execute_data_query(
        task,
        sources=["csv/members.csv", "json/clubs.json", "db/scores.db"],
        sql="""
        SELECT m.name, c.club, s.score
        FROM members AS m
        JOIN clubs AS c ON CAST(m.id AS INTEGER) = c.id
        JOIN scores AS s ON CAST(m.id AS INTEGER) = s.id
        ORDER BY s.score DESC
        """,
    )

    assert result["columns"] == ["name", "club", "score"]
    assert result["rows"][0] == ["Lin", "Robotics", 20]


def test_execute_data_query_serializes_dates_for_trace_json(tmp_path: Path) -> None:
    task = _task(tmp_path)
    _write_csv(task.context_dir / "csv" / "events.csv", [["event_date"], ["2026-05-01"]])

    result = execute_data_query(
        task,
        sources=["csv/events.csv"],
        sql="SELECT CAST(event_date AS DATE) AS event_date FROM events",
    )
    rendered = json.dumps(result)

    assert result["rows"] == [["2026-05-01"]]
    assert "2026-05-01" in rendered


def test_validate_answer_flags_shape_errors_and_tie_warnings(tmp_path: Path) -> None:
    task = _task(tmp_path, question="Which event has the lowest cost?")

    invalid = validate_answer_table(task, columns=["event"], rows=[["A", "extra"]])
    warning = validate_answer_table(task, columns=["event"], rows=[["A"]], notes="minimum cost tie")

    assert not invalid["ok"]
    assert invalid["errors"]
    assert warning["ok"]
    assert any("tie" in item.lower() for item in warning["warnings"])


def test_validate_answer_flags_duplicate_rows_and_ambiguous_notes(tmp_path: Path) -> None:
    task = _task(tmp_path, question="Which race number has the lowest cost and type for severe cases?")

    result = validate_answer_table(
        task,
        columns=["race_name"],
        rows=[["Japanese Grand Prix"], ["Japanese Grand Prix"]],
        notes="Computed result.",
    )

    assert result["ok"]
    rendered_warnings = "\n".join(result["warnings"]).lower()
    assert "duplicate answer rows" in rendered_warnings
    assert "ranking question" in rendered_warnings
    assert "ambiguous term 'cost'" in rendered_warnings
    assert "ambiguous term 'type'" in rendered_warnings
    assert "ambiguous term 'number'" in rendered_warnings
    assert "ambiguous term 'severe'" in rendered_warnings


def test_validate_answer_warns_aggregated_cost_without_row_level_comparison(tmp_path: Path) -> None:
    task = _task(tmp_path, question="Which event has the lowest cost?")

    result = validate_answer_table(
        task,
        columns=["event_name", "total_cost"],
        rows=[["October Meeting", 20.2]],
        notes=(
            "Using expense.cost from expense.json, not budget.spent. "
            "Aggregated SUM(expense.cost) by event; no tie."
        ),
    )

    assert result["ok"]
    assert result["ready_for_answer"]
    assert any("aggregated cost" in item for item in result["warnings"])
    assert result["blocking_warnings"] == []


def test_validate_answer_unblocks_with_rejected_cost_candidates(tmp_path: Path) -> None:
    task = _task(tmp_path, question="Which event has the lowest cost?")

    result = validate_answer_table(
        task,
        columns=["event_name"],
        rows=[["October Speaker"]],
        notes=(
            "Compared row-level MIN(expense.cost) vs SUM(expense.cost) by event, "
            "budget.spent, and budget.amount. Selected expense.cost because question asks "
            "lowest cost, not total expenditure; no tie beyond listed events."
        ),
    )

    assert result["ok"]
    assert result["ready_for_answer"]
    assert result["blocking_warnings"] == []


def test_validate_answer_warns_number_without_rejected_candidates(tmp_path: Path) -> None:
    task = _task(tmp_path, question="Which race was Alex Yoong in when he was in track number less than 20?")

    result = validate_answer_table(
        task,
        columns=["name"],
        rows=[["Japanese Grand Prix"]],
        notes="Used races.round as track number.",
    )

    assert result["ok"]
    assert result["ready_for_answer"]
    assert any("candidate comparison" in item for item in result["warnings"])
    assert result["blocking_warnings"] == []


def test_validate_answer_unblocks_number_with_rejected_candidates(tmp_path: Path) -> None:
    task = _task(tmp_path, question="Which race was Alex Yoong in when he was in track number less than 20?")

    result = validate_answer_table(
        task,
        columns=["name"],
        rows=[["Australian Grand Prix"]],
        notes=(
            "Compared driverStandings.position, races.round, and drivers.number. "
            "Selected driverStandings.position; rejected races.round as race sequence and "
            "drivers.number because it is car number."
        ),
    )

    assert result["ok"]
    assert result["ready_for_answer"]
    assert result["blocking_warnings"] == []


def test_validate_answer_unblocks_ambiguous_output_source_choice(tmp_path: Path) -> None:
    task = _task(tmp_path, question="Identify the type of expenses.")

    result = validate_answer_table(
        task,
        columns=["category"],
        rows=[["Food"]],
        notes="Compared budget.category and expense.expense_description; selected budget.category as the expense type source column.",
    )

    assert result["ok"]
    assert result["ready_for_answer"]
    assert result["blocking_warnings"] == []
