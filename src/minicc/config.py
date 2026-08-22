from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, cast

import yaml

from minicc.core.provider import (
    ALL_CODES,
    FAILOVER_DEFAULT_ON,
    RetryPolicy,
    resolve_retry_policy,
)

CompactionStrategy = Literal["disabled", "deterministic", "semantic"]
PromptLayout = Literal["rebuild", "append", "epoch", "append_until_compaction"]


class MisconfigurationError(ValueError):
    """minicc.yaml / environment violates the V4.1 provider contract (fail-fast)."""


@dataclass(frozen=True)
class ProviderRoute:
    """One enumerable upstream route.

    ``name`` is the route key in the ``providers:`` map. ``api_key`` is the
    *resolved* key value; it comes directly from the route's ``api_key:`` field
    (only ever written to the local, git-ignored ``minicc.yaml``), or from the
    ``api_key_env`` environment variable, falling back to ``MINICC_API_KEY``.
    """

    name: str
    base_url: str
    api_key: str
    model: str
    display_name: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    timeout_ms: int = 120_000
    stream_idle_timeout_ms: int = 300_000
    json_mode: bool = True
    api: str = "openai-completions"
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)

    @property
    def effective_display_name(self) -> str:
        return self.display_name or self.name


@dataclass(frozen=True)
class ChildProviderConfig:
    """Selected submodel route (V4 delegate / scout / planner / reviewer)."""

    provider: str
    model: str | None = None


@dataclass(frozen=True)
class AuxModelConfig:
    """Selected offline aux model (Meta Review / semantic compaction).

    与主 Agent（``default_provider``）和 V4 child（``child``）解耦：Meta Review、
    语义压缩这类「辅助模型」调用可单独指向一条 route，缺省回退主 route。
    """

    provider: str
    model: str | None = None


@dataclass(frozen=True)
class FailoverConfig:
    """Outermost upstream fallback chain.

    ``chain`` lists route names in fallback order; ``on`` is the set of
    ``LlmFailure`` codes that trigger a hop to the next route. ``max_hops``
    caps the number of hop transitions: ``0`` (default) means "unlimited —
    traverse the whole chain", a positive value aborts after that many hops.
    """

    chain: tuple[str, ...]
    on: tuple[str, ...] = FAILOVER_DEFAULT_ON
    max_hops: int = 0


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
    sandbox: SandboxSettings
    budget: BudgetSettings
    context: ContextSettings
    policy: PolicySettings
    providers: Mapping[str, ProviderRoute] = field(default_factory=dict)
    default_provider: str = ""
    failover: FailoverConfig | None = None
    child: ChildProviderConfig | None = None
    aux: AuxModelConfig | None = None
    project: ProjectSettings = field(default_factory=ProjectSettings)
    workspace: WorkspaceSettings = field(default_factory=WorkspaceSettings)
    tooling: ToolingSettings = field(default_factory=ToolingSettings)

    @property
    def default_route(self) -> ProviderRoute:
        return self.providers[self.default_provider]


_ROUTE_KEYS = frozenset(
    {
        "base_url",
        "api_key",
        "api_key_env",
        "model",
        "display_name",
        "headers",
        "timeout_ms",
        "stream_idle_timeout_ms",
        "json_mode",
        "api",
        "retry_policy",
    }
)

# 合法的环境变量名：字母/数字/下划线，且不以数字开头。用于拦截把真密钥
# 误填进 ``api_key_env`` 的情况（见 `_require_valid_env_name`）。
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _require_valid_env_name(route_name: str, api_key_env: str) -> None:
    """``api_key_env`` 填的是环境变量的**名字**，不是密钥本身。

    ``minicc.yaml`` 会被 git 跟踪并提交到 GitHub，绝不允许明码密钥；真密钥只放
    ``.env`` / 环境变量。合法变量名形如 ``SILICONFLOW_API_KEY``，而真实密钥
    （``sk-…``）几乎必然含连字符等非法字符，据此 fail-fast 拦截，避免用户手滑。
    """
    if _ENV_NAME_RE.fullmatch(api_key_env):
        return
    raise MisconfigurationError(
        f"providers.{route_name}.api_key_env 应该填环境变量的**名字**"
        f"（如 SILICONFLOW_API_KEY），而不是密钥本身。检测到 {api_key_env!r} 不是"
        f"合法的环境变量名：请把真密钥写进 .env（已被 .gitignore 忽略、不会提交），"
        f"这里只留变量名。minicc.yaml 会被 git 提交，绝不能包含真密钥。"
    )


def load_settings() -> Settings:
    load_dotenv_file(Path.cwd() / ".env")
    config = load_yaml_config(Path.cwd() / "minicc.yaml")

    sandbox_config = _dict_at(config, "sandbox")
    budget_config = _dict_at(config, "budget")
    context_config = _dict_at(config, "context")
    policy_config = _dict_at(config, "policy")
    project_config = _dict_at(config, "project")
    workspace_config = _dict_at(config, "workspace")
    tooling_config = _dict_at(config, "tooling")

    fallback_api_key = os.getenv("MINICC_API_KEY")
    routes = _parse_providers(config, fallback_api_key=fallback_api_key)
    default_provider = _resolve_default_provider(config, routes)
    routes = _apply_default_route_env_overrides(routes, default_provider)

    failover = _parse_failover(config.get("failover"))
    if failover is not None:
        for route_name in failover.chain:
            if route_name not in routes:
                raise MisconfigurationError(
                    f"failover.chain references unknown route: {route_name!r}"
                )

    child = _parse_child(_dict_at(config, "child"), default_provider)
    if child is not None and child.provider not in routes:
        raise MisconfigurationError(
            f"child.provider references unknown route: {child.provider!r}"
        )

    aux = _parse_aux(_dict_at(config, "aux"), default_provider)
    if aux is not None and aux.provider not in routes:
        raise MisconfigurationError(
            f"aux.provider references unknown route: {aux.provider!r}"
        )

    return Settings(
        sandbox=SandboxSettings(
            image=_str_config(sandbox_config, "image", "python:3.11-slim"),
            mode=_str_config(sandbox_config, "mode", "locked"),
            cpus=_str_config(sandbox_config, "cpus", "1"),
            memory=_str_config(sandbox_config, "memory", "1g"),
            pids_limit=_int_config(sandbox_config, "pids_limit", 256),
            network=_str_config(sandbox_config, "network", "none"),
        ),
        budget=BudgetSettings(
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
            semantic_max_input_chars=_int_config(context_config, "semantic_max_input_chars", 60_000),
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
        providers=routes,
        default_provider=default_provider,
        failover=failover,
        child=child,
        aux=aux,
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


def _parse_providers(
    config: dict[str, Any],
    *,
    fallback_api_key: str | None,
) -> dict[str, ProviderRoute]:
    raw_providers = config.get("providers")
    if not isinstance(raw_providers, Mapping) or not raw_providers:
        raise MisconfigurationError(
            "no providers configured; add a non-empty `providers:` section to minicc.yaml"
        )

    routes: dict[str, ProviderRoute] = {}
    for name, raw_route in raw_providers.items():
        route_cfg = raw_route if isinstance(raw_route, Mapping) else {}
        if not isinstance(raw_route, Mapping):
            raise MisconfigurationError(f"providers.{name} must be a mapping")
        unknown = set(route_cfg) - _ROUTE_KEYS
        if unknown:
            raise MisconfigurationError(
                f"providers.{name} has unknown keys: {sorted(unknown)}"
            )

        base_url = str(route_cfg.get("base_url") or "").strip()
        if not base_url:
            raise MisconfigurationError(f"providers.{name}.base_url is required")

        model = str(route_cfg.get("model") or "").strip()
        if not model:
            raise MisconfigurationError(f"providers.{name}.model is required")

        api_key = str(route_cfg.get("api_key") or "").strip()
        api_key_env = str(route_cfg.get("api_key_env") or "MINICC_API_KEY")
        if not api_key:
            _require_valid_env_name(name, api_key_env)
            api_key = os.getenv(api_key_env) or fallback_api_key or ""
        if not api_key:
            raise MisconfigurationError(
                f"providers.{name}: no API key found (set api_key, {api_key_env!r}, "
                f"or MINICC_API_KEY)"
            )

        api = _str_config(route_cfg, "api", "openai-completions")
        if api != "openai-completions":
            raise MisconfigurationError(
                f"providers.{name}.api must be 'openai-completions' (got {api!r})"
            )

        headers = _string_map(route_cfg.get("headers"))
        retry_policy = resolve_retry_policy(_dict_at(route_cfg, "retry_policy"))

        routes[str(name)] = ProviderRoute(
            name=str(name),
            base_url=base_url,
            api_key=api_key,
            model=model,
            display_name=None
            if route_cfg.get("display_name") is None
            else str(route_cfg["display_name"]),
            headers=headers,
            timeout_ms=_int_config(route_cfg, "timeout_ms", 120_000),
            stream_idle_timeout_ms=_int_config(
                route_cfg,
                "stream_idle_timeout_ms",
                300_000,
            ),
            json_mode=_bool_config(route_cfg, "json_mode", True),
            api=api,
            retry_policy=retry_policy,
        )
    return routes


def _resolve_default_provider(config: dict[str, Any], routes: dict[str, ProviderRoute]) -> str:
    default = os.getenv("MINICC_PROVIDER") or _str_config(config, "default_provider", "")
    if not default:
        # 只配了一条 route 时允许省略 default_provider。
        if len(routes) == 1:
            return next(iter(routes))
        raise MisconfigurationError(
            "default_provider is required when multiple providers are configured"
        )
    if default not in routes:
        raise MisconfigurationError(f"default_provider {default!r} is not a configured route")
    return default


def _apply_default_route_env_overrides(
    routes: dict[str, ProviderRoute],
    default_provider: str,
) -> dict[str, ProviderRoute]:
    """MINICC_MODEL / MINICC_PROVIDER_TIMEOUT_SEC 只作用于默认 route。"""
    route = routes[default_provider]
    model_override = os.getenv("MINICC_MODEL")
    timeout_override = os.getenv("MINICC_PROVIDER_TIMEOUT_SEC")
    if model_override or (timeout_override is not None and timeout_override.strip()):
        if model_override:
            route = replace(route, model=model_override)
        if timeout_override is not None and timeout_override.strip():
            try:
                timeout_ms = int(float(timeout_override) * 1000)
            except ValueError:
                raise MisconfigurationError(
                    "MINICC_PROVIDER_TIMEOUT_SEC must be a number of seconds"
                ) from None
            route = replace(route, timeout_ms=timeout_ms)
        routes = dict(routes)
        routes[default_provider] = route
    return routes


def _parse_failover(raw: Any) -> FailoverConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise MisconfigurationError("failover must be a mapping")
    # YAML 1.1（PyYAML safe_load）会把裸键 ``on:`` 解析成布尔 ``True``；这里把它
    # 还原成字符串键，避免 `failover.on` 因 YAML 类型而失效。
    raw = {
        ("on" if key is True else ("off" if key is False else key)): value
        for key, value in raw.items()
    }
    unknown = set(raw) - {"chain", "on", "max_hops"}
    if unknown:
        raise MisconfigurationError(f"failover has unknown keys: {sorted(unknown)}")
    chain = tuple(_str_tuple_config(raw, "chain"))
    if not chain:
        raise MisconfigurationError("failover.chain must list at least one route")
    on: tuple[str, ...] = _str_tuple_config(raw, "on") or FAILOVER_DEFAULT_ON
    invalid_on = [code for code in on if code not in ALL_CODES]
    if invalid_on:
        raise MisconfigurationError(f"failover.on has unknown codes: {invalid_on}")
    max_hops = _int_config(raw, "max_hops", 0)
    if max_hops < 0:
        raise MisconfigurationError("failover.max_hops must be non-negative")
    return FailoverConfig(chain=chain, on=on, max_hops=max_hops)


def _parse_child(cfg: dict[str, Any], default_provider: str) -> ChildProviderConfig | None:
    provider = os.getenv("MINICC_CHILD_PROVIDER") or _str_config(cfg, "provider", "")
    model = os.getenv("MINICC_CHILD_MODEL") or _optional_str(cfg, "model")
    if not provider and not model:
        return None
    return ChildProviderConfig(provider=provider or default_provider, model=model)


def _parse_aux(cfg: dict[str, Any], default_provider: str) -> AuxModelConfig | None:
    provider = os.getenv("MINICC_AUX_PROVIDER") or _str_config(cfg, "provider", "")
    model = os.getenv("MINICC_AUX_MODEL") or _optional_str(cfg, "model")
    if not provider and not model:
        return None
    return AuxModelConfig(provider=provider or default_provider, model=model)


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


def _dict_at(config: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = config.get(key, {})
    if isinstance(value, dict):
        return value
    return {}


def _string_map(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): str(item) for key, item in value.items() if item is not None}


def _str_config(config: Mapping[str, Any], key: str, default: str) -> str:
    value = config.get(key, default)
    if value is None:
        return default
    return str(value)


def _optional_str(config: Mapping[str, Any], key: str) -> str | None:
    value = config.get(key)
    if value is None:
        return None
    return str(value)


def _int_config(config: Mapping[str, Any], key: str, default: int) -> int:
    value = config.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _str_tuple_config(config: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = config.get(key, ())
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if str(item).strip())


def _compaction_strategy(config: Mapping[str, Any]) -> CompactionStrategy:
    value = str(config.get("compaction_strategy", "deterministic")).strip().lower()
    if value not in {"disabled", "deterministic", "semantic"}:
        raise ValueError("context.compaction_strategy must be disabled, deterministic, or semantic")
    return cast(CompactionStrategy, value)


def _prompt_layout(config: Mapping[str, Any]) -> PromptLayout:
    value = str(config.get("prompt_layout", "rebuild")).strip().lower()
    if value not in {"rebuild", "append", "epoch", "append_until_compaction"}:
        raise ValueError(
            "context.prompt_layout must be rebuild, append, epoch, or append_until_compaction"
        )
    return cast(PromptLayout, value)


def _bool_config(config: Mapping[str, Any], key: str, default: bool) -> bool:
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