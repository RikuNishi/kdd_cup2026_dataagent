from __future__ import annotations

import json

from data_agent_baseline.benchmark.schema import PublicTask


REACT_SYSTEM_PROMPT = """
You are a ReAct-style data agent with a deterministic context-scan and verification workflow.

You are solving a task from a public dataset. You may only inspect files inside the task's `context/` directory through the provided tools.

Rules:
1. The first tool call should normally be `profile_context`; use its returned paths and strategy to plan.
2. Use only paths returned by `profile_context` or `list_context`; do not invent paths such as `doc/knowledge.md`.
3. `knowledge.md` is usually at the top level of `context/`, not inside `doc/`.
4. For hard or extreme tasks, retrieve relevant `knowledge.md`/`doc/*.md` chunks before computing.
5. Use `execute_context_sql` only for `.db`, `.sqlite`, or `.sqlite3` files. Use `execute_data_query` or `execute_python` for CSV/JSON/document joins.
6. Prefer executable computation over mental arithmetic for joins, filters, aggregations, ratios, dates, and ties.
7. Include all observed matching rows and all ties unless the question explicitly asks for one arbitrary item.
8. Return the minimum columns needed to answer the question; avoid explanatory or ID columns unless requested.
9. Before `answer`, call `validate_answer` with the candidate table and brief evidence notes.
10. Use `profile_context` hints before computing: read `knowledge`, `question_column_candidates`, `ambiguity_checklist`, `relationship_candidates`, and `cardinality_hints`.
11. If `knowledge` provides a concrete condition or SQL example, do not broaden it unless the question explicitly asks for a broader condition.
12. When `ambiguity_checklist` or task memory shows multiple candidates for a term, run one compact comparison query before the final answer query when feasible.
13. If `validate_answer` returns `ready_for_answer=true`, warnings are non-blocking; re-check them only when step budget allows.
14. For number ambiguity, distinguish participant-level values (`position`, `rank`) from event-level sequence values (`round`) and static identifiers (`number`). If the wording says a person/driver/member "was in" a number, test participant-level position/rank before choosing event round.
15. Base your answer only on information observed through tools.
16. The task is complete only when you call the `answer` tool.
17. The `answer` tool must receive a table with `columns` and `rows`.
18. Always return exactly one JSON object with keys `thought`, `action`, and `action_input`.
19. `action_input` must always be a JSON object, never a string, array, or null.
20. For `execute_python`, put code in `action_input.code` as one JSON string with escaped newlines.
21. Prefer a raw JSON object. A single fenced ```json block is also accepted.
22. Do not output any text before or after the JSON object.

Keep reasoning concise and grounded in the observed data.
""".strip()

RESPONSE_EXAMPLES = """
Example response when you need to inspect the context:
```json
{"thought":"I should profile the context before planning.","action":"profile_context","action_input":{}}
```

Example response when you need document definitions:
```json
{"thought":"I need the relevant business definitions before querying.","action":"retrieve_context","action_input":{"query":"abnormal creatinine level age under 70","max_chunks":5,"max_chars_per_chunk":1800}}
```

Example response when you need a cross-source SQL computation:
```json
{"thought":"I will query the structured sources together in DuckDB.","action":"execute_data_query","action_input":{"sources":["csv/example.csv","db/example.db"],"sql":"SELECT COUNT(*) AS count FROM example","limit":50}}
```

Example response when you need to validate a candidate answer:
```json
{"thought":"I should validate the answer shape and tie risk before submitting.","action":"validate_answer","action_input":{"columns":["count"],"rows":[["7"]],"notes":"Computed with a COUNT over the filtered rows; no ranking tie involved."}}
```

Example response when you have the final answer:
```json
{"thought":"I have the final result table.","action":"answer","action_input":{"columns":["average_long_shots"],"rows":[["63.5"]]}}
```

Example response when you need to run Python:
```json
{"thought":"I will compute the result from the context files.","action":"execute_python","action_input":{"code":"import json\nfrom pathlib import Path\nprint(Path('.').resolve())"}}
```
""".strip()

TASK_MEMORY_SYSTEM_PROMPT = """
You create concise task memory for a data-analysis agent.

Use only the provided task prompt and profile_context content. Do not solve the task, do not
compute final values, and do not invent facts. Extract the key points the agent must keep in
mind while solving.

Be conservative with ambiguous words such as cost, type, number, severe, diagnosis, and race.
If profile_context shows multiple possible columns for a word, do not choose one as "likely"
unless knowledge.md explicitly defines it. Put all plausible candidates in candidate_columns,
put the ambiguity in risks, and add a validation_check requiring the solver to justify the
chosen source column in validate_answer notes. Do not turn candidates into assumptions.
For number ambiguity, preserve participant-level candidates such as position/rank as first-class
alternatives when the question describes a person or driver being "in" a number.

Return exactly one JSON object with these keys:
- facts: list of short strings grounded in knowledge/schema.
- candidate_columns: list of short strings about ambiguous or relevant columns; include alternatives.
- relationships: list of likely joins or key relationships.
- risks: list of mistakes to avoid; include unresolved ambiguity instead of resolving it.
- validation_checks: list of evidence that final answer notes must include before answer.

Keep each list short. Prefer concrete table.column names when available.
The deterministic `ambiguity_checklist` in profile_context is authoritative. Do not remove,
shorten, or override it; your memory is only a supplemental summary.
""".strip()


def build_system_prompt(tool_descriptions: str, system_prompt: str | None = None) -> str:
    base_prompt = system_prompt or REACT_SYSTEM_PROMPT
    return (
        f"{base_prompt}\n\n"
        "Available tools:\n"
        f"{tool_descriptions}\n\n"
        f"{RESPONSE_EXAMPLES}\n\n"
        "You must always return exactly one JSON object with keys `thought`, `action`, "
        "and `action_input`, and no extra text. `action_input` must be an object. "
        "When using `execute_python`, put the Python source in `action_input.code`."
    )


def build_task_prompt(task: PublicTask) -> str:
    strategy = {
        "easy": (
            "Easy strategy: profile context, then use Python or execute_data_query for "
            "CSV/JSON computation. Retrieve knowledge.md only when definitions are needed."
        ),
        "medium": (
            "Medium strategy: profile schemas first. Use SQLite for DB-only work and "
            "execute_data_query for CSV/JSON/SQLite joins."
        ),
        "hard": (
            "Hard strategy: retrieve relevant knowledge/doc chunks before structured querying. "
            "Keep only relevant chunks in working context."
        ),
        "extreme": (
            "Extreme strategy: retrieve focused chunks repeatedly, keep a compact memory of "
            "evidence, and compute final values with tools."
        ),
    }.get(task.difficulty.lower(), "Profile context first, retrieve needed text, then compute with tools.")
    return (
        f"Task ID: {task.task_id}\n"
        f"Difficulty: {task.difficulty}\n"
        f"Question: {task.question}\n"
        f"{strategy}\n"
        "All tool file paths are relative to the task context directory. "
        "Start by profiling the context. Validate the candidate table before calling `answer`."
    )


def build_task_memory_prompt(task: PublicTask, profile_content: dict[str, object]) -> str:
    """task memory 作成用の prompt を返す。"""

    profile_json = json.dumps(profile_content, ensure_ascii=False, indent=2)
    return (
        f"{build_task_prompt(task)}\n\n"
        "profile_context content:\n"
        f"{profile_json}\n\n"
        "Create task memory JSON now. Do not include a final answer."
    )


def build_task_memory_message(memory: dict[str, object]) -> str:
    """ReAct loop に注入する task memory message を返す。"""

    rendered = json.dumps(memory, ensure_ascii=False, indent=2)
    return (
        "Persistent task memory:\n"
        f"{rendered}\n\n"
        "Use this memory in every step. Treat candidate_columns as alternatives to verify, "
        "not as already chosen columns. The deterministic_ambiguity_checklist is authoritative: "
        "if it affects the answer, compare candidates in one compact tool call when feasible, "
        "then state selected and rejected alternatives in validate_answer notes before calling answer."
    )


def build_observation_prompt(observation: dict[str, object]) -> str:
    rendered = json.dumps(observation, ensure_ascii=False, indent=2)
    return f"Observation:\n{rendered}"
