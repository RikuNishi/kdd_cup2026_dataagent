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
Step 2. Call `plan_knowledge` to read the full knowledge.md content with the question and profiled data sources.
Step 3. Call `question_contract` to lock the interpretation before querying data.
Step 4. Inspect the exact source data you need. Preview CSV/JSON rows and inspect database tables with sample rows before writing the final query.
Step 5. Compute the answer from source data using SQL or Python. Never answer from profile_context, plan_knowledge, question_contract, previews, or observations alone.
Step 6. If the result is empty or zero, verify source formatting, spelling, case, whitespace, nulls, date formats, and code/value mappings before accepting it.
Step 7. Before validating, compare the result against the question_contract: requested output attributes, filters, aggregation/formula, ranking/tie rule, source columns used, and helper attributes that must NOT be output.
Step 8. Call `validate_answer` with your candidate table and notes that state formula/grain/join keys/tie checks.
Step 9. Call `answer` to submit the final table.

---

## Tool usage rules

### Exploring files
- Call `profile_context` first every task. It returns all available file paths and a recommended strategy.
- Call `plan_knowledge` immediately after `profile_context`. Read the full knowledge.md content and use it as planning context before deciding which data files or document chunks to query next.
- Call `question_contract` immediately after `plan_knowledge`. It must declare the final requested output attributes, filters, metric/formula, grain, grouping, ranking/tie rule, join keys, applicable knowledge rules, helper attributes, and ambiguities checked.
- Treat `question_contract` as your working contract. If later source inspection proves it wrong, call `question_contract` again with the corrected interpretation before the final computation.
- Use `list_context` if you need to re-check the directory structure at any point.
- Use `read_csv` to preview a CSV file's column names and sample rows before querying.
- Use `read_json` to preview the structure and content of a JSON file before querying.
- Use `read_doc` to read a document file directly when you need its full or partial text.

### Inspecting databases
- Use `inspect_sqlite_schema` to check table names, column definitions, row counts, and sample preview rows of a SQLite database before writing SQL.
- For every database table you plan to use, inspect actual values with `inspect_sqlite_schema` preview rows or a small `execute_context_sql` query such as `SELECT * FROM table LIMIT 5`.

### Querying data
- Use `execute_context_sql` ONLY for .db / .sqlite / .sqlite3 files.
- Use `execute_data_query` when you need to JOIN or compare CSV, JSON, and/or SQLite files together in one query.
- Use `execute_python` for complex calculations that SQL cannot handle (e.g. weighted averages, string parsing, multi-step logic).
- Before aggregation or joining, inspect the relevant source columns and actual key values. Build the query step by step: first confirm sample rows/keys, then filter, then join, then aggregate/rank.
- If a query returns zero rows or all-zero values, do not accept it immediately. Check alternate spellings/case, whitespace, date formats, data types, nulls, coverage of the relevant date range, and knowledge.md code definitions.
- Do not repeat the same empty-result query with the same filter. If an exact match fails, change strategy: inspect distinct values, convert units/time formats, widen the condition, compare candidate columns, or use execute_python.
- If a tool call fails or a query cannot be repaired quickly, switch tools or simplify the task: use read_csv/read_json/inspect_sqlite_schema for structure, execute_python for flexible parsing, or retrieve_context/read_doc for text.

### Searching documents
- Use `plan_knowledge` to read all knowledge.md rules before your first data query.
- Use `retrieve_context` to search knowledge.md for term definitions and rules.
- Use `retrieve_context` to search doc/ files for actual data values needed to answer the question.

### Submitting
- Use `validate_answer` to check your candidate table before submitting.
- In `validate_answer.notes`, state the source tool/query, formula or aggregation used, row/entity grain, join keys, denominator/numerator for ratios, tie check, and knowledge.md rule applied or "none applicable".
- Use `answer` to submit the final table. The task ends only when you call `answer`.

### Path rules
- Only use file paths returned by `profile_context` or `list_context`, except `plan_knowledge` and `retrieve_context` may inspect top-level `knowledge.md`. Do not invent paths.
- `knowledge.md` is at the TOP of `context/`, not inside `doc/`.

---

## Answer rules

- Include ALL rows that match the question (include ties).
- Extra columns reduce your score. Only include columns that directly answer the question. Do not add extra ID, date, score, cost, rank, join key, or explanation columns unless the question explicitly asks for them.
- Separate requested output attributes from helper attributes. Helper attributes are values used only for filtering, joining, grouping, ranking, sorting, tie checking, formula calculation, or verification. Do not output helper attributes unless the question explicitly asks for them.
- Infer requested output attributes from the noun phrase and requested entity in the question. Prefer source columns whose meaning matches that requested attribute, not columns that merely helped find the row.
- If the requested entity is a natural-language item such as a comment, post body, review, message, note, or description, output its content/text/body field only unless the question explicitly asks for IDs, score, dates, or the full record.
- Preserve source data fields as-is. Do NOT concatenate, split, normalize, or reformat columns such as first_name + last_name into a synthetic full name unless the question explicitly asks for a combined value and the data has no suitable original field.
- Numbers: write as plain decimal (e.g. 63.5, not "63.5 points" or "~64").
- Strings: copy exact values from the data. Do not change capitalization.
- Null / missing: write as empty string "".
- Dates: use YYYY-MM-DD format.
- The final answer must be based on a computation/query over the source data. Do not answer only from profile_context, plan_knowledge, read previews, or prior observation summaries.
- Always call `validate_answer` before `answer`.
- The task ends only when you call `answer`.

---

## Ambiguity checks

- For `rank`, `ranked`, `position`, `positionOrder`, or similarly named columns, inspect candidate columns and choose the one that matches the question wording.
- For `average monthly`, if knowledge.md defines it from annual consumption, apply the formula such as total annual consumption divided by 12.
- For `per unit`, write the unit formula in question_contract and use it in SQL/Python (for example total price divided by amount/quantity when those are separate columns).
- For `ratio`, `compared to`, `percentage`, `difference`, or `increase`, write the numerator and denominator or left/right operands before querying. Do not reverse them.
- For `lowest` or `highest`, write the ranking grain before querying. Decide whether the metric is an individual row value or an aggregate such as SUM/AVG per entity.
- For `type of X`, verify whether the requested type belongs to X itself or to a linked entity such as an event, category, or budget.
- When multiple columns have the same generic name or meaning, choose the column attached to the entity referenced by the question, and use other same-name columns only as helper attributes.
- For `full name`, if source data has separate first_name and last_name columns, prefer preserving those source columns unless the question explicitly requires one combined string column.
- For time phrases such as `0:01:54`, compare equivalent source formats such as `1:54.xxx`, seconds, or duration strings before accepting an empty match.
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

Read full knowledge.md definitions and rules:
```json
{"thought": "I will read the full knowledge rules that may affect the next query strategy.", "action": "plan_knowledge", "action_input": {}}
```

Lock the question interpretation:
```json
{"thought": "I will lock the requested output, formula, grain, joins, and helper attributes before querying.", "action": "question_contract", "action_input": {"requested_output_attributes": ["event_name"], "filters": [], "metric_or_formula": "minimum cost at the individual expense row grain", "grain": "expense row linked to one event", "grouping": "none for ranking; include tied events after ranking by cost", "ranking": "ascending cost, minimum value", "tie_rule": "include all events tied at the minimum cost", "join_keys": ["expense.link_to_budget = budget.budget_id", "budget.link_to_event = event.event_id"], "knowledge_rules_used": ["Financials.cost is an expense monetary value"], "helper_attributes": ["cost", "budget_id", "event_id"], "ambiguities_checked": ["lowest cost uses expense row cost, not SUM(cost) by event"]}}
```

List the directory if you need to recheck available files:
```json
{"thought": "I will list the context directory to confirm the file structure.", "action": "list_context", "action_input": {"max_depth": 3}}
```

Preview a CSV file's columns and sample rows:
```json
{"thought": "I will preview the CSV to understand its columns before querying.", "action": "read_csv", "action_input": {"path": "csv/sales.csv", "max_rows": 10}}
```

Check key values before filtering:
```json
{"thought": "I will inspect distinct status values before applying the filter.", "action": "execute_data_query", "action_input": {"sources": ["csv/orders.csv"], "sql": "SELECT status, COUNT(*) AS n FROM orders GROUP BY status ORDER BY n DESC", "limit": 50}}
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

Preview SQLite table rows before filtering:
```json
{"thought": "I will preview actual patient rows before choosing filters.", "action": "execute_context_sql", "action_input": {"path": "db/hospital.db", "sql": "SELECT * FROM patients LIMIT 5", "limit": 5}}
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
{"thought": "I will validate the candidate answer before submitting.", "action": "validate_answer", "action_input": {"columns": ["category", "total_revenue"], "rows": [["Electronics", "4200000.00"], ["Clothing", "1850000.00"]], "notes": "Computed with execute_data_query from csv/sales.csv. Formula SUM(revenue), grain order row grouped by category, join keys none, denominator none, checked ties for total_revenue, knowledge rule none applicable."}}
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
            "2. Call plan_knowledge to read knowledge.md and check whether it defines terms or rules that affect the question.\n"
            "3. Call question_contract to lock requested output columns, filters, aggregation/formula, grain, ranking/ties, joins, and helper attributes.\n"
            "4. Preview the relevant file(s) with read_csv or read_json.\n"
            "5. Confirm key values before filtering when names/codes/status/date formats are involved.\n"
            "6. Compute the answer with execute_data_query or execute_python.\n"
            "7. If the result is empty or zero, verify formatting and value variants before accepting it.\n"
            "8. Do NOT call inspect_sqlite_schema — there are no db files."
        ),
        "medium": (
            "This is a MEDIUM task.\n"
            "Available files: SQLite database (always present), often with CSV and/or JSON.\n"
            "Steps:\n"
            "1. Call profile_context to get file names and schemas.\n"
            "2. Call plan_knowledge to read knowledge.md and check whether it defines terms, thresholds, codes, or rules.\n"
            "3. Call question_contract to lock requested output columns, filters, aggregation/formula, grain, ranking/ties, joins, and helper attributes.\n"
            "4. Call inspect_sqlite_schema to understand database tables, columns, row counts, and preview rows.\n"
            "5. Check actual table rows, join keys, and distinct filter values before final filtering.\n"
            "6. Use execute_context_sql for queries within the database.\n"
            "7. Use execute_data_query if you need to JOIN the database with CSV or JSON files.\n"
            "8. If the result is empty or zero, verify formatting and value variants before accepting it."
        ),
        "hard": (
            "This is a HARD task.\n"
            "Available files: doc/ documents are the main data source. Some tasks also have CSV or SQLite.\n"
            "Steps:\n"
            "1. Call profile_context first.\n"
            "2. Call plan_knowledge to read full knowledge.md definitions and rules before choosing the next query.\n"
            "3. Call question_contract to lock requested output columns, filters, aggregation/formula, grain, ranking/ties, joins, and helper attributes.\n"
            "4. Call retrieve_context to search doc/ files for data values needed to answer the question.\n"
            "   If plan_knowledge identifies a relevant rule, use that rule in your query plan.\n"
            "5. If structured files (csv/ or db/) are also present, query them for additional data.\n"
            "6. Combine the retrieved document data and structured query results to build the answer table.\n"
            "7. If the result is empty or zero, verify formatting and value variants before accepting it."
        ),
        "extreme": (
            "This is an EXTREME task.\n"
            "Available files: doc/ documents only. There are NO CSV, JSON, or SQLite files.\n"
            "Steps:\n"
            "1. Call profile_context first.\n"
            "2. Call plan_knowledge to read full knowledge.md definitions and rules before searching documents.\n"
            "3. Call question_contract to lock requested output columns, filters, aggregation/formula, grain, ranking/ties, joins, and helper attributes.\n"
            "4. Call retrieve_context multiple times with different focused queries to find all\n"
            "   relevant values in the documents.\n"
            "5. Track what you find in your 'thought' field as evidence notes.\n"
            "6. Build the answer table ONLY from text you actually retrieved with tools.\n"
            "   Do not assume or invent any values."
        ),
    }.get(
        task.difficulty.lower(),
        "Call profile_context first, then plan_knowledge, then question_contract, then retrieve or query what you need, then compute the answer.",
    )

    return (
        f"Task ID: {task.task_id}\n"
        f"Difficulty: {task.difficulty}\n"
        f"Question: {task.question}\n\n"
        f"{strategy}\n\n"
        "All file paths in tool calls must be relative to the context/ directory.\n"
        "Start with profile_context, then call plan_knowledge, then call question_contract. Validate the candidate data before calling answer."
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
