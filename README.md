<div align="center">

# DataAgent-Bench Starter Kit

日本語 | [中文](README.zh.md)

[![Official Website](https://img.shields.io/badge/Official%20Website-Visit%20dataagent.top-0ea5e9?style=for-the-badge&logo=googlechrome&logoColor=white&labelColor=0f172a)](https://dataagent.top)
[![Demo Dataset](https://img.shields.io/badge/Demo%20Dataset-Download%20Phase%201-f59e0b?style=for-the-badge&logo=googledrive&logoColor=white&labelColor=0f172a)](https://drive.google.com/file/d/1c6u5WlFw4KV7CBRyXh5BvFYbKqxhBSbL/view)
[![Discord](https://img.shields.io/badge/Discord-Join%20Community-5865F2?style=for-the-badge&logo=discord&logoColor=white&labelColor=0f172a)](https://discord.com/invite/7eFwJQN3Fx)

</div>

> KDD Cup 2026 DataAgent-Bench チャレンジ向けのスターターキットです。公開デモデータセットを読み込み、各タスクの `prediction.csv` と実行ログを生成します。

## 概要

このリポジトリは、DataAgent-Bench のタスクを ReAct 型 agent で解くためのベースラインです。agent は各 `task_<id>/task.json` の質問を読み、同じタスクの `context/` 配下にある CSV、JSON、SQLite、Markdown などをツール経由で確認し、最終回答を `prediction.csv` として出力します。

v2 では素の ReAct ループに加えて、context profile、文書 retrieval、DuckDB 横断クエリ、answer 検査を標準ツール化しています。

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

## セットアップ

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

4. ローカル用の設定ファイルを作成します。API key は git 管理しない `configs/react_baseline.local.yaml` に置いてください。

   ```bash
   cp configs/react_baseline.example.yaml configs/react_baseline.local.yaml
   ```

5. `configs/react_baseline.local.yaml` の `agent.model`, `agent.api_base`, `agent.api_key` を、利用する OpenAI 互換 API に合わせます。

## データセット

公開デモ dataset は `data/public/input/` 配下に配置します。各タスクディレクトリは次の構造です。

```text
data/public/input/task_<id>/
├── task.json
└── context/
```

対応する公開デモの正解は `data/public/output/task_<id>/gold.csv` に配置されます。hidden test には `input/` のみが含まれ、`output/` ディレクトリは含まれません。

`task.json` には次の項目が含まれます。

- `task_id`
- `difficulty`
- `question`

`context/` ディレクトリには、CSV、JSON、SQLite / DB、テキスト文書などが含まれます。

## 設定

設定ファイルの例は `configs/react_baseline.example.yaml` にあります。ローカル実行では、これをコピーした `configs/react_baseline.local.yaml` を使います。

```yaml
dataset:
  root_path: data/public/input

agent:
  model: YOUR_MODEL_NAME
  api_base: YOUR_API_BASE_URL
  api_key: YOUR_API_KEY
  max_steps: 20
  temperature: 0.0
  max_output_tokens: 4096
  request_timeout_seconds: 180
  max_retries: 1

run:
  output_dir: artifacts/runs
  run_id: example_run_id
  max_workers: 6
  task_timeout_seconds: 480
```

| 項目 | 意味 |
| --- | --- |
| `dataset.root_path` | `input/` dataset のルートディレクトリ。相対パスはプロジェクトルートから解決されます。 |
| `agent.model` | モデル名。 |
| `agent.api_base` | OpenAI 互換 API の base URL。 |
| `agent.api_key` | API key。 |
| `agent.max_steps` | 1 タスクあたりの ReAct 最大ステップ数。 |
| `agent.temperature` | サンプリング温度。 |
| `agent.max_output_tokens` | 1 回のモデル応答の最大 token 数。公式 Qwen の大きな context window に頼りすぎず、JSON action / answer に十分な余裕を持たせます。 |
| `agent.request_timeout_seconds` | モデル API request の timeout 秒数。 |
| `agent.max_retries` | モデル API request の最大 retry 回数。 |
| `run.output_dir` | 実行 artifact の出力ディレクトリ。 |
| `run.run_id` | 任意の run ディレクトリ名。省略時は UTC timestamp が使われます。既存の run ディレクトリは拒否されます。 |
| `run.max_workers` | `run-benchmark` / `submit-run` の並列 worker 数。公式評価の 16 vCPU / 64 GB RAM、A-board 2 時間、全タスク合計 12 時間制限を前提に、初期値は `6` としています。モデル API が詰まりやすい場合は `4`、十分安定している場合は `8` などへ調整してください。 |
| `run.task_timeout_seconds` | 1 タスクあたりの最大 wall-clock 時間。初期値 `480` 秒なら、A-board 約 60 タスクは worst-case でも約 80 分、B-board 約 320 タスクは約 7.1 時間で timeout 処理できます。`0` または負の値で無効化します。 |

公式評価では `MODEL_API_URL`, `MODEL_API_KEY`, `MODEL_NAME` が注入されるため、提出 image 内で API endpoint や model name をハードコードしないでください。`submit-run` はこれらの環境変数を config より優先して読み込みます。

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

全タスクを実行し、そのまま公開デモ正解で評価:

```bash
uv run dabench run-benchmark --config configs/react_baseline.local.yaml && \
RUN_DIR="$(ls -td artifacts/runs/* | head -n 1)" && \
uv run dabench evaluate-run \
  --run-dir "$RUN_DIR" \
  --gold-dir data/public/output \
  --output-json "$RUN_DIR/evaluation.json"
```

出力先:

```text
artifacts/runs/<run_id>/
├── summary.json
└── <task_id>/
    ├── trace.json
    └── prediction.csv
```

`trace.json` には、モデル応答、実行した action、tool observation、最終回答が記録されます。

## CLI

```bash
uv run dabench <command> --config PATH [options]
```

| コマンド | 用途 | 例 |
| --- | --- | --- |
| `status` | project path、config path、dataset root、公開タスク数を表示します。 | `uv run dabench status --config configs/react_baseline.local.yaml` |
| `inspect-task` | タスク metadata と `context/` 配下でアクセス可能なファイル一覧を表示します。 | `uv run dabench inspect-task task_1 --config configs/react_baseline.local.yaml` |
| `run-task` | 1 つのタスクで agent を実行します。 | `uv run dabench run-task task_1 --config configs/react_baseline.local.yaml` |
| `run-benchmark` | dataset 全体または `--limit N` 件で agent を実行します。 | `uv run dabench run-benchmark --config configs/react_baseline.local.yaml --limit 5` |
| `evaluate-run` | 公開デモの `gold.csv` がある範囲で run 出力をローカル評価します。 | `uv run dabench evaluate-run --run-dir artifacts/runs/<run_id> --gold-dir data/public/output` |
| `submit-run` | Docker 提出環境向けに `/input` を読み、`/output` と `/logs` へ出力します。 | `uv run dabench submit-run --input-dir data/public/input --output-dir /tmp/dabench-output --logs-dir /tmp/dabench-logs` |

## ローカル評価

公開デモの `gold.csv` がある範囲では、run 出力をローカル評価できます。

```bash
uv run dabench evaluate-run \
  --run-dir artifacts/runs/<run_id> \
  --gold-dir data/public/output \
  --output-json artifacts/runs/<run_id>/evaluation.json
```

この評価は公式 scoring rules に近づけたローカル近似です。hidden test の公式スコアそのものではありません。

## ツール

baseline はモデルに次のツールを公開します。ツールに渡すファイルパスは、すべて対象タスクの `context/` ディレクトリからの相対パスである必要があります。

| ツール | 用途 |
| --- | --- |
| `profile_context` | 難易度、質問、存在ファイル、CSV header、JSON shape、SQLite schema、文書見出しなどをまとめて返します。`context/knowledge.md` は除外します。 |
| `plan_knowledge` | `profile_context` 後に、参照データ情報とあわせて `knowledge.md` の全文を返し、用語、閾値、ルール確認に使います。 |
| `retrieve_context` | `knowledge.md` や `doc/*.md` から、質問に関連する文書 chunk を返します。 |
| `list_context` | `context/` 配下のファイルとディレクトリを一覧表示します。 |
| `read_csv` | CSV の preview を読み込みます。 |
| `read_json` | JSON の preview を読み込みます。 |
| `read_doc` | テキスト文書の preview を読み込みます。 |
| `inspect_sqlite_schema` | SQLite / DB ファイル内の table、columns、row count、先頭 preview rows を調べます。 |
| `execute_context_sql` | `context/` 内の SQLite / DB ファイルに対して read-only SQL を実行します。 |
| `execute_data_query` | CSV / JSON / SQLite を DuckDB 上に登録し、横断 SQL で join、集計、ranking を行います。 |
| `execute_python` | タスクの `context/` ディレクトリ内で Python code を実行します。 |
| `validate_answer` | 最終回答前に shape、空回答、0 値、余分列、tie、数値表記、結合名らしさ、根拠不足などのリスクを確認します。 |
| `answer` | 最終回答 table を提出し、タスクを終了します。 |

## 主要モジュール

| モジュール | 役割 |
| --- | --- |
| `src/data_agent_baseline/benchmark/dataset.py` | 公開 dataset loader |
| `src/data_agent_baseline/tools/context_profile.py` | `profile_context`, `plan_knowledge`, `retrieve_context` |
| `src/data_agent_baseline/tools/data_query.py` | `execute_data_query` |
| `src/data_agent_baseline/tools/filesystem.py` | `list_context`, `read_csv`, `read_json`, `read_doc` |
| `src/data_agent_baseline/tools/python_exec.py` | `execute_python` |
| `src/data_agent_baseline/tools/sqlite.py` | `inspect_sqlite_schema`, `execute_context_sql` |
| `src/data_agent_baseline/tools/answer_validation.py` | `validate_answer` |
| `src/data_agent_baseline/tools/registry.py` | tool 登録と終端 action の `answer` |
| `src/data_agent_baseline/agents/prompt.py` | system prompt、task prompt、observation prompt |
| `src/data_agent_baseline/agents/react.py` | JSON action protocol による ReAct runtime |
| `src/data_agent_baseline/run/runner.py` | 単一タスク実行と benchmark 実行 |

## 提出

公式提出は `prediction.csv` 単体ではなく、Docker image archive です。評価環境では `/input`, `/output`, `/logs` が mount され、`MODEL_API_URL`, `MODEL_API_KEY`, `MODEL_NAME` が注入されます。この実装は `submit-run` でそれらを読み、評価時は主催側 Qwen `qwen3.5-35b-a3b` に接続します。

提出用の Team ID は `1560` です。

```text
Docker image tag: 1560:v1
Archive filename: 1560_v1.tar.gz
Team ID in email: 1560
```

`v<N>` は提出ごとに必ずインクリメントし、同じバージョン番号を再利用しないでください。2 回目以降の例:

```text
Docker image tag: 1560:v2
Archive filename: 1560_v2.tar.gz
```

### 公式評価の注意事項

- ログにテストデータや gold answer を出力しないでください。公式ルール 3.7 では `/logs` の内容が主催者レビュー対象であり、テストセットのデータや gold answer を含むログを出力した場合、そのデータはデバッグサポート対象外になります。悪質なケースでは失格になる可能性があります。
- `runtime.log` だけでなく、task ごとの `trace.json` にも注意してください。`trace.json` にはモデル応答、tool observation、最終回答が残るため、gold answer や hidden test の内容を不要に含めない設計にしてください。
- 公式ルール 7 では、Phase 1 の A-board 約 60 タスクは評価 1 回あたり wall-clock 2 時間が上限です。全タスク合計の上限は 12 時間です。
- 公式ルール 4.2 では、timeout / OOM 時は `SIGTERM`、30 秒待機、`SIGKILL` の順で container が終了します。すでに書き出された `prediction.csv` は採点対象ですが、未書き出しタスクは 0 点になります。
- そのため、各タスクが完了した時点で即座に `/output/task_<id>/prediction.csv` を書き出すことが重要です。この実装の `submit-run` は task ごとに prediction と trace を保存します。
- Phase 1 leaderboard は A-board 約 60 タスクと B-board 約 320 タスクに分かれ、最終スコアはデータ比率で重み付けされた合計です。同スコアの場合は、提出時刻が早いチームが上位になります。

### 1. Docker image を build

```bash
docker build --platform linux/amd64 -t 1560:v1 .
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

ENTRYPOINT に `submit-run` が含まれているため、評価環境では追加パラメータなしで起動できます。`submit-run` は既定で `/input` を読み、`/output` に `task_<id>/prediction.csv`、`/logs` に `runtime.log` と task ごとの `trace.json` を出力します。

### 2. Docker image を疑似評価

API key は Dockerfile や config に書かず、環境変数として渡します。

軽量な起動確認:

```bash
export MODEL_API_URL="https://openrouter.ai/api/v1"
export MODEL_API_KEY="YOUR_LOCAL_API_KEY"
export MODEL_NAME="qwen3.5-35b-a3b"

rm -rf /tmp/dabench-output /tmp/dabench-logs
mkdir -p /tmp/dabench-output /tmp/dabench-logs

docker run --rm --platform linux/amd64 \
  -v "$PWD/data/public/input:/input:ro" \
  -v /tmp/dabench-output:/output \
  -v /tmp/dabench-logs:/logs \
  -e MODEL_API_URL="$MODEL_API_URL" \
  -e MODEL_API_KEY="$MODEL_API_KEY" \
  -e MODEL_NAME="$MODEL_NAME" \
  1560:v1 --limit 1
```

本番相当の起動確認では、`--limit` を付けずに実行します。

確認項目:

- `/tmp/dabench-output/task_<id>/prediction.csv` が生成される。
- `/tmp/dabench-logs/runtime.log` が生成される。
- `/tmp/dabench-logs/task_<id>/trace.json` が生成される。
- `data/public/input` が変更されていない。
- `runtime.log` や `trace.json` に gold answer や hidden test の内容を不要に出力していない。

### 3. 提出用 archive を作成

```bash
docker save 1560:v1 | gzip > 1560_v1.tar.gz
ls -lh 1560_v1.tar.gz
```

archive は 10GB 以下にしてください。

### 4. Google Drive 経由で提出

`1560_v1.tar.gz` を Google Drive にアップロードし、「リンクを知っている全員が閲覧可」に設定してから、公式 Rules の形式でメール提出します。
宛先：kddcup@hkust-gz.edu.cn

```text
Subject: [KDDCup2026 Data Agents] Submission - 1560 - v1
Team ID: 1560
Version: v1
Sharing link: <Google Drive link>
```

評価完了通知を受け取るまで、Google Drive 上の archive は削除・変更しないでください。

### 5. 公式 vLLM 起動コマンド

参加者 container からこのコマンドを実行する必要はありません。評価時は `MODEL_API_URL`, `MODEL_API_KEY`, `MODEL_NAME` で注入される OpenAI 互換 endpoint を使います。

```bash
vllm serve <model_path> \
  --host <host> --port <port> \
  --tensor-parallel-size 8 \
  --seed 1024 \
  --served-model-name qwen3.5-35b-a3b \
  --max-model-len 262144 \
  --reasoning-parser qwen3 \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder \
  --trust-remote-code
```

## v2 改善結果メモ

`baseline_results/dabench-output` と v2 実行結果 `dabench-output` を、公開デモの `data/public/output` に対してローカル近似評価した比較です。hidden test の公式スコアそのものではありません。

| run | scored | missing | average_score | average_recall |
| --- | ---: | ---: | ---: | ---: |
| baseline_results | 42/50 | 8 | 0.527500 | 0.533333 |
| v2 `dabench-output` | 43/50 | 7 | 0.610000 | 0.613333 |
| 差分 | +1 | -1 | +0.082500 | +0.080000 |

主な改善は、`task_19`, `task_22`, `task_38`, `task_243`, `task_350`, `task_408` が 0 点から 1.0 へ改善したことです。一方で `task_11`, `task_86` は 1.0 から 0 点へ悪化しており、次の優先確認対象です。

## 改訂記録

このリポジトリで仕様、設定、実行手順、エージェント挙動に関わる変更を行った場合は、この節に日付と概要を追記してください。

| 日付 | 内容 |
| --- | --- |
| 2026-05-05 | `inspect_sqlite_schema` が DB table の schema だけでなく row count と preview rows も返すようにし、prompt で DB 実データ確認を明示。 |
| 2026-05-05 | ReAct prompt を段階的なデータ確認・質問理解・計算重視に更新し、0 件/0 値・列結合・ツール失敗時の fallback 指示と回答検査 warning を追加。 |
| 2026-05-05 | `plan_knowledge` ツールを追加し、`profile_context` 後に `knowledge.md` 全文を参照する workflow へ更新。 |
| 2026-05-05 | `profile_context` が `context/knowledge.md` を profile 対象から除外するように変更。 |
| 2026-05-04 | 難易度別 task prompt 戦略を `csv/json/db/doc/root` 分類ベースへ更新。 |
| 2026-05-01 | v2 実行結果を `baseline_results` と公開 gold で比較し、平均 score が `0.527500` から `0.610000` へ改善したことを記録。 |
| 2026-05-01 | v2 構造刷新として `profile_context`、`retrieve_context`、`execute_data_query`、`validate_answer` を実装し、難易度別 prompt、config の timeout/retry、既定 `max_workers=4` を整備。 |
| 2026-05-01 | 提出用 Team ID を `1560` に更新し、Docker image tag、archive 名、メール提出例を `1560` 形式へ変更。 |
| 2026-05-01 | `submit-run` 後の trace・runtime log・prediction を統合分析する project-local skill `.codex/skills/dabench-analyze-run` を追加。 |
| 2026-05-01 | `execute_data_query` と `validate_answer` を追加し、answer 形状・tie・集計の検証方針とモデル API timeout/retry 設定を追加。 |
| 2026-05-01 | task 難易度と context 構成に応じた agent strategy を追加し、`execute_data_query` の CSV 型推論・SQLite alias・修復可能エラー応答を改善。 |
| 2026-04-30 | 公開デモ dataset の Google Drive ダウンロードリンクと配置先を導入手順に追記。 |
| 2026-04-30 | README を概要・導入・ローカル実行・評価・提出手順中心に整理し、詳細な tool/module 説明を docs 参照へ移動。 |
| 2026-04-30 | Docker build、ローカル疑似評価、ローカル採点、archive 作成、Google Drive 提出までの手順を追加。 |
| 2026-04-30 | Dockerfile をマルチステージ化し、runtime image から `uv` と build 用ファイルを除外。 |
| 2026-04-30 | Docker 提出 image の entrypoint を `.venv/bin/dabench` 直接実行に変更し、起動時の `uv run` 再同期を回避。 |
| 2026-04-30 | `submit-run` 後の評価手順を project-local skill `.codex/skills/dabench-evaluate-run` として追加。 |
| 2026-04-30 | 公開デモの `gold.csv` に対する `evaluate-run` コマンドを追加。 |
| 2026-04-30 | `AGENTS.md` にプロンプト文言は英語、人間向け解説は日本語とする言語方針を追記。 |
| 2026-04-30 | モデル向けツール説明文を system prompt と揃えて英語化。 |
| 2026-04-30 | 公式提出向けの `submit-run`、Dockerfile、`.dockerignore` を追加し、`MODEL_*` 環境変数優先の実行方針を整理。 |
| 2026-04-30 | `docs/submission.md` を追加し、KDD Cup 2026 の Docker image 提出方式と次の対応事項を整理。 |
| 2026-04-30 | README を日本語化し、プロジェクト概要・実行手順・主要モジュールを整理。 |
