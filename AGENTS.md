# AGENTS.md

このリポジトリは KDD Cup 2026 DataAgent-Bench のスターターキットです。公開データセットを読み込み、各タスクの予測結果を `artifacts/runs/` に出力します。

## 基本方針

- 変更前に既存実装を確認し、現在の構成と命名に合わせる。
- 不要なリファクタリングや大きな設計変更は避け、依頼範囲に絞って変更する。
- 既存のユーザー変更を巻き戻さない。
- Python 実装では型ヒントと簡潔な docstring を保つ。
- ファイルパスは原則としてプロジェクトルートからの相対パスで扱う。

## 主要コマンド

```bash
uv sync
uv run dabench status --config configs/react_baseline.example.yaml
uv run dabench inspect-task task_1 --config configs/react_baseline.example.yaml
uv run dabench run-task task_1 --config configs/react_baseline.example.yaml
uv run dabench run-benchmark --config configs/react_baseline.example.yaml
```

`run-benchmark` は `--limit N` で実行タスク数を制限できます。

## データと出力

- 入力データ: `data/public/input/`
- 公開デモの正解: `data/public/output/task_<id>/gold.csv`
- タスク構成: `task.json` と `context/`
- 出力先: `artifacts/runs/<run_id>/<task_id>/`
- 主な出力: `trace.json`, `prediction.csv`, `summary.json`

隠しテストでは `output/` は存在しない前提で実装してください。

## 設定

設定例は `configs/react_baseline.example.yaml` にあります。

主な項目:

- `dataset.root_path`: 入力データセットのルート
- `agent.model`: 使用モデル名
- `agent.api_base`: OpenAI 互換 API のベース URL
- `agent.api_key`: API キー
- `agent.max_steps`: タスクごとの最大 ReAct ステップ数
- `run.output_dir`: 実行結果の出力先
- `run.max_workers`: ベンチマーク実行時の並列数
- `run.task_timeout_seconds`: タスク単位のタイムアウト秒数

## 主要モジュール

- `src/data_agent_baseline/benchmark/dataset.py`: 公開データセットの読み込み
- `src/data_agent_baseline/tools/filesystem.py`: `context/` 内ファイルの一覧・読み込み
- `src/data_agent_baseline/tools/sqlite.py`: SQLite スキーマ確認と読み取り専用 SQL
- `src/data_agent_baseline/tools/python_exec.py`: `context/` 内での Python 実行
- `src/data_agent_baseline/tools/registry.py`: ツール登録と `answer` 処理
- `src/data_agent_baseline/agents/prompt.py`: プロンプト生成
- `src/data_agent_baseline/agents/react.py`: ReAct 実行ループ
- `src/data_agent_baseline/run/runner.py`: 単一タスク・ベンチマーク実行

## ツール仕様

モデルに公開されるツールは `src/data_agent_baseline/tools/registry.py` で定義されています。

- `list_context`: `context/` 配下のファイル一覧
- `read_csv`: CSV プレビュー
- `read_json`: JSON プレビュー
- `read_doc`: テキスト文書プレビュー
- `inspect_sqlite_schema`: SQLite スキーマ確認
- `execute_context_sql`: 読み取り専用 SQL 実行
- `execute_python`: Python コード実行
- `answer`: 最終回答の提出

ツールに渡すファイルパスは、すべてタスクの `context/` ディレクトリからの相対パスにしてください。

## 検証

変更後は影響範囲に応じて、少なくとも以下のいずれかを実行してください。

```bash
python3 -m compileall src/data_agent_baseline
uv run dabench status --config configs/react_baseline.example.yaml
uv run dabench run-task task_1 --config configs/react_baseline.example.yaml
```
