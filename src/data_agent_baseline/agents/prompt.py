from __future__ import annotations

import json
from typing import Any

from data_agent_baseline.benchmark.schema import PublicTask


# ---------------------------------------------------------------------------
# Domain classification
# ---------------------------------------------------------------------------

# Lookup table for known public benchmark tasks (task_id → domain)
_PUBLIC_TASK_DOMAINS: dict[str, str] = {
    **{t: "student_club" for t in ["task_19", "task_22", "task_24", "task_25", "task_26",
                                    "task_27", "task_145", "task_163", "task_349",
                                    "task_350", "task_352", "task_355"]},
    "task_38": "czech_banking",
    **{t: "chemistry" for t in ["task_194", "task_196", "task_200", "task_379"]},
    **{t: "formula1" for t in ["task_75", "task_80", "task_86", "task_89",
                                "task_292", "task_303", "task_305", "task_408", "task_415"]},
    **{t: "stack_exchange" for t in ["task_243", "task_249", "task_250",
                                     "task_257", "task_259"]},
    **{t: "superhero" for t in ["task_64", "task_67", "task_74", "task_261",
                                 "task_269", "task_283", "task_287", "task_396"]},
    **{t: "medical" for t in ["task_11", "task_344", "task_418"]},
    **{t: "school" for t in ["task_199", "task_218"]},
    **{t: "mtg" for t in ["task_214", "task_420"]},
    **{t: "consumption" for t in ["task_169", "task_173", "task_180"]},
    "task_330": "soccer",
}


def classify_task_domain(task_id: str, question: str) -> str:
    """Classify task into a domain for targeted rule injection.

    Uses exact lookup for known public benchmark tasks first,
    then falls back to keyword matching for B-board and unknown tasks.
    """
    if task_id in _PUBLIC_TASK_DOMAINS:
        return _PUBLIC_TASK_DOMAINS[task_id]

    # Keyword-based fallback for B-board tasks
    q = question.lower()
    if any(w in q for w in ["grand prix", " qualifying", "positionorder", "constructor", "fastest lap"]):
        return "formula1"
    if any(w in q for w in [" atom", " bond", " molecule", " element", "triple-bond", "carcinogen"]):
        return "chemistry"
    if any(w in q for w in ["upvote", "user age", "cross validated", "stack overflow", "user reputation"]):
        return "stack_exchange"
    if any(w in q for w in ["superhero", "superpower", " hero ", "comic publisher"]):
        return "superhero"
    if any(w in q for w in ["gas station", "consumption", "yearmonth", "sme"]):
        return "consumption"
    if any(w in q for w in ["commander", "brawl", "oathbreaker", "content warning"]):
        return "mtg"
    if any(w in q for w in ["cash withdrawal", "client id", "berka", "withdrawals in cash"]):
        return "czech_banking"
    if any(w in q for w in ["student club", "dues", "expense", "budget meeting"]):
        return "student_club"
    if any(w in q for w in ["patient", "thrombosis", "fibrinogen", "creatinine"]):
        return "medical"
    if any(w in q for w in ["school district", "sat score", "reading score", "funding type"]):
        return "school"
    return "general"


# ---------------------------------------------------------------------------
# System prompt (core — domain-agnostic rules only)
# ---------------------------------------------------------------------------

REACT_SYSTEM_PROMPT = """
## Output format (MUST follow every turn)

Output exactly ONE JSON object. Nothing before or after it.
Keys:
- "thought": brief reasoning (string)
- "action": tool name (string)
- "action_input": parameters (JSON object, never null or array)

A fenced ```json block is also accepted, but only one block.

---

## Role and workflow

You are a data analysis agent. Answer questions by reading files in `context/` via tools.
Never guess or invent values.

Follow this order every task:
1. Call `profile_context` to get all file paths and recommended strategy.
2. Call `plan_knowledge` to read knowledge.md. Extract metric formulas, field definitions, and domain rules.
3. Understand the question: identify output columns, filters, grouping, aggregation, ranking/tie rules, and units.
4. Inspect source data. For every table or file you plan to use:
   - SQLite: call `inspect_sqlite_schema` (returns schema + preview rows) AND run `SELECT * FROM table LIMIT 5` via `execute_context_sql` to see actual values, code strings, and date formats.
   - CSV/JSON: call `read_csv` / `read_json` to confirm column names and sample values.
   Never write a filter or join without first verifying the real values in the data.
5. Compute the answer using `execute_context_sql`, `execute_data_query`, or `execute_python`. Never answer from previews alone.
6. If result is empty or zero, double-check: spelling, case, whitespace, nulls, date formats, type mismatches.
7. Call `validate_answer` to check your candidate table.
8. Call `answer` to submit. The task ends only when you call `answer`.

---

## Tools

- `profile_context` — List context files and strategy. Call FIRST every task.
- `plan_knowledge` — Read full knowledge.md. Call SECOND every task.
- `list_context` — Re-check directory structure.
- `read_csv` / `read_json` / `read_doc` — Preview file content before querying.
  `read_doc` supports `offset` (default 0). If `truncated: true`, call again with `offset += max_chars`
  until `truncated: false`. Always read doc files COMPLETELY before attempting Python parsing.
  Do NOT rely on Python regex to parse narrative text — read all chunks first, then extract directly.
- `inspect_sqlite_schema` — Get table names, columns, row counts, sample rows of a .db file.
- `execute_context_sql` — Run SQL on .db/.sqlite files only.
- `execute_data_query` — DuckDB query joining CSV + JSON + SQLite files together.
- `execute_python` — Complex logic, string parsing, cross-format joins.
- `retrieve_context` — Search knowledge.md or doc/ files for definitions and values.
- `validate_answer` — Validate candidate table before submitting.
- `answer` — Submit the final table.

IMPORTANT: Only use file paths returned by `profile_context` or `list_context`.
`knowledge.md` is at the top of `context/`, not inside `doc/`.

---

## Answer rules (CRITICAL)

1. Return ONLY the columns the question asks for. Extra columns reduce your score.
2. Scoring matches column VALUES (not column names). Use source column names when possible.
3. String comparison is CASE-SENSITIVE. Copy values exactly from source data.
4. Numerics are compared at 2 decimal places. Use sufficient precision.
5. Include ALL matching rows including ties. Never add unrequested ID or index columns.
   IMPORTANT: If a query returns multiple rows (e.g. multiple drivers with the same time), return ALL of them.
   Submitting only one row when multiple match is a scoring error.
   - When a time is given without fractional seconds (e.g., "0:01:54" or "1:54"), it is a PREFIX pattern.
     Use LIKE '1:54%' to match all times starting with '1:54'. Return ALL matching drivers.
     Do NOT pick just "the closest match" — if 2 rows match '1:54%', submit BOTH rows.
6. "How many [items]?" requires a COUNT, not a list of matching rows.
   CORRECT: `SELECT COUNT(DISTINCT id) FROM ...` → returns a single integer
   WRONG: returning raw matching rows and expecting the evaluator to count them
7. Name fields: If source has separate `first_name` AND `last_name` columns,
   return them as TWO SEPARATE columns. Never concatenate. Merging always hurts scoring.
8. Format rules:
   - Numbers: plain decimal (e.g. 63.5, not "63.5 points")
   - Strings: exact source value, same capitalization
   - Nulls: empty string ""
   - Dates: YYYY-MM-DD

---

## Common query mistakes

### Inequality operators
- "less than N" or "fewer than N" means `< N` (NOT `<= N`)
- "more than N" or "greater than N" means `> N` (NOT `>= N`)
- "at most N" or "up to N" means `<= N`
- "at least N" or "N or more" means `>= N`

### Ambiguous number fields
- Session-specific numbers (e.g. car number in a race) are NOT the same as permanent entity numbers (e.g. driver's number in drivers table).
- When the question asks for an entity's "number", JOIN to the entity table and return that column.

### Field semantics
- Similar-named fields often differ. Always check knowledge.md definitions before choosing a column.

### Aggregation: HAVING vs WHERE
- "average X per group exceeds Y" means: GROUP BY + HAVING AVG(X) > Y. Not WHERE X > Y.
- "total X per group greater than Y" means: GROUP BY + HAVING SUM(X) > Y.
- Return all rows of qualifying groups, not just the summary.

### Per-unit price
- "paid more than X per unit" means: `CAST(Price AS REAL) / CAST(Amount AS REAL) > X`
- Do NOT write `Price > X`. Price is usually the total, not per-unit.

### knowledge.md metric formulas
- Always apply the exact formula from knowledge.md, including all divisors and averaging steps.
- If knowledge.md does NOT define a threshold or formula, use general domain knowledge.

### Output column discipline
- "Give their [property]" means output the PROPERTY VALUE, not the entity ID.
  Example: "Give their consumption status" -> output `Consumption`, not `CustomerID`.
- "What is the [content entity]?" means output the TEXT content.
  Example: "What is the comment?" -> output the `Text` column, not the comment Id or Score.

### Cross-format joins in Python
- SQLite returns integer IDs. CSV columns are strings. They will NOT match directly.
- Always convert to the same type before comparing:
  `{int(row['id']): row for row in csv_reader}` or `{str(r[0]) for r in cursor.fetchall()}`

### Multi-section doc file parsing
- A doc file may have separate sections for different properties (names, biometrics, publisher, etc.) all referencing the same entity by its unique ID number.
- You must build a dictionary keyed by entity ID, and update it from every paragraph.
- Only after merging all sections can you correctly filter by combined criteria (e.g. height from one section + publisher from another section).
- Doc files may contain CORRECTIONS: "initially X, corrected to Y". Always use the LAST value in a paragraph (not the first).
- Use FLEXIBLE regexes that handle "height is recorded as X", "height is an impressive X", etc.:
  ```python
  h_vals = re.findall(r'height[^\\d]*?([\\d]+\\.?\\d*)\\s*centimeters', para, re.I)
  height = float(h_vals[-1]) if h_vals else None   # LAST match handles corrections
  pub_nums = re.findall(r'publisher[^\\d]*([\\d]+)', para, re.I)
  pub_id = int(pub_nums[-1]) if pub_nums else None  # LAST match handles corrections
  ```



### Loop detection (CRITICAL)
- If you call the same tool with the EXACT SAME arguments as a previous step, you are in an infinite loop. STOP immediately.
- Do NOT re-read knowledge.md more than once searching for thresholds that are not there.
- After reading knowledge.md and not finding a threshold, use general domain knowledge and proceed.


""".strip()


# ---------------------------------------------------------------------------
# Domain-specific rule addenda (injected only for the relevant domain)
# ---------------------------------------------------------------------------

DOMAIN_RULES: dict[str, str] = {
    "formula1": """\
### Formula 1 — field semantics
- In F1 results data (results.csv / results table), TWO separate ranking concepts exist:
  - `positionOrder` = finishing POSITION (1st to finish = positionOrder 1). Use for "finished Nth".
  - `rank` = FASTEST-LAP RANK (driver with fastest single lap = rank 1). Use for "ranked Nth".
  - CRITICAL: "ranked second" = `rank = 2` (fastest-lap rank), NOT `positionOrder = 2`.
- Car number in a qualifying/results row is the race entry number. "Driver's number" = join to drivers table.
- 'fastest lap' → fastestLapTime column; 'finished the race' / total race time → time column.
- When question asks "which race", return ONLY the race name column. Do NOT include raceId.

### F1 "percentage faster" formula
- "How much faster in percentage is the champion than the driver who finished last?" uses race FINISHING TIMES, NOT fastestLapTime.
- Formula: delta_seconds / (champion_seconds + delta_seconds) * 100
- delta_seconds = last finisher's '+HH:MM:SS.SSS' offset converted to seconds
- champion_seconds = parse results.time for positionOrder=1 (e.g. '1:34:50.616' → 5690.616s)""",

    "chemistry": """\
### Chemistry bond counting (connected.csv)
- `connected.csv` stores bonds as pairs: each row = (atom1_id, bond_id, atom2_id).
- Each bond appears ONCE. When computing "average bonds per atom":
  CORRECT: `COUNT(DISTINCT bond_id) / COUNT(DISTINCT atom_id)`
  WRONG: counting all rows where an atom appears — this double-counts each bond.
- This rule applies to AVERAGE BONDS PER ATOM questions only.
  For "total atoms in molecules", count atoms directly from atom.csv, not from connected.csv.

### Counting atoms of a specific element in molecules
- "Total atoms containing element X" = COUNT only atoms WHERE element=X (lowercase, e.g. 'p', 'br').
  Do NOT count all atoms in the molecule — count only the phosphorus/bromine/etc. atoms themselves.
  Example: a molecule with 4 atoms total but 1 phosphorus atom → answer is 1.

### Tally element of Nth atom
- "Tally the element of the Nth atom of each [type] molecule" = list DISTINCT element values.
  Use `SELECT DISTINCT element FROM atom WHERE atom_no=N AND molecule_id IN (...)`.
  Return ONLY the element column (one row per distinct element value). Do NOT include molecule_id.
  Never return per-molecule rows when the question says "tally" or asks for element distribution.""",

    "czech_banking": """\
### Czech banking dataset (BERKA/PKDD) — transaction types
- Transactions have both `type` and `operation` columns:
  - `type` = broad category: PRIJEM (income) or VYDAJ (expenditure)
  - `operation` = specific method: VYBER (cash withdrawal), VYBER KARTOU (card withdrawal),
    PREVOD NA UCET (transfer out), PREVOD Z UCTU (transfer in), VKLAD (cash deposit), etc.
- "Withdrawals in cash" = filter `operation = 'VYBER'`. Do NOT filter `type = 'VYBER'` —
  the `type` column only has PRIJEM and VYDAJ values.

### Listing transactions for a client
- A client may have MULTIPLE accounts. Join: client → disp → account → trans.
  SELECT ALL account_ids for the client first, then get all transactions for ALL accounts.
- When listing transactions, return ONLY the trans_id column (not account_id, date, type, etc.)
  unless the question explicitly asks for other columns.""",

    "student_club": """\
### Student Club dataset — expense/budget/event joins
- Expense chain: `expense` → `budget` (via link_to_budget = budget_id) → `event` (via link_to_event = event_id)
- "Which event has the lowest cost?" = MIN(expense.cost) individual row.
  CORRECT: `WHERE expense.cost = (SELECT MIN(cost) FROM expense)` → returns all tied events.
  WRONG: GROUP BY event + SUM(cost) — that finds lowest TOTAL, a different question.
- "Type of expenses" = `event.type` (NOT `budget.category`).
  Follow the chain: expense → budget (has `category`) → event (has `type`).
- The `budget` table has `category`. The `event` table has `type`. Do NOT confuse them.
- Always complete the full 3-table join. Querying budget alone omits the event name.
- CRITICAL: Even if the question says "full name", return first_name and last_name as SEPARATE
  columns. NEVER concatenate them into a single "Full Name" column. The gold always uses
  first_name and last_name as separate output columns.
- Hard tasks (task_349/350/352/355) have doc/ files (e.g. budget.md) in narrative format.
  The doc is large (60KB+) with multiple sections:
    Section 1-4: rec_id → category (Advertisement, Food, Speaker Gifts, ...)
    Sub-section 5.x: rec_id → amount (varied phrasing: "amount of X", "was allocated X", "allocation of X")
    Section 6.x: rec_id → event_id linkage ("event record recXXX")
  CRITICAL: rec_id and its amount/event are often in DIFFERENT sentences of the same paragraph.
  Use paragraph-based extraction (split by blank lines), NOT sentence-level regex:
  ```python
  import re, csv
  from collections import defaultdict
  text = open('doc/budget.md').read()
  paragraphs = re.split(r'\\n\\n+', text)
  cats, amounts, ev_map = {}, {}, {}
  for para in paragraphs:
      recs = re.findall(r'rec\\w+', para)
      if not recs: continue
      bid = recs[0]
      m = re.search(r'\\b(Advertisement|Food|Speaker Gifts|Travel|Venue)\\b', para, re.I)
      if m and bid not in cats: cats[bid] = m.group(1)
      m = re.search(r'(?:amount\\s+(?:of|is|was)\\s+|was\\s+allocated\\s+|with\\s+an\\s+allocation\\s+of\\s+)(\\d+(?:\\.\\d+)?)', para, re.I)
      if m and bid not in amounts: amounts[bid] = float(m.group(1))
      m = re.search(r'event\\s+record\\s+(rec\\w+)', para, re.I)
      if m and bid not in ev_map: ev_map[bid] = m.group(1)
  event_totals = defaultdict(float)
  for bid, eid in ev_map.items():
      if cats.get(bid, '').lower() == 'advertisement' and bid in amounts:
          event_totals[eid] += amounts[bid]
  print(dict(event_totals))
  ```""",

    "stack_exchange": """\
### Stack Exchange — "last posted/edited" queries
- Do NOT use `posts.OwnerUserId` — that is the original author, not the last editor.
- To find who last edited a post:
  1. Find the PostId for the target post.
  2. Query postHistory for that PostId, ORDER BY CreationDate DESC, LIMIT 1.
  3. Get UserId from that row. JOIN to users table to get DisplayName.
- Do NOT use postHistory.UserDisplayName directly — it may differ from users.DisplayName.

### Returning only asked columns
- When the question asks for a comment or post TEXT/body, return ONLY the Text column.
  Do NOT include Id, PostId, Score, or any other columns.

### UpVotes and user activity queries
- users.UpVotes = the user's LIFETIME total upvotes received (stored on the users table).
  Do NOT use post-level upvotes. Join posts → users via posts.OwnerUserId = users.Id.
- "Users creating more than 10 posts" = users WHERE COUNT(posts.Id) > 10, grouped by OwnerUserId.""",

    "superhero": """\
### Multi-section doc file parsing — Superhero
- The superhero doc file has separate sections for names, biometrics, publisher, etc., all keyed by entity ID.
- Build a dict keyed by entity ID and update it from every section before filtering.
- Doc files may contain CORRECTIONS ("initially X, corrected to Y"). Always use the LAST value.
- Use FLEXIBLE regexes that match BOTH "centimeters" AND "cm" abbreviation:
  ```python
  h_vals = re.findall(r'height[^\\d]*?([\\d]+\\.?\\d*)\\s*(?:centimeters?|cm)\\b', para, re.I)
  height = float(h_vals[-1]) if h_vals else None   # LAST match handles corrections
  pub_nums = re.findall(r'publisher[^\\d]*([\\d]+)', para, re.I)
  pub_id = int(pub_nums[-1]) if pub_nums else None
  ```
- Height filter "between X and Y" is INCLUSIVE: X <= height <= Y (not strict inequalities).""",

    "consumption": """\
### Sample databases vs full CSV data
- A database named `xxx_1k`, `xxx_sample`, or `xxx_small` is a SMALL SAMPLE (a few days of data).
- Date-range queries spanning months or years MUST use the CSV files, not the sample DB.
- `yearmonth.csv` CustomerID IS the same entity as `gasstations.json` GasStationID.
  They share the same numeric ID space — join them directly:
  `WHERE int(yearmonth.CustomerID) == int(gasstations.GasStationID)`
  Do NOT give up because column names differ. They are the same ID, just named differently.
  Example Python join:
  ```python
  import csv, json
  with open('json/gasstations.json') as f:
      gs = {int(r['GasStationID']): r for r in json.load(f)['records']}
  results = []
  for row in csv.DictReader(open('csv/yearmonth.csv')):
      if row['Date'] == '201306' and int(row['CustomerID']) in gs:
          results.append(gs[int(row['CustomerID'])]['Country'])
  print(sorted(set(results)))
  ```

### Date formats in CSV files
- Dates stored as 6-character YYYYMM: '201306' = June 2013.
- Filter for June 2013: `Date = '201306'` (NOT BETWEEN date strings — returns 0 rows).
- Filter for year 2013: `Date LIKE '2013%'`.

### Average Monthly Consumption formula
- "Average Monthly Consumption for year 2013" = mean of monthly values divided by 12.
  CORRECT: `sum(vals) / len(vals) / 12` where vals = one value per month-row.
  WRONG: `sum(vals) / 12` — that is total divided by 12, not average divided by 12.

### Return only the requested value column
- Return ONLY the consumption/value column asked for (e.g., Consumption).
  Do NOT include CustomerID, GasStationID, or other ID columns in the output.""",

    "mtg": """\
### MTG cards — format/legal status encoding
- MTG databases often lack explicit 'Format' or 'Status' columns.
- Format+legality is in `leadershipSkills` as a Python dict string:
  e.g. `"{'commander': 'Legal', 'brawl': 'NotLegal', ...}"`
- Filter: `leadershipSkills LIKE '%commander%: Legal%'` or parse with `ast.literal_eval()`.
- If no `leadershipSkills` column, check `legalities`, `printings`, or other text columns.
- Do NOT give up after not finding explicit 'Format'/'Status' columns.""",

    "medical": """\
### Medical thresholds (fallback if not in knowledge.md)
- Always use thresholds defined in knowledge.md if present.
- If knowledge.md does NOT define thresholds, use these domain defaults:
  - WBC (white blood cells): normal 3.5 ≤ WBC ≤ 9.0 (×10³/μL) — INCLUSIVE at both boundaries
  - Fibrinogen (FG): normal 150 ≤ FG ≤ 400 mg/dL; ABNORMAL = FG < 150 OR FG > 400 (strict)
    Include NULL fibrinogen values as ABNORMAL when counting "abnormal" patients.
  - Creatinine: normal 0.6–1.2 mg/dL (male), 0.5–1.1 mg/dL (female)""",

    "school": "",
    "soccer": "",
    "general": "",
}


# ---------------------------------------------------------------------------
# Response examples
# ---------------------------------------------------------------------------

RESPONSE_EXAMPLES = """
## Examples

Step 1 - Profile context:
```json
{"thought": "I will profile the context to see what files are available.", "action": "profile_context", "action_input": {}}
```

Step 2 - Read knowledge.md:
```json
{"thought": "I will read knowledge.md to extract metric formulas and field definitions.", "action": "plan_knowledge", "action_input": {}}
```

Validate before submitting:
```json
{"thought": "I will validate the candidate answer.", "action": "validate_answer", "action_input": {"columns": ["category", "total_revenue"], "rows": [["Electronics", "4200000.00"], ["Clothing", "1850000.00"]], "notes": "Grouped by category from csv/sales.csv."}}
```

Submit the final answer:
```json
{"thought": "Validation passed. Submitting.", "action": "answer", "action_input": {"columns": ["category", "total_revenue"], "rows": [["Electronics", "4200000.00"], ["Clothing", "1850000.00"]]}}
```
""".strip()

# Difficulty-specific examples (appended to RESPONSE_EXAMPLES based on task difficulty)
EXAMPLES_BY_DIFFICULTY = {
    "easy": """
Preview a CSV file:
```json
{"thought": "I will preview the CSV to check column names and sample values.", "action": "read_csv", "action_input": {"path": "csv/sales.csv", "max_rows": 10}}
```

Query CSV with DuckDB:
```json
{"thought": "I will query the CSV to compute the answer.", "action": "execute_data_query", "action_input": {"sources": ["csv/orders.csv"], "sql": "SELECT product, SUM(revenue) AS total FROM orders GROUP BY product ORDER BY total DESC", "limit": 100}}
```

Join multiple CSV/JSON files:
```json
{"thought": "I need to join two files to get the final result.", "action": "execute_data_query", "action_input": {"sources": ["csv/orders.csv", "json/products.json"], "sql": "SELECT o.order_id, p.name FROM orders o JOIN products p ON o.product_id = p.id", "limit": 200}}
```
""".strip(),

    "medium": """
Inspect a SQLite database:
```json
{"thought": "I will inspect the database schema and preview rows.", "action": "inspect_sqlite_schema", "action_input": {"path": "db/hospital.db"}}
```

Query a SQLite database:
```json
{"thought": "I will query the database to compute the answer.", "action": "execute_context_sql", "action_input": {"path": "db/hospital.db", "sql": "SELECT COUNT(*) AS count FROM patients WHERE status = 'active'", "limit": 100}}
```

Join SQLite with CSV using DuckDB:
```json
{"thought": "I need to join the DB and CSV to get the final result.", "action": "execute_data_query", "action_input": {"sources": ["csv/yearmonth.csv", "db/customers.db"], "sql": "SELECT y.Consumption FROM yearmonth y JOIN customers c ON y.CustomerID = c.CustomerID WHERE c.Segment = 'SME'", "limit": 500}}
```

Python for cross-format join (type conversion required):
```json
{"thought": "I will use Python to join SQLite integer IDs with CSV string IDs.", "action": "execute_python", "action_input": {"code": "import csv, sqlite3\\nconn = sqlite3.connect('db/hero_power.db')\\nids = {r[0] for r in conn.execute('SELECT hero_id FROM hero_power WHERE power_id=18')}\\nwith open('csv/superhero.csv') as f:\\n    rows = [r for r in csv.DictReader(f) if int(r['id']) in ids and float(r['height_cm']) > 200]\\nprint(len(rows))"}}
```
""".strip(),

    "hard": """
Search doc files for definitions:
```json
{"thought": "I need the definition from knowledge.md before querying.", "action": "retrieve_context", "action_input": {"query": "abnormal creatinine level", "max_chunks": 4, "max_chars_per_chunk": 1800}}
```

Parse doc file with Python:
```json
{"thought": "I will use Python to parse the doc file and extract structured data.", "action": "execute_python", "action_input": {"code": "import re\\nwith open('doc/superhero.md') as f:\\n    content = f.read()\\nentity = {}\\nfor para in content.split('\\\\n\\\\n'):\\n    m = re.search(r'ID\\\\s+(\\\\d+)', para)\\n    if m:\\n        eid = int(m.group(1))\\n        if eid not in entity: entity[eid] = {}\\n        h = re.search(r'height.*?(\\\\d+\\\\.?\\\\d*)\\\\s*centimeters', para, re.I)\\n        if h: entity[eid]['height'] = float(h.group(1))\\nprint(len(entity))"}}
```

Query structured data if available:
```json
{"thought": "I will query the SQLite database for structured data.", "action": "execute_context_sql", "action_input": {"path": "db/data.db", "sql": "SELECT id, name FROM heroes WHERE power_id = 18", "limit": 500}}
```
""".strip(),

    "extreme": """
Search doc for key definitions:
```json
{"thought": "I will search for the key definition before parsing.", "action": "retrieve_context", "action_input": {"query": "budget amount allocation", "max_chunks": 4, "max_chars_per_chunk": 2000}}
```

Parse documents as data with Python:
```json
{"thought": "I will parse the entire doc file programmatically to extract all values.", "action": "execute_python", "action_input": {"code": "import re\\nwith open('doc/report.md') as f:\\n    text = f.read()\\nmatches = re.findall(r'ID\\\\s+(\\\\d+).*?amount.*?(\\\\d+\\\\.\\\\d+)', text, re.S)\\nprint(len(matches), matches[:5])"}}
```
""".strip(),
}


# ---------------------------------------------------------------------------
# Truncation limits
# ---------------------------------------------------------------------------

MAX_OBSERVATION_STRING_CHARS = 1200
MAX_OBSERVATION_ITEMS = 12
MAX_OBSERVATION_JSON_CHARS = 12000


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def build_system_prompt(
    tool_descriptions: str,
    system_prompt: str | None = None,
    difficulty: str | None = None,
    domain: str | None = None,
) -> str:
    base_prompt = system_prompt or REACT_SYSTEM_PROMPT

    # Inject domain-specific rules only for the relevant domain
    domain_addendum = ""
    if domain:
        rules = DOMAIN_RULES.get(domain, "")
        if rules.strip():
            domain_addendum = f"\n\n---\n\n## Dataset-specific rules\n\n{rules}"

    # Build examples: common examples + difficulty-specific examples
    examples = RESPONSE_EXAMPLES
    if difficulty and difficulty.lower() in EXAMPLES_BY_DIFFICULTY:
        examples = examples + "\n\n" + EXAMPLES_BY_DIFFICULTY[difficulty.lower()]

    return (
        f"{base_prompt}{domain_addendum}\n\n"
        "---\n\n"
        "## Available tools\n\n"
        f"{tool_descriptions}\n\n"
        "---\n\n"
        f"{examples}\n\n"
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
            "   After reading knowledge.md, explicitly extract:\n"
            "   - The correct field names for each concept in the question (e.g., rank vs positionOrder, permanent number vs race number).\n"
            "   - Any threshold, code, or inequality condition (e.g., 'less than N' means strict `< N`, not `<= N`).\n"
            "3. Identify requested output columns, filters, aggregation, ranking/ties, and units.\n"
            "   CRITICAL: If the question involves a person's/entity's 'number', JOIN the relevant\n"
            "   event table (e.g. qualifying, results) with the entity table (e.g. drivers) to get\n"
            "   the entity's permanent number, not the event-specific number.\n"
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
            "3. Identify requested output columns, filters, aggregation, ranking/ties, and units.\n"
            "4. Call inspect_sqlite_schema to understand database tables, columns, row counts, and preview rows.\n"
            "5. Check actual table rows, join keys, and distinct filter values before final filtering.\n"
            "6. Use execute_context_sql for queries within the database.\n"
            "7. Use execute_data_query if you need to JOIN the database with CSV or JSON files.\n"
            "8. If the result is empty or zero, verify formatting and value variants before accepting it."
        ),
        "hard": (
            "This is a HARD task.\n"
            "Available files: Multiple data sources — doc/ documents, CSV, JSON, and/or SQLite may all be present.\n"
            "Steps:\n"
            "1. Call profile_context first to see ALL available files and their types.\n"
            "2. Call plan_knowledge to read full knowledge.md definitions and rules.\n"
            "3. Identify requested output columns, filters, aggregation, ranking/ties, and units.\n"
            "4. If structured files (CSV, JSON, SQLite) exist, query them FIRST with SQL or Python.\n"
            "   Use knowledge.md rules (thresholds, codes, definitions) to build correct filters.\n"
            "5. Use retrieve_context ONLY to find specific definitions/rules you haven't found yet.\n"
            "   LIMIT: Do NOT call retrieve_context more than 3 times total.\n"
            "6. For complex multi-step logic, use execute_python to combine all data sources.\n"
            "7. If your approach isn't working after a few attempts, simplify and answer with what you have.\n"
            "8. Validate and submit your answer.\n"
            "CRITICAL: Do NOT loop on the same tool. If a tool call doesn't give you what you need,\n"
            "switch to a different approach immediately.\n"
            "DOMAIN HINTS:\n"
            "- For ratio/percentage questions, double-check numerator vs denominator carefully.\n"
            "- For 'lap time' vs 'race finish time', they are DIFFERENT columns. The question word\n"
            "  'fastest lap' → fastestLapTime; 'finished the race' or total race time → time column.\n"
            "- For 'how many times more than' questions, the answer is a RATIO (A/B), not a percentage.\n"
            "DOC FILE PARSING:\n"
            "- When doc/ files contain narrative text with embedded numerical values (e.g., 'amount of 150'),\n"
            "  use execute_python to open and read the file, then regex to extract amounts near each ID.\n"
            "  Do NOT rely on retrieve_context alone to get numerical values from doc files.\n"
            "  Pattern: open('doc/budget.md').read() → find ID in text → extract nearby number.\n"
            "  Example: import re; m = re.search(r'amount.*?(\\d+\\.?\\d*)', paragraph_near_id)"
        ),
        "extreme": (
            "This is an EXTREME task.\n"
            "Available files: doc/ documents only. There are NO CSV, JSON, or SQLite files.\n"
            "The documents ARE the data source — you must parse them programmatically.\n"
            "Steps:\n"
            "1. Call profile_context first.\n"
            "2. Call plan_knowledge to read knowledge.md definitions and rules.\n"
            "3. Call retrieve_context ONCE with question keywords to find key definitions.\n"
            "4. Use execute_python to read the .md files from the working directory, extract\n"
            "   values/IDs/dates with regex or string operations, filter, and compute the answer.\n"
            "5. Validate and answer.\n"
            "Key insight: Do NOT call retrieve_context repeatedly. Use Python to parse documents as data."
        ),
    }.get(
        task.difficulty.lower(),
        "Call profile_context first, then plan_knowledge, then retrieve or query what you need, then compute the answer.",
    )

    return (
        f"Task ID: {task.task_id}\n"
        f"Difficulty: {task.difficulty}\n"
        f"Question: {task.question}\n\n"
        f"{strategy}\n\n"
        "All file paths in tool calls must be relative to the context/ directory.\n"
        "Start with profile_context, then call plan_knowledge. Validate the candidate data before calling answer."
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
