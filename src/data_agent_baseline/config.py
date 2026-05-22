from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _default_dataset_root() -> Path:
    return PROJECT_ROOT / "data" / "public" / "input"


def _default_run_output_dir() -> Path:
    return PROJECT_ROOT / "artifacts" / "runs"


@dataclass(frozen=True, slots=True)
class DatasetConfig:
    root_path: Path = field(default_factory=_default_dataset_root)


@dataclass(frozen=True, slots=True)
class AgentConfig:
    model: str = "gpt-4.1-mini"
    api_base: str = "https://api.openai.com/v1"
    api_key: str = ""
    max_steps: int = 20
    temperature: float = 0.0
    max_output_tokens: int = 4096
    request_timeout_seconds: float = 180.0
    max_retries: int = 1


@dataclass(frozen=True, slots=True)
class RunConfig:
    output_dir: Path = field(default_factory=_default_run_output_dir)
    run_id: str | None = None
    max_workers: int = 6
    task_timeout_seconds: int = 480


@dataclass(frozen=True, slots=True)
class DifficultyRuntimeOverride:
    max_steps: int | None = None
    max_retries: int | None = None
    task_timeout_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class ResolvedRuntimeConfig:
    max_steps: int
    max_retries: int
    task_timeout_seconds: int


@dataclass(frozen=True, slots=True)
class AppConfig:
    dataset: DatasetConfig = field(default_factory=DatasetConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    run: RunConfig = field(default_factory=RunConfig)
    difficulty_overrides: dict[str, DifficultyRuntimeOverride] = field(default_factory=dict)


def _path_value(raw_value: str | None, default_value: Path) -> Path:
    if not raw_value:
        return default_value
    candidate = Path(raw_value)
    if candidate.is_absolute():
        return candidate
    return (PROJECT_ROOT / candidate).resolve()


def _optional_int(payload: dict, key: str) -> int | None:
    """設定 dict から任意の整数値を取り出す。"""

    raw_value = payload.get(key)
    if raw_value is None:
        return None
    return int(raw_value)


def _load_difficulty_overrides(payload: dict) -> dict[str, DifficultyRuntimeOverride]:
    """難易度別の実行設定 override を読み込む。"""

    overrides_payload = payload.get("difficulty_overrides", {})
    if not isinstance(overrides_payload, dict):
        raise ValueError("difficulty_overrides must be a mapping.")

    overrides: dict[str, DifficultyRuntimeOverride] = {}
    for raw_difficulty, raw_override in overrides_payload.items():
        if raw_override is None:
            continue
        if not isinstance(raw_override, dict):
            raise ValueError(f"difficulty_overrides.{raw_difficulty} must be a mapping.")
        difficulty = str(raw_difficulty).strip().lower()
        if not difficulty:
            raise ValueError("difficulty_overrides keys must not be empty.")
        overrides[difficulty] = DifficultyRuntimeOverride(
            max_steps=_optional_int(raw_override, "max_steps"),
            max_retries=_optional_int(raw_override, "max_retries"),
            task_timeout_seconds=_optional_int(raw_override, "task_timeout_seconds"),
        )
    return overrides


def load_app_config(config_path: Path) -> AppConfig:
    """YAML 設定ファイルを読み込み、アプリ設定として返す。"""

    payload = yaml.safe_load(config_path.read_text()) or {}
    dataset_defaults = DatasetConfig()
    agent_defaults = AgentConfig()
    run_defaults = RunConfig()

    dataset_payload = payload.get("dataset", {})
    agent_payload = payload.get("agent", {})
    run_payload = payload.get("run", {})

    dataset_config = DatasetConfig(
        root_path=_path_value(dataset_payload.get("root_path"), dataset_defaults.root_path),
    )
    agent_config = AgentConfig(
        model=str(agent_payload.get("model", agent_defaults.model)),
        api_base=str(agent_payload.get("api_base", agent_defaults.api_base)),
        api_key=str(agent_payload.get("api_key", agent_defaults.api_key)),
        max_steps=int(agent_payload.get("max_steps", agent_defaults.max_steps)),
        temperature=float(agent_payload.get("temperature", agent_defaults.temperature)),
        max_output_tokens=int(agent_payload.get("max_output_tokens", agent_defaults.max_output_tokens)),
        request_timeout_seconds=float(
            agent_payload.get(
                "request_timeout_seconds",
                agent_defaults.request_timeout_seconds,
            )
        ),
        max_retries=int(agent_payload.get("max_retries", agent_defaults.max_retries)),
    )
    raw_run_id = run_payload.get("run_id")
    run_id = run_defaults.run_id
    if raw_run_id is not None:
        normalized_run_id = str(raw_run_id).strip()
        run_id = normalized_run_id or None

    run_config = RunConfig(
        output_dir=_path_value(run_payload.get("output_dir"), run_defaults.output_dir),
        run_id=run_id,
        max_workers=int(run_payload.get("max_workers", run_defaults.max_workers)),
        task_timeout_seconds=int(run_payload.get("task_timeout_seconds", run_defaults.task_timeout_seconds)),
    )
    return AppConfig(
        dataset=dataset_config,
        agent=agent_config,
        run=run_config,
        difficulty_overrides=_load_difficulty_overrides(payload),
    )


def resolve_runtime_config(config: AppConfig, difficulty: str) -> ResolvedRuntimeConfig:
    """難易度別 override を反映したタスク実行設定を返す。"""

    override = config.difficulty_overrides.get(difficulty.strip().lower())
    return ResolvedRuntimeConfig(
        max_steps=override.max_steps if override and override.max_steps is not None else config.agent.max_steps,
        max_retries=override.max_retries if override and override.max_retries is not None else config.agent.max_retries,
        task_timeout_seconds=(
            override.task_timeout_seconds
            if override and override.task_timeout_seconds is not None
            else config.run.task_timeout_seconds
        ),
    )


def apply_model_env_overrides(config: AppConfig) -> AppConfig:
    """評価環境の MODEL_* 変数があれば agent 設定へ優先適用する。"""

    model = os.getenv("MODEL_NAME", "").strip() or config.agent.model
    api_base = os.getenv("MODEL_API_URL", "").strip() or config.agent.api_base
    api_key = os.getenv("MODEL_API_KEY", "").strip() or config.agent.api_key
    if (
        model == config.agent.model
        and api_base == config.agent.api_base
        and api_key == config.agent.api_key
    ):
        return config

    agent_config = AgentConfig(
        model=model,
        api_base=api_base,
        api_key=api_key,
        max_steps=config.agent.max_steps,
        temperature=config.agent.temperature,
        max_output_tokens=config.agent.max_output_tokens,
        request_timeout_seconds=config.agent.request_timeout_seconds,
        max_retries=config.agent.max_retries,
    )
    return AppConfig(
        dataset=config.dataset,
        agent=agent_config,
        run=config.run,
        difficulty_overrides=config.difficulty_overrides,
    )
