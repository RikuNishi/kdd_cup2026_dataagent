# ツール詳細リファレンス

各ツールの具体的な内部処理・入出力・制約をまとめます。

---

## 実行フェーズ別ツールマップ

```
profile_context
    └─→ plan_knowledge
            └─→ retrieve_context   ← Hard/Extreme で文書から情報収集
            └─→ list_context / read_csv / read_json / read_doc
            └─→ inspect_sqlite_schema
                    └─→ execute_context_sql
            └─→ execute_data_query  ← CSV/JSON/SQLite 横断 JOIN
            └─→ execute_python      ← 柔軟な集計・パース
                    └─→ validate_answer
                            └─→ answer
```

---

## 1. `profile_context`

**入力：** なし  
**ソース：** `tools/context_profile.py` → `build_context_profile()`

### 処理内容

`context/` 配下を全走査し、ファイル種別ごとにプロファイルを構築する。
`context/knowledge.md` **は除外**する（`plan_knowledge` が担当）。

| ファイル種別 | 処理 |
|---|---|
| `.csv` | header・行数・先頭3行プレビューを取得 |
| `.db / .sqlite / .sqlite3` | `PRAGMA table_info` でカラム型・行数を取得、CREATE 文も含む |
| `.json` | 10MB 以下は全ロードして top-level 型・キーを返す。超過時は先頭 4096 文字から正規表現でキーを推定 |
| `.md / .txt` | Markdown 見出しを最大 30 件抽出 + 先頭 512 文字プレビュー |

難易度と doc/db の有無から **strategy 文字列**も生成して返す：

- `"retrieve-first: ..."` → Hard/Extreme またはドキュメントあり
- `"schema-first: ..."` → DB あり
- `"python-first: ..."` → CSV/JSON のみ

### 返り値の主要フィールド

```json
{
  "task_id": "...",
  "difficulty": "hard",
  "question": "...",
  "modalities": ["csv", "db", "doc"],
  "files": [...],
  "strategy": "retrieve-first: ..."
}
```

---

## 2. `plan_knowledge`

**入力：** なし  
**ソース：** `tools/context_profile.py` → `plan_knowledge_needs()`

### 処理内容

1. `build_context_profile()` を内部で呼び、ファイル一覧の要約（最大 12 ファイル、各 12 カラム）を作る
2. `context/knowledge.md` を全文読み込みそのまま返す
3. `knowledge.md` が存在しない場合は `knowledge_exists: false` と代替ステップを返す

### 返り値の主要フィールド

```json
{
  "knowledge_exists": true,
  "knowledge_content": "（knowledge.md の全文）",
  "knowledge_char_count": 3200,
  "profile_summary": {
    "modalities": ["csv", "db"],
    "sources": [{"path": "...", "type": "csv", "columns": [...]}]
  },
  "recommended_next_steps": [...]
}
```

---

## 3. `retrieve_context`

**入力：** `query`, `keywords?`, `max_chunks=5`, `max_chars_per_chunk=2000`  
**ソース：** `tools/context_profile.py` → `retrieve_context_chunks()`

### 処理内容

**ベクトル検索ではなく語彙一致（bag-of-words）スコアリング**。

1. `query` と `keywords` を結合してトークン化（3文字以上の英数字、ストップワード除外）
2. `context/` 配下の `.md` / `.txt` を全走査
3. 各ファイルを **Markdown 見出し単位でチャンク分割**（見出しをまたぐ場合は `chunk_chars` で固定長分割）
4. `score = len(query_tokens & chunk_tokens)` で一致単語数を算出
5. スコア降順で上位 `max_chunks` 件を返す（スコア 0 は除外）

### 制限・注意点

- 英語トークンのみ有効。日本語・略語は一致しない
- クエリ単語が文書に**そのまま出現しない**と score=0 で取れない
- 同じ単語が多い chunk が優先されるため、関係ない chunk が混入する場合がある
- プロンプト側で「最大3回まで」と制限されている

### 返り値

```json
{
  "query": "abnormal creatinine level",
  "keywords": ["abnormal", "creatinine", "level"],
  "searched_paths": ["knowledge.md", "doc/lab_values.md"],
  "chunks": [
    {"path": "knowledge.md", "heading": "Lab Values", "score": 3, "text": "..."}
  ]
}
```

---

## 4. `list_context`

**入力：** `max_depth=4`  
**ソース：** `tools/filesystem.py` → `list_context_tree()`

### 処理内容

`context/` 配下を `max_depth` 深さまで再帰列挙する。ファイルは name 昇順、ディレクトリを先に並べる（`key=(is_file, name)`）。

### 返り値

```json
{
  "root": "/path/to/context",
  "entries": [
    {"path": "doc/", "kind": "dir", "size": null},
    {"path": "doc/patients.md", "kind": "file", "size": 14200}
  ]
}
```

---

## 5. `read_csv`

**入力：** `path`, `max_rows=20`  
**ソース：** `tools/filesystem.py` → `read_csv_preview()`

### 処理内容

- `context/` 相対パスを解決してパストラバーサルを防ぐ
- `csv.reader` でヘッダー行と全データ行を読み込み、先頭 `max_rows` 行のみ返す
- 総行数（`row_count`）は先頭カットとは別に全行数を返す

---

## 6. `read_json`

**入力：** `path`, `max_chars=4000`  
**ソース：** `tools/filesystem.py` → `read_json_preview()`

### 処理内容

- JSON を `json.loads` → `json.dumps(indent=2)` で整形
- 整形後の文字列を `max_chars` で切り詰めて返す
- `truncated: true/false` を付与

---

## 7. `read_doc`

**入力：** `path`, `max_chars=4000`, `offset=0`  
**ソース：** `tools/filesystem.py` → `read_doc_preview()`

### 処理内容

- テキストファイルの `offset` 文字目から `max_chars` 文字を読み込む
- 不正バイトは `errors="replace"` で置換
- `truncated: true/false`、`offset`、`total_chars` を付与

### ページネーション

大きなドキュメントを全文取得する場合は `offset` をずらして繰り返し呼ぶ:

```
read_doc(path, offset=0)       # 先頭 4000 文字
read_doc(path, offset=4000)    # 次の 4000 文字
...
# truncated: false になるまで続ける
```

---

## 8. `inspect_sqlite_schema`

**入力：** `path`  
**ソース：** `tools/sqlite.py` → `inspect_sqlite_schema()`

### 処理内容

SQLite ファイルを **読み取り専用 URI** (`?mode=ro`) で接続。

1. `sqlite_master` からユーザー定義テーブル一覧と CREATE 文を取得
2. `PRAGMA table_info` でカラム名・型・NOT NULL・DEFAULT・PK フラグを取得
3. 各テーブルの行数を `COUNT(*)` で取得
4. 先頭 5 行のプレビューを取得

### 返り値

```json
{
  "tables": [{
    "name": "patients",
    "create_sql": "CREATE TABLE ...",
    "columns": [{"name": "id", "type": "INTEGER", "primary_key": true}],
    "row_count": 450,
    "preview_rows": [[1, "Alice", ...]]
  }]
}
```

---

## 9. `execute_context_sql`

**入力：** `path`, `sql`, `limit=200`  
**ソース：** `tools/sqlite.py` → `execute_read_only_sql()`

### 処理内容

- 読み取り専用 URI (`?mode=ro`) で接続
- SQL 先頭が `SELECT` / `WITH` / `PRAGMA` のいずれかのみ許可（それ以外は `ValueError`）
- `fetchmany(limit + 1)` で件数制限。`limit+1` 件取れた場合は `truncated: true` を返す

### 制限

- `.db / .sqlite / .sqlite3` 専用。CSV/JSON には使えない
- **SQLite 単体 DB 内のみ**有効。複数ファイル JOIN は `execute_data_query` を使う

---

## 10. `execute_data_query`

**入力：** `sources?`, `sql`, `limit=200`  
**ソース：** `tools/data_query.py` → `execute_data_query()`

### 処理内容

CSV / JSON / SQLite を **DuckDB** に登録して横断 SQL を実行する。

| ファイル種別 | DuckDB への登録方法 |
|---|---|
| `.csv` | `read_csv_auto()` で VIEW として登録。alias はファイル名（拡張子なし） |
| `.json` | `records` キーまたは配列を pandas DataFrame 化 → `conn.register()` で登録。`table` キーがあればそれを natural alias にも登録 |
| `.sqlite / .db` | `ATTACH ... (TYPE SQLITE, READ_ONLY)` で schema として attach。テーブルが 1 つの場合は view alias も作成 |

`sources` が省略された場合は `context/` 配下の全 structured ファイルを登録する。

SQL は `SELECT` / `WITH` / `PRAGMA` / `DESCRIBE` / `SHOW` のみ許可。`limit` 件超は `truncated: true`。

### 返り値の主要フィールド

```json
{
  "registered_sources": [
    {"relative_path": "csv/members.csv", "type": "csv", "alias": "members", "columns": [...]}
  ],
  "columns": ["name", "score"],
  "rows": [["Alice", 92]],
  "row_count": 1,
  "truncated": false
}
```

### 注意

- `registered_sources` の `alias` / `view_alias` / `aliases` を確認して SQL のテーブル名を決める
- JSON の natural alias はファイル内 `table` キーの値になることがある

---

## 11. `execute_python`

**入力：** `code`  
**ソース：** `tools/python_exec.py` → `execute_python_code()`  
**タイムアウト：** 30秒

### 処理内容

1. `multiprocessing.Process` で**子プロセス**を起動
2. `os.chdir(context_root)` → `context/` がカレントディレクトリになる
3. stdout / stderr を一時ファイルにリダイレクトして `exec(code, namespace)` を実行
4. `timeout_seconds` 以内に完了しなければ `process.kill()`
5. 親プロセスが stdout / stderr を読み戻して返す

`namespace` には `Path` と `context_root`（文字列）があらかじめ渡されている。

### 返り値

```json
{
  "success": true,
  "output": "42\n",
  "stderr": "",
  "elapsed_seconds": 0.8
}
```

### 注意

- タイムアウト 30 秒。重い処理（大規模ファイル全ロード等）は分割する
- `print()` した内容が `output` に入る。戻り値は取れない

---

## 12. `validate_answer`

**入力：** `columns`, `rows`, `notes`  
**ソース：** `tools/answer_validation.py` → `validate_answer_table()`

### 処理内容

`answer` を呼ぶ前の事前検査。**errors**（致命的）と **warnings**（リスク警告）を分けて返す。

| チェック項目 | エラー or 警告 |
|---|---|
| `columns` が非空リストでない | error |
| `rows` がリストでない | error |
| 行のセル数がカラム数と不一致 | error |
| 重複カラム名 | warning |
| 行数が 0 | warning |
| スカラー質問なのにカラム数 > 2 | warning |
| 複数行質問なのに行数 = 1 | warning |
| tie/highest/lowest 表現があり行数 = 1 | warning |
| 数値が科学表記（`e`）を含む | warning |
| 全数値が 0 | warning |
| 名前系質問で空白結合らしい値 | warning |
| `notes` が空 or ソース証拠なし | warning |

`ok: true` かつ `ready_for_answer: true` でエラーがなければ `answer` に進める。

---

## 13. `answer`

**入力：** `columns`, `rows`  
**ソース：** `tools/registry.py` → `_answer()`

### 処理内容

1. `columns` が非空文字列リストであることを検証（`ValueError` で即終了）
2. `rows` が各行ともリストかつカラム数一致を検証
3. `AnswerTable(columns, rows)` を構築して `ToolExecutionResult(is_terminal=True)` を返す
4. ReAct ループが `is_terminal=True` を検知してループを終了する

### 注意

- このツールを呼ばないまま `max_steps` に達すると `failure_reason` がセットされ **0 点**
- `validate_answer` でエラーがある状態で `answer` を呼ぶと `ValueError` が発生してステップ失敗になる
