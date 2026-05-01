---
name: dabench-analyze-run
description: DataAgent-Bench / DABench の submit-run や run-benchmark 後に、prediction.csv、trace.json、runtime.log、公開 gold.csv を統合分析して失点原因と改善施策を検討する。KDD Cup 2026 DataAgent-Bench の実行結果レビュー、低スコア task の原因分類、prompt/tool/agent loop/config 改善案の作成を求められたときに使う。
---

# DABench Analyze Run

この project-local skill は、DABench 実行結果の採点値だけでなく、ReAct trace と runtime log から失敗原因を分類し、次に実装すべき改善施策を出すために使います。

## 基本方針

- 提出・評価環境に関わる作業なので、先に `docs/submission.md` を確認する。
- 採点はローカル近似評価であり、hidden test の公式スコアとは言わない。
- 採点専用の作業は既存 skill `.codex/skills/dabench-evaluate-run` を優先し、この skill では採点結果を trace 診断の入力として扱う。
- `jq` は前提にしない。JSON/CSV の集計は Python 標準ライブラリで行う。

## 標準ワークフロー

1. 実行ディレクトリを特定する。
   - `submit-run`: `--output-dir` と `--logs-dir` を使う。例: `dabench-output`, `dabench-logs`
   - `run-benchmark`: run ディレクトリ配下に `prediction.csv` と `trace.json` が同居する。必要なら `--logs-dir` に同じ run ディレクトリを指定する。
2. まず公開 gold に対するローカル近似評価を出す。

```bash
uv run dabench evaluate-run \
  --run-dir dabench-output \
  --gold-dir data/public/output
```

3. 付属スクリプトで trace / runtime / prediction / gold をまとめて分析する。

```bash
python3 .codex/skills/dabench-analyze-run/scripts/analyze_dabench_run.py \
  --prediction-dir dabench-output \
  --logs-dir dabench-logs \
  --gold-dir data/public/output \
  --top-k 10
```

4. 低スコア task を優先して、`prediction.csv` と `gold.csv` の列数・一致列・余分列・missing を確認する。
5. `trace.json` から step 数、tool 使用順、tool error、`failure_reason`、answer 有無、`e2e_elapsed_seconds` を確認する。
6. 改善案を次のカテゴリに分けて提案する。
   - `prompt`: `src/data_agent_baseline/agents/prompt.py`
   - `tool`: `src/data_agent_baseline/tools/registry.py` または各 tool 実装
   - `agent loop`: `src/data_agent_baseline/agents/react.py`
   - `data handling`: CSV/JSON/SQLite の読み取り・正規化・answer 生成
   - `runtime/config`: `src/data_agent_baseline/run/runner.py`, `src/data_agent_baseline/cli.py`, config

## 付属スクリプト

`scripts/analyze_dabench_run.py` は次の既定パスを使います。

```bash
python3 .codex/skills/dabench-analyze-run/scripts/analyze_dabench_run.py
```

既定値:

- `--prediction-dir dabench-output`
- `--logs-dir dabench-logs`
- `--gold-dir data/public/output`
- `--input-dir data/public/input`
- `--top-k 10`
- `--format markdown`

JSON として後続処理に渡す場合:

```bash
python3 .codex/skills/dabench-analyze-run/scripts/analyze_dabench_run.py \
  --format json
```

## レポートの読み方

- `score=0` かつ prediction がある task は、trace を最優先で確認する。出力は生成できているため、問題は task 解釈、列選択、値正規化、行集合のいずれかである可能性が高い。
- `missing` は runtime timeout、uncaught error、answer 未提出、実行対象外のいずれかを切り分ける。
- `extra_columns` が大きい task は、answer の列選択や余分な ID/説明列を疑う。
- tool error が多い場合は、tool description、tool 入力 schema、error observation 後の回復方針を優先して見る。
- step 数や elapsed が大きい task は、探索手順、context preview 上限、SQL/Python 実行への誘導を検討する。

## 出力時の注意

- 改善施策は「何を変えるか」「どのファイルを見るか」「どのコマンドで検証するか」を短く添える。
- 低スコア task の個別考察では、必ず task id と根拠となる trace/prediction/gold の観察を示す。
- 実装変更を提案する場合も、ユーザーが明示するまではこの skill 自体でコード変更まで進めない。
