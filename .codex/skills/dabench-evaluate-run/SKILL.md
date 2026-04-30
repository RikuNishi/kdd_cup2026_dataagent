---
name: dabench-evaluate-run
description: DataAgent-Bench / DABench の submit-run や run-benchmark 後に、prediction.csv を公開 gold.csv と照合してローカル評価する。スコア、評価、採点、正解比較、accuracy/recall/score 計算、KDD Cup 2026 DataAgent-Bench の task 別性能要約を求められたときに使う。
---

# DABench Evaluate Run

この project-local skill は、次のような `submit-run` 実行後の DataAgent-Bench 出力を評価するために使います。

```bash
uv run dabench submit-run \
  --input-dir data/public/input \
  --output-dir /tmp/dabench-output \
  --logs-dir /tmp/dabench-logs \
  --config configs/react_baseline.local.yaml \
  --limit 15
```

## 手順

1. 予測ディレクトリを特定する。
   - `submit-run` の場合は `--output-dir` の値を使う。例: `/tmp/dabench-output`
   - `run-task` や `run-benchmark` の場合は run ディレクトリを使う。例: `artifacts/runs/<run_id>`
2. 正解ディレクトリを特定する。
   - このリポジトリでは、通常 `data/public/output` を使う。
3. まず repo 側の CLI が使えるなら優先して実行する。

```bash
uv run dabench evaluate-run \
  --run-dir /tmp/dabench-output \
  --gold-dir data/public/output
```

4. repo 側の CLI が使えない、または壊れている場合は、skill 付属の fallback script を実行する。

```bash
python3 .codex/skills/dabench-evaluate-run/scripts/evaluate_dabench_run.py \
  --prediction-dir /tmp/dabench-output \
  --gold-dir data/public/output
```

5. 結果として `average_score`, `average_recall`, `scored_task_count`, `missing_prediction_count` と、特に低スコアの task を要約する。

## 採点メモ

- 公開 Rules に近いローカル評価として、列名を無視し、行順を無視し、列ごとの signature を比較して recall を計算し、余分な予測列には redundancy penalty を適用する。
- null 系の値は空文字に正規化する。
- 数値は小数 2 桁に正規化する。
- 文字列比較は case-sensitive。
- 公式の redundancy penalty 定数は公開仕様上では明示されていない。ユーザー指定がなければ `0.1` を使う。

## 出力時の注意

評価結果を説明するときは、公開 scoring rules に近づけたローカル近似評価であることを明記する。公式 hidden-test score だとは言わない。
