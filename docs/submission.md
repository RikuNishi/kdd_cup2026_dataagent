# KDD Cup 2026 提出方式メモ

このメモは KDD Cup 2026 Data Agents の公式サイトと Rules ページをもとに、提出方式を実装作業向けに整理したものです。提出仕様は公式 Rules が正です。

参照元:

- https://dataagent.top/
- https://dataagent.top/rules

## 提出物

提出物は `prediction.csv` 単体ではなく、Agent 一式を含む Docker image archive です。

| 項目 | 形式 |
| --- | --- |
| Docker image 名 | `<team_id>:v<N>` |
| archive ファイル名 | `<team_id>_v<N>.tar.gz` |
| 例 | `team0042:v3`, `team0042_v3.tar.gz` |

注意点:

- `<team_id>` は主催者から割り当てられる ID を使う。
- `<N>` は提出ごとに 1 から増やす。過去に使った version は再利用しない。
- archive 名は image tag と対応させる。
- image は `ENTRYPOINT` または `CMD` を持ち、`docker run` だけで実行できる必要がある。

## 提出手順

1. Docker image を `<team_id>:v<N>` で build する。
2. image を `<team_id>_v<N>.tar.gz` として export / 圧縮する。
3. archive を Google Drive にアップロードする。
4. 共有設定を「リンクを知っている全員が閲覧可」にする。
5. 主催者へメールで共有リンクを送る。

メールに含める情報:

- 件名: `[KDDCup2026 Data Agents] Submission - <team_id> - v<N>`
- Team ID
- Version
- Google Drive sharing link

Google Drive 上のファイルは、評価完了通知を受け取るまで削除・変更しないでください。

## 評価環境の I/O

評価環境では、コンテナに次のディレクトリが用意されます。

| パス | 用途 |
| --- | --- |
| `/input` | 評価対象タスク。読み取り専用として扱う。 |
| `/output` | 予測結果の出力先。 |
| `/logs` | runtime log の出力先。 |

Agent は `/input/task_<id>/` を走査し、各タスクについて `/output/task_<id>/prediction.csv` を作成します。

出力例:

```text
/output/
└── task_<id>/
    └── prediction.csv
```

`/input` を変更してはいけません。

## prediction.csv 仕様

- UTF-8 の標準 CSV。
- 1 行目は header。列名自体は採点対象外。
- 2 行目以降が回答行。
- 列順・行順は採点に影響しない。
- `prediction.csv` が存在しないタスクは 0 点。
- 余分な列が多いと redundancy penalty の対象になる。
- 文字列比較は case-sensitive。
- 数値は評価時に小数 2 桁へ正規化される。
- null 系の値は空文字として扱われる。

## モデル接続

評価時は、主催者が OpenAI Chat Completions 互換の Qwen3.5-35B-A3B サービスを提供します。Agent は以下の環境変数からモデル接続情報を読む必要があります。

| 環境変数 | 内容 |
| --- | --- |
| `MODEL_API_URL` | 評価環境内のモデル API URL |
| `MODEL_API_KEY` | 評価環境が注入する API key |
| `MODEL_NAME` | 評価時のモデル名。`qwen3.5-35b-a3b` |

禁止事項:

- Docker image 内に API URL や API key を hardcode しない。
- 評価時に OpenRouter など外部 LLM API を直接呼ばない。
- 評価時に主タスク解決用の別 LLM をコンテナ内で動かさない。
- 評価環境の外部ネットワークへアクセスしない。

OpenRouter はローカル検証用に限定し、本番 image には key を含めない方針にします。

## リソース制限

| 項目 | 制限 |
| --- | --- |
| CPU | 16 vCPU |
| Memory | 64GB |
| GPU | なし |
| 全タスク runtime | 12 時間 |

timeout、OOM、non-zero exit が起きた場合でも、それまでに生成済みの `prediction.csv` は採点対象になります。未出力タスクは 0 点です。

## 提出回数と期限

| 項目 | ルール |
| --- | --- |
| 次回提出条件 | 前回提出の評価結果を待つ |
| 1 日あたり | 最大 1 回 |
| Phase 1 合計 | 最大 30 回 |
| Phase 1 final deadline | 2026-05-20 EoD AoE |
| Phase 1 final leaderboard | 2026-05-25 EoD AoE |

2026-04-30 から 2026-05-02 AoE までは submission intake が一時停止し、2026-05-03 AoE に再開予定です。

## 次にやること

1. `submit-run` をローカル疑似評価で実行し、`/output/task_<id>/prediction.csv` と `/logs/runtime.log` が生成されることを確認する。
2. Docker image を build し、`docker run` だけで `submit-run` が始まることを確認する。
3. 小さいタスク数で Docker 実行を検証し、`prediction.csv` が公式仕様どおり出ることを確認する。
4. team ID が確定したら、提出用 tag と archive 名で image を export する。

## ローカル疑似評価

ローカルでは `/input`, `/output`, `/logs` の代わりに任意のディレクトリを指定して確認できます。

```bash
uv run dabench submit-run \
  --input-dir data/public/input \
  --output-dir /tmp/dabench-output \
  --logs-dir /tmp/dabench-logs \
  --config configs/react_baseline.local.yaml \
  --limit 1
```

確認項目:

- `/tmp/dabench-output/task_<id>/prediction.csv` が生成される。
- `/tmp/dabench-logs/runtime.log` が生成される。
- `/tmp/dabench-logs/task_<id>/trace.json` が生成される。
- 入力ディレクトリは変更されない。

## Docker build / export

提出前の build 例:

```bash
docker build -t <team_id>:v1 .
```

疑似評価の実行例:

```bash
docker run --rm \
  -v "$PWD/data/public/input:/input:ro" \
  -v "/tmp/dabench-output:/output" \
  -v "/tmp/dabench-logs:/logs" \
  -e MODEL_API_URL="$MODEL_API_URL" \
  -e MODEL_API_KEY="$MODEL_API_KEY" \
  -e MODEL_NAME="qwen3.5-35b-a3b" \
  <team_id>:v1
```

提出用 archive の作成例:

```bash
docker save <team_id>:v1 | gzip > <team_id>_v1.tar.gz
```

archive 作成後は、ファイル名が `<team_id>_v<N>.tar.gz` 形式になっていることを確認してください。
