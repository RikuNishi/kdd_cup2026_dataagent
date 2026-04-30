<div align="center">

# DataAgent-Bench Starter Kit

日本語 | [中文](README.zh.md)

[![Official Website](https://img.shields.io/badge/Official%20Website-Visit%20dataagent.top-0ea5e9?style=for-the-badge&logo=googlechrome&logoColor=white&labelColor=0f172a)](https://dataagent.top)
[![Demo Dataset](https://img.shields.io/badge/Demo%20Dataset-Download%20Phase%201-f59e0b?style=for-the-badge&logo=googledrive&logoColor=white&labelColor=0f172a)](https://drive.google.com/file/d/1c6u5WlFw4KV7CBRyXh5BvFYbKqxhBSbL/view)
[![Discord](https://img.shields.io/badge/Discord-Join%20Community-5865F2?style=for-the-badge&logo=discord&logoColor=white&labelColor=0f172a)](https://discord.com/invite/7eFwJQN3Fx)

</div>

> KDD Cup 2026 DataAgent-Bench チャレンジの公式スターターキットです。このリポジトリは `data/public/input/` からタスクを読み込み、評価用の予測結果を書き出します。

## 概要

| 項目 | 内容 |
| --- | --- |
| データセット入力 | `data/public/input/` |
| 公開デモの正解 | `data/public/output/task_<id>/gold.csv` |
| 隠しテストデータ | `input/` のみ。`output/` は含まれません |
| 実行コマンド | `uv run dabench <command> --config PATH` |
| デフォルト出力先 | `artifacts/runs/` |

## クイックスタート

1. 公式ガイドに従って `uv` をインストールします。
   - https://docs.astral.sh/uv/getting-started/installation/
2. macOS / Linux では、次の standalone installer を利用できます。

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

3. プロジェクト依存関係をインストールします。

   ```bash
   uv sync
   ```

4. データセットルートが参照できることを確認します。

   ```bash
   uv run dabench status --config configs/react_baseline.example.yaml
   ```

5. baseline を実行します。

   ```bash
   uv run dabench run-benchmark --config configs/react_baseline.example.yaml
   ```

## データセット

公開デモデータセットは `data/public/input/` にあります。各タスクディレクトリは次の構成です。

```text
data/public/input/task_<id>/
├── task.json
└── context/
```

公開デモの正解ファイルは `data/public/output/task_<id>/gold.csv` にあります。
隠しテストセットには `input/` のみが含まれ、`output/` ディレクトリはありません。

`task.json` には次の項目が含まれます。

- `task_id`
- `difficulty`
- `question`

`context/` には、次のようなファイルが 1 つ以上含まれます。

- CSV ファイル
- JSON ファイル
- SQLite / DB ファイル
- テキストドキュメント

## 設定

設定例は `configs/react_baseline.example.yaml` にあります。

```yaml
dataset:
  root_path: data/public/input

agent:
  model: YOUR_MODEL_NAME
  api_base: YOUR_API_BASE_URL
  api_key: YOUR_API_KEY
  max_steps: 16
  temperature: 0.0

run:
  output_dir: artifacts/runs
  run_id:
  max_workers: 4
  task_timeout_seconds: 600
```

設定項目:

| 項目 | 説明 |
| --- | --- |
| `dataset.root_path` | 公開デモ `input/` データセットのルート。相対パスはプロジェクトルートから解決されます。 |
| `agent.model` | モデル名。 |
| `agent.api_base` | OpenAI 互換 API のベース URL。 |
| `agent.api_key` | API キー。設定ファイルから直接読み込まれます。 |
| `agent.max_steps` | 1 タスクあたりの最大 ReAct ステップ数。 |
| `agent.temperature` | サンプリング温度。 |
| `run.output_dir` | 実行成果物の出力ディレクトリ。 |
| `run.run_id` | 任意の実行ディレクトリ名。省略時は UTC タイムスタンプ。単一ディレクトリ名のみ指定でき、既存ディレクトリは拒否されます。 |
| `run.max_workers` | `run-benchmark` の並列 worker 数。 |
| `run.task_timeout_seconds` | 1 タスクあたりの最大実行時間。`0` 以下にするとタスク単位のタイムアウトを無効化します。 |

## CLI

```bash
uv run dabench <command> --config PATH [options]
```

| コマンド | 目的 | 例 |
| --- | --- | --- |
| `status` | プロジェクトパス、設定ファイル、データセットルート、公開タスク数を表示します。 | `uv run dabench status --config configs/react_baseline.example.yaml` |
| `inspect-task` | タスクのメタデータと `context/` 配下のアクセス可能なファイルを表示します。 | `uv run dabench inspect-task task_1 --config configs/react_baseline.local.yaml` |
| `run-task` | 1 つのタスクで baseline を実行し、結果を書き出します。 | `uv run dabench run-task task_1 --config configs/react_baseline.local.yaml` |
| `run-benchmark` | 公開データセット全体で baseline を実行します。 | `uv run dabench run-benchmark --config configs/react_baseline.local.yaml` |

`run-benchmark` は `--limit N` により実行タスク数を制限できます。

## ツール

baseline はモデルに次のツールを公開します。

| ツール | 目的 | 入力 |
| --- | --- | --- |
| `list_context` | `context/` 配下のファイルとディレクトリを列挙します。 | `max_depth` |
| `read_csv` | CSV のプレビューを読み込みます。 | `path`, `max_rows` |
| `read_json` | JSON のプレビューを読み込みます。 | `path`, `max_chars` |
| `read_doc` | テキストドキュメントのプレビューを読み込みます。 | `path`, `max_chars` |
| `inspect_sqlite_schema` | SQLite / DB ファイルのテーブルを確認します。 | `path` |
| `execute_context_sql` | `context/` 内の SQLite / DB ファイルに対して読み取り専用 SQL を実行します。 | `path`, `sql`, `limit` |
| `execute_python` | タスクの `context/` ディレクトリ内で任意の Python コードを実行します。 | `code` |
| `answer` | 最終回答テーブルを提出し、タスクを終了します。 | `columns`, `rows` |

ツールに渡すファイルパスは、すべてタスクの `context/` ディレクトリからの相対パスで指定します。

## 出力

タスクが成功すると、次のファイルが生成されます。

- `trace.json`
- `prediction.csv`

タスクごとの出力先:

```text
artifacts/runs/<run_id>/<task_id>/
├── trace.json
└── prediction.csv
```

ベンチマーク実行時は、次のファイルも生成されます。

```text
artifacts/runs/<run_id>/summary.json
```

## 連絡先

- Issue: https://github.com/HKUSTDial/kddcup2026-data-agents-starter-kit/issues
- 公式サイト: https://dataagent.top
- Discord: https://discord.com/invite/7eFwJQN3Fx
- WeChat 公式アカウント: `数据智能与分析实验室 DIAL`

<div align="center">
  <table>
    <tr>
      <td align="center">
        <a href="https://dataagent.top">
          <img
            src="https://api.qrserver.com/v1/create-qr-code/?size=144x144&data=https://dataagent.top&bgcolor=ffffff&color=111827&margin=8"
            alt="Official website QR code"
            width="144"
          />
        </a>
        <br />
        公式サイト
      </td>
      <td align="center">
        <a href="https://discord.com/invite/7eFwJQN3Fx">
          <img
            src="https://api.qrserver.com/v1/create-qr-code/?size=144x144&data=https://discord.com/invite/7eFwJQN3Fx&bgcolor=ffffff&color=111827&margin=8"
            alt="Discord QR code"
            width="144"
          />
        </a>
        <br />
        Discord
      </td>
      <td align="center">
        <img
          src="https://dataagent.top/HKUSTGZ_DIAL.jpg"
          alt="WeChat official account QR code"
          width="144"
        />
        <br />
        WeChat 公式アカウント
      </td>
    </tr>
  </table>
</div>

## 主要モジュール

| モジュール | 役割 |
| --- | --- |
| `src/data_agent_baseline/benchmark/dataset.py` | 公開データセットローダー |
| `src/data_agent_baseline/tools/filesystem.py` | `list_context`, `read_csv`, `read_json`, `read_doc` |
| `src/data_agent_baseline/tools/python_exec.py` | `execute_python` |
| `src/data_agent_baseline/tools/sqlite.py` | `inspect_sqlite_schema`, `execute_context_sql` |
| `src/data_agent_baseline/tools/registry.py` | ツール登録と終端アクション `answer` |
| `src/data_agent_baseline/agents/prompt.py` | system prompt、task prompt、observation prompt |
| `src/data_agent_baseline/agents/react.py` | JSON action protocol に基づく ReAct runtime |
| `src/data_agent_baseline/run/runner.py` | 単一タスク実行とベンチマーク実行 |
