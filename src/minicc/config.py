from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import yaml

CompactionStrategy = Literal["disabled", "deterministic", "semantic"]
PromptLayout = Literal["rebuild", "append", "epoch", "append_until_compaction"]


@dataclass(frozen=True)
class ProviderSettings:
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    temperature: float = 0.0
    stream: bool = False
    include_usage: bool = True
    json_mode: bool = True
    timeout_sec: float = 120.0
    max_completion_tokens: int = 2_048
    max_retries: int = 2


@dataclass(frozen=True)
class SandboxSettings:
    image: str = "python:3.11-slim"
    mode: str = "locked"
    cpus: str = "1"
    memory: str = "1g"
    pids_limit: int = 256
    network: str = "none"


@dataclass(frozen=True)
class BudgetSettings:
    max_turns: int = 12
    max_bash_actions: int = 30
    max_seconds: int = 900
    max_action_timeout_sec: int = 120


@dataclass(frozen=True)
class ContextSettings:
    max_prompt_chars: int = 120_000
    recent_turns: int = 6
    artifact_preview_chars: int = 12_000
    summary_max_chars: int = 12_000
    field_preview_chars: int = 4_000
    compaction_strategy: CompactionStrategy = "deterministic"
    semantic_max_input_chars: int = 60_000
    semantic_max_completion_tokens: int = 2_048
    retention_markers: tuple[str, ...] = ()
    prompt_layout: PromptLayout = "rebuild"


@dataclass(frozen=True)
class PolicySettings:
    require_approval_for_network: bool = True
    deny_sudo: bool = True
    require_approval_for_destructive: bool = True


@dataclass(frozen=True)
class ProjectSettings:
    milestone: str = ""


@dataclass(frozen=True)
class WorkspaceSettings:
    ignored_allowlist: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolingSettings:
    profile: str = "baseline-bash"
    max_parallel_tool_calls: int = 4
    max_tool_calls_per_step: int = 16


@dataclass(frozen=True)
class Settings:
    provider: ProviderSettings
    sandbox: SandboxSettings
    budget: BudgetSettings
    context: ContextSettings
    policy: PolicySettings
    project: ProjectSettings = field(default_factory=ProjectSettings)
    workspace: WorkspaceSettings = field(default_factory=WorkspaceSettings)
    tooling: ToolingSettings = field(default_factory=ToolingSettings)

    @property
    def base_url(self) -> str | None:
        return self.provider.base_url

    @property
    def api_key(self) -> str | None:
        return self.provider.api_key

    @property
    def model(self) -> str | None:
        return self.provider.model

    @property
    def temperature(self) -> float:
        return self.provider.temperature


def load_settings() -> Settings:
    load_dotenv_file(Path.cwd() / ".env")
    config = load_yaml_config(Path.cwd() / "minicc.yaml")

    provider_config = _dict_at(config, "provider")
    sandbox_config = _dict_at(config, "sandbox")
    budget_config = _dict_at(config, "budget")
    context_config = _dict_at(config, "context")
    policy_config = _dict_at(config, "policy")
    project_config = _dict_at(config, "project")
    workspace_config = _dict_at(config, "workspace")
    tooling_config = _dict_at(config, "tooling")

    return Settings(
        provider=ProviderSettings(
            base_url=_env_or_config("MINICC_BASE_URL", provider_config, "base_url"),
            api_key=os.getenv("MINICC_API_KEY"),
            model=_env_or_config("MINICC_MODEL", provider_config, "model"),
            temperature=_float_env_or_config(
                "MINICC_TEMPERATURE",
                provider_config,
                "temperature",
                0.0,
            ),
            stream=_bool_env_or_config("MINICC_STREAM", provider_config, "stream", False),
            include_usage=_bool_env_or_config(
                "MINICC_INCLUDE_USAGE",
                provider_config,
                "include_usage",
                True,
            ),
            json_mode=_bool_env_or_config(
                "MINICC_JSON_MODE",
                provider_config,
                "json_mode",
                True,
            ),
            max_completion_tokens=_int_config(
                provider_config,
                "max_completion_tokens",
                2_048,
            ),
            max_retries=_int_config(provider_config, "max_retries", 2),
            timeout_sec=_float_env_or_config(
                "MINICC_PROVIDER_TIMEOUT_SEC",
                provider_config,
                "timeout_sec",
                120.0,
            ),
        ),
        sandbox=SandboxSettings(
            image=_str_config(sandbox_config, "image", "python:3.11-slim"),
            mode=_str_config(sandbox_config, "mode", "locked"),
            cpus=_str_config(sandbox_config, "cpus", "1"),
            memory=_str_config(sandbox_config, "memory", "1g"),
            pids_limit=_int_config(sandbox_config, "pids_limit", 256),
            network=_str_config(sandbox_config, "network", "none"),
        ),
        budget=BudgetSettings(
            max_turns=_int_config(budget_config, "max_turns", 12),
            max_bash_actions=_int_config(budget_config, "max_bash_actions", 30),
            max_seconds=_int_config(budget_config, "max_seconds", 900),
            max_action_timeout_sec=_int_config(budget_config, "max_action_timeout_sec", 120),
        ),
        context=ContextSettings(
            max_prompt_chars=_int_config(context_config, "max_prompt_chars", 120_000),
            recent_turns=_int_config(context_config, "recent_turns", 6),
            artifact_preview_chars=_int_config(context_config, "artifact_preview_chars", 12_000),
            summary_max_chars=_int_config(context_config, "summary_max_chars", 12_000),
            field_preview_chars=_int_config(context_config, "field_preview_chars", 4_000),
            compaction_strategy=_compaction_strategy(context_config),
            semantic_max_input_chars=_int_config(
                context_config,
                "semantic_max_input_chars",
                60_000,
            ),
            semantic_max_completion_tokens=_int_config(
                context_config,
                "semantic_max_completion_tokens",
                2_048,
            ),
            retention_markers=_str_tuple_config(context_config, "retention_markers"),
            prompt_layout=_prompt_layout(context_config),
        ),
        policy=PolicySettings(
            require_approval_for_network=_bool_config(
                policy_config,
                "require_approval_for_network",
                True,
            ),
            deny_sudo=_bool_config(policy_config, "deny_sudo", True),
            require_approval_for_destructive=_bool_config(
                policy_config,
                "require_approval_for_destructive",
                True,
            ),
        ),
        project=ProjectSettings(
            milestone=_str_config(project_config, "milestone", ""),
        ),
        workspace=WorkspaceSettings(
            ignored_allowlist=_str_tuple_config(workspace_config, "ignored_allowlist"),
        ),
        tooling=ToolingSettings(
            profile=_str_config(tooling_config, "profile", "baseline-bash"),
            max_parallel_tool_calls=_int_config(tooling_config, "max_parallel_tool_calls", 4),
            max_tool_calls_per_step=_int_config(tooling_config, "max_tool_calls_per_step", 16),
        ),
    )


def load_yaml_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("minicc.yaml must contain a YAML mapping at the top level.")
    return data


def load_dotenv_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = _strip_env_value(value.strip())
        if key and key not in os.environ:
            os.environ[key] = value


def _strip_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _dict_at(config: dict[str, Any], key: str) -> dict[str, Any]:
    value = config.get(key, {})
    if isinstance(value, dict):
        return value
    return {}


def _env_or_config(env_name: str, config: dict[str, Any], key: str) -> str | None:
    env_value = os.getenv(env_name)
    if env_value is not None:
        return env_value
    value = config.get(key)
    if value is None:
        return None
    return str(value)


def _str_config(config: dict[str, Any], key: str, default: str) -> str:
    value = config.get(key, default)
    if value is None:
        return default
    return str(value)


def _int_config(config: dict[str, Any], key: str, default: int) -> int:
    value = config.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _str_tuple_config(config: dict[str, Any], key: str) -> tuple[str, ...]:
    value = config.get(key, ())
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


def _compaction_strategy(config: dict[str, Any]) -> CompactionStrategy:
    value = str(config.get("compaction_strategy", "deterministic")).strip().lower()
    if value not in {"disabled", "deterministic", "semantic"}:
        raise ValueError("context.compaction_strategy must be disabled, deterministic, or semantic")
    return cast(CompactionStrategy, value)


def _prompt_layout(config: dict[str, Any]) -> PromptLayout:
    value = str(config.get("prompt_layout", "rebuild")).strip().lower()
    if value not in {"rebuild", "append", "epoch", "append_until_compaction"}:
        raise ValueError(
            "context.prompt_layout must be rebuild, append, epoch, or append_until_compaction"
        )
    return cast(PromptLayout, value)


def _float_env_or_config(
    env_name: str,
    config: dict[str, Any],
    key: str,
    default: float,
) -> float:
    raw_value = os.getenv(env_name)
    if raw_value is None:
        raw_value = config.get(key, default)
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return default


def _bool_env_or_config(
    env_name: str,
    config: dict[str, Any],
    key: str,
    default: bool,
) -> bool:
    raw_value = os.getenv(env_name)
    if raw_value is None:
        raw_value = config.get(key, default)
    return _bool_value(raw_value, default)


def _bool_config(config: dict[str, Any], key: str, default: bool) -> bool:
    return _bool_value(config.get(key, default), default)


def _bool_value(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default
