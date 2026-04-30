# エージェント処理フローのメモ

## このエージェントの役割

このリポジトリの baseline は、KDD Cup 2026 DataAgent-Bench 用の ReAct 型データエージェントです。

各タスクの `task.json` にある質問を読み、同じタスクの `context/` ディレクトリ内にある CSV、JSON、SQLite、ドキュメントをツール経由で調べます。必要な情報を集めたら、最後に `answer` ツールで表形式の回答を提出し、`prediction.csv` と `trace.json` を出力します。

中心になる実装は次のファイルです。

- `src/data_agent_baseline/run/runner.py`: CLI から呼ばれる実行制御、出力保存、タイムアウト管理
- `src/data_agent_baseline/agents/react.py`: ReAct ループ本体
- `src/data_agent_baseline/agents/prompt.py`: モデルに渡すプロンプト作成
- `src/data_agent_baseline/tools/registry.py`: モデルに公開するツールの登録と実行
- `src/data_agent_baseline/agents/runtime.py`: `trace.json` に残る実行結果の構造

## 全体フロー

1. `uv run dabench run-task ...` または `uv run dabench run-benchmark ...` から CLI が起動する。
2. 設定ファイルを読み込み、`dataset.root_path`、`agent.model`、`agent.max_steps`、`run.output_dir` などを確定する。
3. `DABenchPublicDataset` が対象 `task_id` の `task.json` と `context/` を読み、`PublicTask` を作る。
4. `OpenAIModelAdapter` がモデル呼び出しを担当し、`ToolRegistry` が使えるツール一覧を持つ。
5. `ReActAgent.run()` が 1 タスク分の ReAct ループを開始する。
6. モデルが次に実行したいツールを JSON で返す。
7. `ToolRegistry.execute()` が指定されたツールを実行する。
8. ツール結果を observation として保存し、次のモデル入力に戻す。
9. `answer` ツールが呼ばれるか、`agent.max_steps` に到達するまで繰り返す。
10. 実行結果を `trace.json` に保存し、回答がある場合は `prediction.csv` も保存する。
11. `run-benchmark` の場合は、全タスク完了後に `summary.json` も保存する。

## 1 ステップの中で起きること

`ReActAgent.run()` の 1 ステップは、だいたい次の順序です。

1. `_build_messages()` が system prompt、task prompt、過去ステップの observation を会話履歴にまとめる。
2. `model.complete()` がモデルに問い合わせる。
3. モデルは `thought`, `action`, `action_input` を持つ JSON を返す。
4. `parse_model_step()` がモデル応答を JSON として解釈し、実行する action を取り出す。
5. `tools.execute()` が `action` に対応するツールを実行する。
6. ツール結果を `observation` として `StepRecord` に保存する。
7. `action` が `answer` なら `state.answer` に最終回答を入れて終了する。
8. JSON parse 失敗、未知ツール、ツール実行エラーなどが起きた場合は `action == "__error__"` のステップとして記録し、次のステップで修復を促す。

つまり、モデルは直接ファイルを読むのではなく、「次にどのツールをどう呼ぶか」を毎回 JSON で決めます。実際のファイルアクセスや SQL 実行、Python 実行はツール側が担当します。

## プロンプト構成

プロンプトは `src/data_agent_baseline/agents/prompt.py` で作られます。大きく 3 種類あります。

### system prompt

エージェント全体のルールをモデルに伝えます。主な内容は次の通りです。

- `context/` 内の情報だけをツール経由で調べる。
- 観測できた情報だけを根拠に回答する。
- タスク完了には必ず `answer` ツールを呼ぶ。
- 応答は必ず 1 個の JSON object にする。
- JSON には `thought`, `action`, `action_input` を含める。
- `action_input` は必ず JSON object にする。
- `execute_python` を使う場合は、Python コードを `action_input.code` に入れる。
- JSON の前後に余計な文章を出さない。

この system prompt には、`ToolRegistry.describe_for_prompt()` が作った利用可能ツールの説明も追加されます。

### task prompt

タスクごとの質問文をモデルに渡します。

内容はシンプルで、`task.json` の `question` と、ツールに渡すファイルパスは `context/` からの相対パスにするという注意を含みます。

### observation prompt

各ステップでツールを実行したあと、その結果を次のモデル入力に戻すためのプロンプトです。

例えば `read_csv` の結果なら列名、先頭行、行数が入ります。`execute_python` の結果なら `success`, `output`, `stderr` が入ります。モデルはこの observation を見て、次の action を決めます。

## モデルに要求している応答形式

モデルの応答は、基本的に次の JSON 形式です。

```json
{
  "thought": "次に何をするかの短い理由",
  "action": "list_context",
  "action_input": {
    "max_depth": 4
  }
}
```

`thought` はモデルの簡潔な意図、`action` は使うツール名、`action_input` はツールに渡す引数です。

最終回答するときは `answer` ツールを使います。

```json
{
  "thought": "必要な結果がそろったので回答する。",
  "action": "answer",
  "action_input": {
    "columns": ["column_name"],
    "rows": [["value_1"]]
  }
}
```

`answer.columns` は列名の配列、`answer.rows` は行の配列です。ここで提出された内容が `prediction.csv` に書き出されます。

## 使えるツール

標準ツールは `src/data_agent_baseline/tools/registry.py` の `create_default_tool_registry()` で定義されています。

- `list_context`: `context/` 配下のファイルとディレクトリを列挙する。
- `read_csv`: CSV のヘッダー、先頭行、総行数を確認する。
- `read_json`: JSON ファイルをプレビューする。
- `read_doc`: Markdown やテキストドキュメントをプレビューする。
- `inspect_sqlite_schema`: SQLite / DB ファイルのテーブル定義を確認する。
- `execute_context_sql`: SQLite / DB ファイルに読み取り専用 SQL を実行する。
- `execute_python`: `context/` を作業ディレクトリとして Python コードを実行する。
- `answer`: 最終回答テーブルを提出し、タスクを終了する。

ツールに渡すファイルパスは、すべてタスクの `context/` ディレクトリからの相対パスです。

## 出力ファイル

通常実行では、タスクごとに次のような出力が作られます。

```text
artifacts/runs/<run_id>/<task_id>/
├── trace.json
└── prediction.csv
```

- `trace.json`: モデルが何を考え、どのツールを呼び、どんな結果を得たかを記録した実行ログ。
- `prediction.csv`: `answer` ツールで提出された最終回答。回答がない場合は作られない。
- `summary.json`: `run-benchmark` のときに run 全体の成功数、タスク数、各タスクの出力パスを記録する。

提出用の `submit-run` では、`prediction.csv` は `/output/<task_id>/` 側、`trace.json` は `/logs/<task_id>/` 側に分けて保存されます。

## `trace.json` の見方

`trace.json` は、各タスクでエージェントがどのように回答に到達したかを確認するためのログです。まずトップレベルを見て、次に `steps` を順に追うと読みやすいです。

### まず見る項目

- `succeeded`: タスクが成功扱いかどうか。`true` なら `answer` が提出され、失敗理由がない。
- `failure_reason`: 失敗理由。成功時は `null`。`answer` なしで `max_steps` に到達した場合や、タイムアウト時に理由が入る。
- `answer`: 最終回答。`columns` と `rows` が入る。これが `prediction.csv` の元データ。
- `e2e_elapsed_seconds`: タスク全体の実行時間。
- `steps`: ReAct の各ステップの詳細ログ。

最初は `succeeded` と `failure_reason` を確認します。次に `answer` が期待する列・行になっているかを見ます。なぜその回答になったかを調べたい場合に `steps` を上から順に読みます。

### `steps` 内の項目

各 step は 1 回の「モデル応答 + ツール実行」に対応します。

- `step_index`: 何ステップ目か。
- `thought`: モデルがその action を選んだ理由。
- `action`: 実行したツール名。
- `action_input`: ツールに渡した入力。
- `raw_response`: モデルが返した生の文字列。
- `observation`: ツール実行結果。次ステップのプロンプトに戻される内容。
- `ok`: そのステップのツール実行が成功したか。

例えば `action == "list_context"` の step では、`observation.content.entries` に利用可能なファイル一覧が入ります。`action == "read_csv"` の step では、`observation.content.columns` や `observation.content.rows` でプレビューを確認できます。`action == "execute_python"` の step では、`observation.content.output` に Python の標準出力、`observation.content.stderr` に標準エラーが入ります。

### 失敗調査の見方

失敗時は次の順で確認します。

1. トップレベルの `failure_reason` を見る。
2. `steps` の最後の方で `ok: false` の step を探す。
3. `action == "__error__"` の step があれば、モデル応答の JSON 形式や action protocol が壊れていた可能性が高い。
4. `observation.error` と `observation.error_type` で直接のエラー内容を確認する。
5. `raw_response` または `observation.invalid_response_preview` で、モデルが実際に何を返したか確認する。
6. ツール自体の失敗なら、該当 step の `action_input` と `observation.content` を見て、ファイルパス、SQL、Python コード、出力を確認する。

`trace.json` は、単なる結果ファイルではなく「モデルがどの情報を見て、どの判断で回答したか」を追跡するためのデバッグログです。回答の妥当性を見るときは、`answer` だけでなく、その直前の `execute_python` や `execute_context_sql` の `observation` まで確認すると原因を追いやすくなります。
