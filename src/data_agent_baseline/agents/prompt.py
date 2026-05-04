from __future__ import annotations

import json
from typing import Any

from data_agent_baseline.benchmark.schema import PublicTask


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

REACT_SYSTEM_PROMPT = """
## Output format (MUST follow every turn)

Always output exactly ONE JSON object with these three keys and nothing else:
- "thought": your brief reasoning (string)
- "action": the tool name to call (string)
- "action_input": the parameters for the tool (JSON object, never null or array)

Do NOT write any text before or after the JSON object.
A fenced ```json block is also accepted, but only one block.

---

## Your role

You are a data analysis agent. You answer questions by reading files in the
task's `context/` directory using the provided tools.
Never guess or invent values — always read them from files with tools.

---

## Fixed workflow (follow this order every task)

Step 1. Call `profile_context` first. Read the returned file list and strategy.
Step 2. Look at the files. Read schemas or previews for the files you need.
Step 3. Compute the answer using SQL or Python. Never do math in your head.
Step 4. Call `validate_answer` with your candidate table.
Step 5. Call `answer` to submit the final table.

---

## Tool usage rules

### Exploring files
- Call `profile_context` first every task. It returns all available file paths and a recommended strategy.
- Use `list_context` if you need to re-check the directory structure at any point.
- Use `read_csv` to preview a CSV file's column names and sample rows before querying.
- Use `read_json` to preview the structure and content of a JSON file before querying.
- Use `read_doc` to read a document file directly when you need its full or partial text.

### Inspecting databases
- Use `inspect_sqlite_schema` to check the table names and column definitions of a SQLite database before writing SQL.

### Querying data
- Use `execute_context_sql` ONLY for .db / .sqlite / .sqlite3 files.
- Use `execute_data_query` when you need to JOIN or compare CSV, JSON, and/or SQLite files together in one query.
- Use `execute_python` for complex calculations that SQL cannot handle (e.g. weighted averages, string parsing, multi-step logic).

### Searching documents
- Use `retrieve_context` to search knowledge.md for term definitions and rules.
- Use `retrieve_context` to search doc/ files for actual data values needed to answer the question.

### Submitting
- Use `validate_answer` to check your candidate table before submitting.
- Use `answer` to submit the final table. The task ends only when you call `answer`.

### Path rules
- Only use file paths returned by `profile_context` or `list_context`. Do not invent paths.
- `knowledge.md` is at the TOP of `context/`, not inside `doc/`.

---

## Answer rules

- Include ALL rows that match the question (include ties).
- Return only the columns needed to answer the question. Do not add extra ID or explanation columns.
- Numbers: write as plain decimal (e.g. 63.5, not "63.5 points" or "~64").
- Strings: copy exact values from the data. Do not change capitalization.
- Dates: use YYYY-MM-DD format.
- Null / missing: write as empty string "".
- Always call `validate_answer` before `answer`.
- The task ends only when you call `answer`.
""".strip()


# ---------------------------------------------------------------------------
# Response examples
# ---------------------------------------------------------------------------

RESPONSE_EXAMPLES = """
## Examples

Profile the context first:
```json
{"thought": "I will profile the context to see what files are available.", "action": "profile_context", "action_input": {}}
```

List the directory if you need to recheck available files:
```json
{"thought": "I will list the context directory to confirm the file structure.", "action": "list_context", "action_input": {"max_depth": 3}}
```

Preview a CSV file's columns and sample rows:
```json
{"thought": "I will preview the CSV to understand its columns before querying.", "action": "read_csv", "action_input": {"path": "csv/sales.csv", "max_rows": 10}}
```

Preview a JSON file's structure:
```json
{"thought": "I will preview the JSON file to understand its structure.", "action": "read_json", "action_input": {"path": "json/config.json", "max_chars": 2000}}
```

Read a document file directly:
```json
{"thought": "I will read the document file to find the relevant data.", "action": "read_doc", "action_input": {"path": "doc/report.md", "max_chars": 4000}}
```

Check a SQLite database schema before querying:
```json
{"thought": "I will inspect the database schema to understand the table structure.", "action": "inspect_sqlite_schema", "action_input": {"path": "db/hospital.db"}}
```

Query a SQLite database:
```json
{"thought": "I will count rows where status is active.", "action": "execute_context_sql", "action_input": {"path": "db/hospital.db", "sql": "SELECT COUNT(*) AS count FROM patients WHERE status = 'active'", "limit": 100}}
```

Join a CSV and a SQLite file with DuckDB:
```json
{"thought": "I need to join the CSV and the DB to get the final result.", "action": "execute_data_query", "action_input": {"sources": ["csv/orders.csv", "db/products.db"], "sql": "SELECT o.order_id, p.name FROM orders o JOIN products p ON o.product_id = p.id", "limit": 200}}
```

Run Python for complex calculation:
```json
{"thought": "I will calculate the weighted average in Python.", "action": "execute_python", "action_input": {"code": "import pandas as pd\ndf = pd.read_csv('csv/sales.csv')\nresult = (df['revenue'] * df['weight']).sum() / df['weight'].sum()\nprint(round(result, 2))"}}
```

Search knowledge.md for meaning of terms and data definitions:
```json
{"thought": "I need the definition of 'abnormal creatinine' from knowledge.md before querying.", "action": "retrieve_context", "action_input": {"query": "abnormal creatinine level", "max_chunks": 4, "max_chars_per_chunk": 1800}}
```

Search doc/ files for actual data values:
```json
{"thought": "I will search the doc files for the revenue figures mentioned in the question.", "action": "retrieve_context", "action_input": {"query": "Q3 revenue by region", "max_chunks": 6, "max_chars_per_chunk": 2000}}
```

Validate before submitting:
```json
{"thought": "I will validate the candidate answer before submitting.", "action": "validate_answer", "action_input": {"columns": ["category", "total_revenue"], "rows": [["Electronics", "4200000.00"], ["Clothing", "1850000.00"]], "notes": "Summed from sales table grouped by category. No ties."}}
```

Submit the final answer:
```json
{"thought": "Validation passed. I will submit the answer.", "action": "answer", "action_input": {"columns": ["category", "total_revenue"], "rows": [["Electronics", "4200000.00"], ["Clothing", "1850000.00"]]}}
```
""".strip()


# ---------------------------------------------------------------------------
# Truncation limits
# ---------------------------------------------------------------------------

MAX_OBSERVATION_STRING_CHARS = 1200
MAX_OBSERVATION_ITEMS = 12
MAX_OBSERVATION_JSON_CHARS = 12000


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def build_system_prompt(tool_descriptions: str, system_prompt: str | None = None) -> str:
    base_prompt = system_prompt or REACT_SYSTEM_PROMPT
    return (
        f"{base_prompt}\n\n"
        "---\n\n"
        "## Available tools\n\n"
        f"{tool_descriptions}\n\n"
        "---\n\n"
        f"{RESPONSE_EXAMPLES}\n\n"
        "---\n\n"
        "Remember: output exactly ONE JSON object per turn. No text before or after."
    )


def build_task_prompt(task: PublicTask) -> str:
    # Difficulty-specific instructions tuned to actual dataset composition:
    #   easy   → csv + json only (no db, no doc)
    #   medium → db always present, often with csv and/or json
    #   hard   → doc is the main source; some tasks also have csv or db
    #   extreme → doc only (no structured files)
    strategy = {
        "easy": (
            "This is an EASY task.\n"
            "Available files: CSV and/or JSON only. There is no database or document file.\n"
            "Steps:\n"
            "1. Call profile_context to see the CSV/JSON file names and column names.\n"
            "2. Preview the relevant file(s) with read_csv or read_json.\n"
            "3. Compute the answer with execute_data_query or execute_python.\n"
            "4. Do NOT call retrieve_context or inspect_sqlite_schema — there are no doc or db files."
        ),
        "medium": (
            "This is a MEDIUM task.\n"
            "Available files: SQLite database (always present), often with CSV and/or JSON.\n"
            "Steps:\n"
            "1. Call profile_context to get file names and schemas.\n"
            "2. Call inspect_sqlite_schema to understand the database tables and columns.\n"
            "3. Use execute_context_sql for queries within the database.\n"
            "4. Use execute_data_query if you need to JOIN the database with CSV or JSON files.\n"
            "5. Do NOT call retrieve_context unless profile_context shows a doc file."
        ),
        "hard": (
            "This is a HARD task.\n"
            "Available files: doc/ documents are the main data source. Some tasks also have CSV or SQLite.\n"
            "Steps:\n"
            "1. Call profile_context first.\n"
            "2. Call retrieve_context to search doc/ files for data values needed to answer the question.\n"
            "   Also search knowledge.md for any term definitions or rules the question depends on.\n"
            "   Use specific keywords from the question as the query.\n"
            "3. If structured files (csv/ or db/) are also present, query them for additional data.\n"
            "4. Combine the retrieved document data and structured query results to build the answer table."
        ),
        "extreme": (
            "This is an EXTREME task.\n"
            "Available files: doc/ documents only. There are NO CSV, JSON, or SQLite files.\n"
            "Steps:\n"
            "1. Call profile_context first.\n"
            "2. Call retrieve_context multiple times with different focused queries to find all\n"
            "   relevant values in the documents.\n"
            "3. Track what you find in your 'thought' field as evidence notes.\n"
            "4. Build the answer table ONLY from text you actually retrieved with tools.\n"
            "   Do not assume or invent any values."
        ),
    }.get(
        task.difficulty.lower(),
        "Call profile_context first, then retrieve or query what you need, then compute the answer.",
    )

    return (
        f"Task ID: {task.task_id}\n"
        f"Difficulty: {task.difficulty}\n"
        f"Question: {task.question}\n\n"
        f"{strategy}\n\n"
        "All file paths in tool calls must be relative to the context/ directory.\n"
        "Start with profile_context. Validate the candidate data before calling answer."
    )


# ---------------------------------------------------------------------------
# Observation formatting
# ---------------------------------------------------------------------------

def _truncate_value(value: Any) -> Any:
    if isinstance(value, str):
        if len(value) <= MAX_OBSERVATION_STRING_CHARS:
            return value
        return value[:MAX_OBSERVATION_STRING_CHARS] + "... [truncated]"
    if isinstance(value, list):
        items = [_truncate_value(item) for item in value[:MAX_OBSERVATION_ITEMS]]
        if len(value) > MAX_OBSERVATION_ITEMS:
            items.append(f"... [{len(value) - MAX_OBSERVATION_ITEMS} more items truncated]")
        return items
    if isinstance(value, dict):
        items = list(value.items())
        truncated = {
            str(key): _truncate_value(item_value)
            for key, item_value in items[:MAX_OBSERVATION_ITEMS]
        }
        if len(items) > MAX_OBSERVATION_ITEMS:
            truncated["_truncated_keys"] = len(items) - MAX_OBSERVATION_ITEMS
        return truncated
    return value


def build_observation_prompt(observation: dict[str, object]) -> str:
    rendered = json.dumps(_truncate_value(observation), ensure_ascii=False, indent=2)
    if len(rendered) > MAX_OBSERVATION_JSON_CHARS:
        rendered = rendered[:MAX_OBSERVATION_JSON_CHARS] + "\n... [observation truncated]"
    return f"Observation:\n{rendered}"