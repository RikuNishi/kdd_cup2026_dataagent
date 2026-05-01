<div align="center">

# DataAgent-Bench Starter Kit

日本語 | [中文](README.zh.md)

[![Official Website](https://img.shields.io/badge/Official%20Website-Visit%20dataagent.top-0ea5e9?style=for-the-badge&logo=googlechrome&logoColor=white&labelColor=0f172a)](https://dataagent.top)
[![Demo Dataset](https://img.shields.io/badge/Demo%20Dataset-Download%20Phase%201-f59e0b?style=for-the-badge&logo=googledrive&logoColor=white&labelColor=0f172a)](https://drive.google.com/file/d/1c6u5WlFw4KV7CBRyXh5BvFYbKqxhBSbL/view)
[![Discord](https://img.shields.io/badge/Discord-Join%20Community-5865F2?style=for-the-badge&logo=discord&logoColor=white&labelColor=0f172a)](https://discord.com/invite/7eFwJQN3Fx)

</div>

> KDD Cup 2026 DataAgent-Bench チャレンジ向けのスターターキットです。公開デモデータセットを読み込み、各タスクの `prediction.csv` と実行ログを生成します。

## v2 改善結果メモ

`baseline_results/dabench-output` と v2 実行結果 `dabench-output` を、公開デモの `data/public/output` に対してローカル近似評価した比較です。hidden test の公式スコアそのものではありません。

| run | scored | missing | average_score | average_recall |
| --- | ---: | ---: | ---: | ---: |
| baseline_results | 42/50 | 8 | 0.527500 | 0.533333 |
| v2 `dabench-output` | 43/50 | 7 | 0.610000 | 0.613333 |
| 差分 | +1 | -1 | +0.082500 | +0.080000 |

主な改善は、`task_19`, `task_22`, `task_38`, `task_243`, `task_350`, `task_408` が 0 点から 1.0 へ改善したことです。一方で `task_11`, `task_86` は 1.0 から 0 点へ悪化しており、次の優先確認対象です。

## 概要

このリポジトリは、DataAgent-Bench のタスクを ReAct 型 agent で解くためのベースラインです。v2 では ReAct ループに加えて、context profile、文書 retrieval、DuckDB 横断クエリ、answer 検査を標準ツール化しています。agent は各 `task_<id>/task.json` の質問を読み、同じタスクの `context/` 配下にある CSV、JSON、SQLite、Markdown 等をツール経由で確認し、最終回答を `prediction.csv` として出力します。

| 項目 | 内容 |
| --- | --- |
| 入力データ | `data/public/input/task_<id>/` |
| 公開デモ正解 | `data/public/output/task_<id>/gold.csv` |
| 通常実行出力 | `artifacts/runs/<run_id>/<task_id>/` |
| 提出形式 | Docker image archive |
| Team ID | `1560` |

詳細:

- Agent の内部フロー: `docs/overview.md`
- 公式提出方式と評価環境: `docs/submission.md`
- Public task の概要: `docs/tasks_summry.md`

## 導入

1. `uv` をインストールします。

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. 公開デモ dataset を Google Drive からダウンロードし、`data/public/` 配下に配置します。

   - https://drive.google.com/file/d/1c6u5WlFw4KV7CBRyXh5BvFYbKqxhBSbL/view?usp=share_link

   配置後、少なくとも `data/public/input/` が存在する状態にしてください。公開デモの正解を使ってローカル評価する場合は、`data/public/output/` も配置します。

3. 依存関係を同期します。

   ```bash
   uv sync
   ```

4. 設定ファイルを作成します。ローカルの API key は git 管理しない `configs/react_baseline.local.yaml` に置いてください。

   ```bash
   cp configs/react_baseline.example.yaml configs/react_baseline.local.yaml
   ```

5. `configs/react_baseline.local.yaml` の `agent.model`, `agent.api_base`, `agent.api_key` をローカル検証用の OpenAI 互換 API に合わせます。必要に応じて `agent.request_timeout_seconds` と `agent.max_retries` も調整します。

   `run.max_workers` は既定で `4` です。モデル API が詰まりやすい場合は `2`、十分安定している場合は `8` などへ調整してください。

## ローカル実行

データセットの認識確認:

```bash
uv run dabench status --config configs/react_baseline.local.yaml
```

1 タスクだけ実行:

```bash
uv run dabench run-task task_11 --config configs/react_baseline.local.yaml
```

複数タスクを実行:

```bash
uv run dabench run-benchmark --config configs/react_baseline.local.yaml --limit 5
```

主な出力:

```text
artifacts/runs/<run_id>/<task_id>/
├── trace.json
└── prediction.csv
```

`trace.json` には、モデル応答、実行した action、tool observation、最終回答が記録されます。

## ローカル評価

公開デモの `gold.csv` がある範囲では、run 出力をローカル評価できます。

```bash
uv run dabench evaluate-run \
  --run-dir artifacts/runs/<run_id> \
  --gold-dir data/public/output \
  --output-json artifacts/runs/<run_id>/evaluation.json
```

この評価は公式 scoring rules に近づけたローカル近似です。hidden test の公式スコアそのものではありません。

## 提出の重要事項

公式提出は `prediction.csv` 単体ではなく、Docker image archive です。評価環境では `/input`, `/output`, `/logs` が mount され、`MODEL_API_URL`, `MODEL_API_KEY`, `MODEL_NAME` が注入されます。この実装は `submit-run` でそれらを読み、評価時は主催側 Qwen `qwen3.5-35b-a3b` に接続します。

提出用の Team ID は `1560` です。Docker image tag、archive 名、メール上の Team ID はすべて `1560` を使います。

```text
Docker image tag: 1560:v1
Archive filename: 1560_v1.tar.gz
Team ID in email: 1560
```

### 1. Docker image を build

```bash
docker build -t 1560:v1 .
```

entrypoint 確認:

```bash
docker image inspect 1560:v1 \
  --format '{{json .Config.Entrypoint}}'
```

期待値:

```text
["/app/.venv/bin/dabench","submit-run"]
```

### 2. Docker image を疑似評価

API key は Dockerfile や config に書かず、環境変数として渡します。

```bash
export MODEL_API_URL="https://openrouter.ai/api/v1"
export MODEL_API_KEY="YOUR_LOCAL_API_KEY"
export MODEL_NAME="qwen3.5-35b-a3b"

rm -rf /tmp/dabench-output /tmp/dabench-logs
mkdir -p /tmp/dabench-output /tmp/dabench-logs

docker run --rm \
  -v "$PWD/data/public/input:/input:ro" \
  -v /tmp/dabench-output:/output \
  -v /tmp/dabench-logs:/logs \
  -e MODEL_API_URL="$MODEL_API_URL" \
  -e MODEL_API_KEY="$MODEL_API_KEY" \
  -e MODEL_NAME="$MODEL_NAME" \
  1560:v1 --limit 1
```

確認項目:

- `/tmp/dabench-output/task_<id>/prediction.csv` が生成される。
- `/tmp/dabench-logs/runtime.log` が生成される。
- `/tmp/dabench-logs/task_<id>/trace.json` が生成される。
- `data/public/input` が変更されていない。


### 3. 提出用 archive を作成

```bash
docker save 1560:v1 | gzip > 1560_v1.tar.gz
ls -lh 1560_v1.tar.gz
```

archive は 10GB 以下にしてください。

### 4. Google Drive 経由で提出

`1560_v1.tar.gz` を Google Drive にアップロードし、「リンクを知っている全員が閲覧可」に設定してから、公式 Rules の形式でメール提出します。

```text
Subject: [KDDCup2026 Data Agents] Submission - 1560 - v1
Team ID: 1560
Version: v1
Sharing link: <Google Drive link>
```

評価完了通知を受け取るまで、Google Drive 上の archive は削除・変更しないでください。

## 改訂記録

このリポジトリで仕様、設定、実行手順、エージェント挙動に関わる変更を行った場合は、この節に日付と概要を追記してください。

| 日付 | 内容 |
| --- | --- |
| 2026-05-01 | v2 実行結果を `baseline_results` と公開 gold で比較し、平均 score が `0.527500` から `0.610000` へ改善したことを記録。 |
| 2026-05-01 | v2 構造刷新として `profile_context`、`retrieve_context`、`execute_data_query`、`validate_answer` を実装し、難易度別 prompt、config の timeout/retry、既定 `max_workers=4` を整備。 |
| 2026-05-01 | 提出用 Team ID を `1560` に更新し、Docker image tag、archive 名、メール提出例を `1560` 形式へ変更。 |
| 2026-04-30 | 公開デモ dataset の Google Drive ダウンロードリンクと配置先を導入手順に追記。 |
| 2026-04-30 | README を概要・導入・ローカル実行・評価・提出手順中心に整理し、詳細な tool/module 説明を docs 参照へ移動。 |
| 2026-04-30 | Docker build、ローカル疑似評価、ローカル採点、archive 作成、Google Drive 提出までの手順を追加。 |
| 2026-04-30 | Dockerfile をマルチステージ化し、runtime image から `uv` と build 用ファイルを除外。 |
| 2026-04-30 | Docker 提出 image の entrypoint を `.venv/bin/dabench` 直接実行に変更し、起動時の `uv run` 再同期を回避。 |
| 2026-04-30 | `submit-run` 後の評価手順を project-local skill `.codex/skills/dabench-evaluate-run` として追加。 |
| 2026-05-01 | `submit-run` 後の trace・runtime log・prediction を統合分析する project-local skill `.codex/skills/dabench-analyze-run` を追加。 |
| 2026-05-01 | `execute_data_query` と `validate_answer` を追加し、answer 形状・tie・集計の検証方針とモデル API timeout/retry 設定を追加。 |
| 2026-05-01 | task 難易度と context 構成に応じた agent strategy を追加し、`execute_data_query` の CSV 型推論・SQLite alias・修復可能エラー応答を改善。 |
| 2026-04-30 | 公開デモの `gold.csv` に対する `evaluate-run` コマンドを追加。 |
| 2026-04-30 | `AGENTS.md` にプロンプト文言は英語、人間向け解説は日本語とする言語方針を追記。 |
| 2026-04-30 | モデル向けツール説明文を system prompt と揃えて英語化。 |
| 2026-04-30 | 公式提出向けの `submit-run`、Dockerfile、`.dockerignore` を追加し、`MODEL_*` 環境変数優先の実行方針を整理。 |
| 2026-04-30 | `docs/submission.md` を追加し、KDD Cup 2026 の Docker image 提出方式と次の対応事項を整理。 |
| 2026-04-30 | README を日本語化し、プロジェクト概要・実行手順・主要モジュールを整理。 |
