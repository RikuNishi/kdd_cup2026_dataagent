# v6 ソース復元パッケージ

## 何が入っているか

Docker image `1560:v6` (sha256:`a9ecb16b3ce1`, build 時刻 **2026-05-19 07:46 UTC**) から `/app/` 配下を抽出したソース一式です。

`feature/v6-speed` ブランチ名の由来となった "v6" 状態 (v7 改善着手前) のコードがそのまま入っています。git の reflog / dangling commit / リモート全部探したが見つからなかったため、Docker image 経由で復元しました。

## 復元元

- Docker image: `1560:v6`
- Image ID: `sha256:a9ecb16b3ce12e69c9e55d96dbeecd01f81a3e066a7604d604d930f9faf87476`
- Image build (= Dockerfile COPY 時点): 2026-05-19 07:46 UTC
- Entrypoint: `/app/.venv/bin/dabench submit-run`
- 抽出コマンド:
  ```pwsh
  docker run -d --name v6tmp --entrypoint sleep 1560:v6 3600
  docker exec v6tmp tar cf /tmp/v6.tar --exclude=.venv --exclude=__pycache__ \
      --exclude=artifacts --exclude=data -C /app .
  docker cp v6tmp:/tmp/v6.tar v6_app.tar
  docker rm -f v6tmp
  tar -xf v6_app.tar -C v6_extracted
  ```

`.venv` / `__pycache__` / `artifacts` / `data` は除外しています (再生成可能なため)。

## ディレクトリ構成

```
v6_extracted/
├── README.md
├── configs/
│   └── react_baseline.example.yaml
├── docs/
└── src/
    └── data_agent_baseline/
        ├── __init__.py
        ├── cli.py
        ├── config.py
        ├── evaluation.py
        ├── agents/
        │   ├── __init__.py
        │   ├── model.py
        │   ├── prompt.py
        │   ├── react.py
        │   └── runtime.py
        ├── benchmark/
        │   ├── __init__.py
        │   ├── dataset.py
        │   └── schema.py
        ├── run/
        │   ├── __init__.py
        │   └── runner.py
        └── tools/
            ├── __init__.py
            ├── answer_validation.py
            ├── context_profile.py
            ├── data_query.py
            ├── filesystem.py
            ├── python_exec.py
            ├── registry.py
            └── sqlite.py
```

## v6 の config (`configs/react_baseline.example.yaml` 抜粋)

```yaml
agent:
  max_steps: 20
  temperature: 0.1
  max_output_tokens: 4096
  request_timeout_seconds: 180
  max_retries: 3

run:
  max_workers: 6
  task_timeout_seconds: 480

difficulty_overrides:
  easy:    { max_steps: 15, task_timeout_seconds: 240 }
  medium:  { max_steps: 20, task_timeout_seconds: 360 }
  hard:    { max_steps: 30, task_timeout_seconds: 720 }
  extreme: { max_steps: 15, task_timeout_seconds: 240 }
```

- `max_workers: 6` の並列ベンチ前提
- hard だけ step / timeout が大きい (30 / 720s)
- extreme は当時まだ最適化されていない (15 / 240s)

## v6 のローカルベンチ結果 (`docs/v7_changelog.md` より引用)

| 難易度 | n | 平均スコア | 平均時間 |
|---|---|---|---|
| easy | 15 | 0.730 | 22.5s |
| medium | 23 | 0.652 | 195.0s |
| hard | 11 | 0.389 | 70.5s |
| extreme | 1 | 0.000 | 53.9s |
| **合計** | **50** | **0.605** | 113.0s |

v6 → v7 で hard が 0.389 → 0.567 に改善された記録あり。

## ビルド & 実行 (Docker)

復元したソースから image を再 build したい場合:

```bash
# このパッケージのルート (v6_extracted/) に Dockerfile が無いので、
# 提出用 Dockerfile (Python 3.12 + uv + dabench) は別途用意してください。
# 既存 image をそのまま使うのが最も確実です:
docker run --rm --platform linux/amd64 \
  -e MODEL_API_URL=...  -e MODEL_API_KEY=...  -e MODEL_NAME=... \
  -v /path/to/input:/input:ro \
  -v /path/to/output:/output \
  -v /path/to/logs:/logs \
  1560:v6
```

## ローカル開発として動かす場合

```bash
cd v6_extracted
uv sync          # 要 pyproject.toml (このパッケージには含まれない場合あり)
uv run dabench run-benchmark --config configs/react_baseline.example.yaml
```

> **重要:** v6 image は multi-stage build で、`/app` には `src/configs/docs/README.md` しか入っていません (`pyproject.toml` / `uv.lock` / `Dockerfile` は builder stage で使われただけ)。
>
> このパッケージには **現行 repo (= v8 樋口版 = commit 3a2b6c5) の `pyproject.toml`, `uv.lock`, `Dockerfile.current_repo` を参考として同梱** しています。v6 と v8 で依存定義は大きく変わっていないため、これらと組み合わせれば再ビルド可能です。完全に bit-identical な再現が必要なら `1560:v6` image そのものを使ってください。

## 注意

- Docker image は本番提出用にビルドされたものなので、API key / API URL は環境変数で渡す前提です (image 内に埋め込まれていません)
- 一部の dev tool (pytest, ruff など) は image に含まれない可能性があります
- `.venv` を除外しているので、ローカルで再構築する場合は `uv sync` が必要です
