import os

import pytest

from minicc.config import (
    MisconfigurationError,
    load_dotenv_file,
    load_settings,
    load_yaml_config,
)


def _minimal_providers() -> str:
    return """
providers:
  primary:
    base_url: https://provider.test/v1
    model: test-model
default_provider: primary
"""


def test_load_settings_reads_budget_max_turns(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MINICC_API_KEY", "sk-key")
    (tmp_path / "minicc.yaml").write_text(
        _minimal_providers() + "\nbudget:\n  max_turns: 5\n",
        encoding="utf-8",
    )

    settings = load_settings()

    assert settings.budget.max_turns == 5


def test_load_settings_defaults_max_turns_to_zero(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MINICC_API_KEY", "sk-key")
    (tmp_path / "minicc.yaml").write_text(_minimal_providers(), encoding="utf-8")

    settings = load_settings()

    assert settings.budget.max_turns == 0


def test_load_dotenv_file_does_not_override_existing_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MINICC_API_KEY", "from-env")
    dotenv = tmp_path / ".env"
    dotenv.write_text("MINICC_API_KEY=from-file", encoding="utf-8")

    load_dotenv_file(dotenv)

    assert os.environ["MINICC_API_KEY"] == "from-env"


def test_load_settings_reads_providers_and_sections(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("MINICC_API_KEY=from-dotenv\n", encoding="utf-8")
    (tmp_path / "minicc.yaml").write_text(
        """
sandbox:
  image: python:3.12-slim
  cpus: "2"
  memory: 2g
  pids_limit: 128
  network: bridge
budget:
  max_action_timeout_sec: 9
context:
  artifact_preview_chars: 42
  summary_max_chars: 2048
  field_preview_chars: 512
  compaction_strategy: semantic
  semantic_max_input_chars: 4096
  retention_markers: [src/app.py, ROOT_CAUSE]
  prompt_layout: append
providers:
  primary:
    base_url: https://provider.test/v1
    model: test-model
    display_name: Primary Upstream
    headers:
      X-Trace: abc
    json_mode: false
    timeout_ms: 30000
    retry_policy:
      max_retries: 4
      backoff:
        initial_delay_ms: 250
  backup:
    base_url: https://backup.test/v1
    model: backup-model
default_provider: primary
failover:
  chain: [primary, backup]
  on: [RATE_LIMIT, TIMEOUT]
  max_hops: 1
policy:
  require_approval_for_network: false
project:
  milestone: v4.1
workspace:
  ignored_allowlist:
    - generated/runtime.json
    - fixtures/*.db
""",
        encoding="utf-8",
    )

    settings = load_settings()

    route = settings.default_route
    assert settings.default_provider == "primary"
    assert route.name == "primary"
    assert route.base_url == "https://provider.test/v1"
    assert route.model == "test-model"
    assert route.api_key == "from-dotenv"
    assert route.display_name == "Primary Upstream"
    assert route.effective_display_name == "Primary Upstream"
    assert route.headers == {"X-Trace": "abc"}
    assert route.json_mode is False
    assert route.timeout_ms == 30000
    assert route.retry_policy.max_retries == 4
    assert route.retry_policy.backoff.initial_delay_ms == 250

    assert settings.providers["backup"].model == "backup-model"
    assert settings.failover is not None
    assert settings.failover.chain == ("primary", "backup")
    assert settings.failover.on == ("RATE_LIMIT", "TIMEOUT")
    assert settings.failover.max_hops == 1

    assert settings.sandbox.image == "python:3.12-slim"
    assert settings.sandbox.cpus == "2"
    assert settings.sandbox.memory == "2g"
    assert settings.sandbox.pids_limit == 128
    assert settings.sandbox.network == "bridge"
    assert settings.budget.max_action_timeout_sec == 9
    assert settings.context.artifact_preview_chars == 42
    assert settings.context.summary_max_chars == 2048
    assert settings.context.field_preview_chars == 512
    assert settings.context.compaction_strategy == "semantic"
    assert settings.context.semantic_max_input_chars == 4096
    assert settings.context.retention_markers == ("src/app.py", "ROOT_CAUSE")
    assert settings.context.prompt_layout == "append"
    assert settings.policy.require_approval_for_network is False
    assert settings.project.milestone == "v4.1"
    assert settings.workspace.ignored_allowlist == (
        "generated/runtime.json",
        "fixtures/*.db",
    )


def test_load_settings_single_route_defaults_without_explicit_name(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MINICC_API_KEY", "sk-key")
    (tmp_path / "minicc.yaml").write_text(
        """
providers:
  only:
    base_url: https://provider.test/v1
    model: test-model
""",
        encoding="utf-8",
    )

    settings = load_settings()

    assert settings.default_provider == "only"
    assert settings.default_route.name == "only"


def test_load_settings_requires_default_provider_for_multiple_routes(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MINICC_API_KEY", "sk-key")
    (tmp_path / "minicc.yaml").write_text(
        """
providers:
  primary:
    base_url: https://a.test/v1
    model: a
  backup:
    base_url: https://b.test/v1
    model: b
""",
        encoding="utf-8",
    )

    with pytest.raises(MisconfigurationError, match="default_provider"):
        load_settings()


def test_load_settings_env_override_default_route(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MINICC_API_KEY", "sk-key")
    monkeypatch.setenv("MINICC_MODEL", "env-model")
    monkeypatch.setenv("MINICC_PROVIDER_TIMEOUT_SEC", "45")
    monkeypatch.setenv("MINICC_PROVIDER", "backup")
    (tmp_path / "minicc.yaml").write_text(
        """
providers:
  primary:
    base_url: https://a.test/v1
    model: yaml-model
  backup:
    base_url: https://b.test/v1
    model: backup-model
default_provider: primary
""",
        encoding="utf-8",
    )

    settings = load_settings()

    assert settings.default_provider == "backup"
    assert settings.default_route.model == "env-model"
    assert settings.default_route.timeout_ms == 45000


@pytest.mark.parametrize(
    ("yaml_text", "message"),
    [
        (
            "providers:\n  primary:\n    model: test-model\n",
            "base_url",
        ),
        (
            "providers:\n  primary:\n    base_url: https://a.test/v1\n    model: m\n    bogus: 1\n",
            "unknown keys",
        ),
    ],
)
def test_load_settings_fail_fast_on_invalid_providers(
    tmp_path, monkeypatch, yaml_text, message
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MINICC_API_KEY", "sk-key")
    (tmp_path / "minicc.yaml").write_text(yaml_text, encoding="utf-8")

    with pytest.raises(MisconfigurationError, match=message):
        load_settings()


def test_load_settings_missing_model_fails(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MINICC_API_KEY", "sk-key")
    (tmp_path / "minicc.yaml").write_text(
        "providers:\n  primary:\n    base_url: https://a.test/v1\n",
        encoding="utf-8",
    )

    with pytest.raises(MisconfigurationError, match="model"):
        load_settings()


def test_load_settings_requires_api_key(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MINICC_API_KEY", raising=False)
    (tmp_path / "minicc.yaml").write_text(_minimal_providers(), encoding="utf-8")

    with pytest.raises(MisconfigurationError, match="API key"):
        load_settings()


def test_load_settings_api_key_env_accepts_direct_key(tmp_path, monkeypatch) -> None:
    # api_key_env 找不到同名环境变量时，把值本身当密钥（仅用于覆盖兼容行为的测试）。
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MINICC_API_KEY", raising=False)
    (tmp_path / "minicc.yaml").write_text(
        """
providers:
  primary:
    base_url: https://provider.test/v1
    model: test-model
    api_key_env: test-inline-key
default_provider: primary
""",
        encoding="utf-8",
    )

    settings = load_settings()
    assert settings.default_route.api_key == "test-inline-key"


def test_load_settings_accepts_valid_env_name(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CUSTOM_KEY", "sk-custom")
    (tmp_path / "minicc.yaml").write_text(
        """
providers:
  primary:
    base_url: https://provider.test/v1
    model: test-model
    api_key_env: CUSTOM_KEY
default_provider: primary
""",
        encoding="utf-8",
    )

    settings = load_settings()
    assert settings.default_route.name == "primary"


def test_load_settings_reads_direct_api_key(tmp_path, monkeypatch) -> None:
    # 本地 minicc.yaml 允许直填 api_key（该文件已被 .gitignore 忽略，不提交）。
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MINICC_API_KEY", raising=False)
    (tmp_path / "minicc.yaml").write_text(
        """
providers:
  primary:
    base_url: https://provider.test/v1
    model: test-model
    api_key: sk-direct-key-123
default_provider: primary
""",
        encoding="utf-8",
    )

    settings = load_settings()
    assert settings.default_route.api_key == "sk-direct-key-123"


def test_load_settings_failover_unknown_route_fails(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MINICC_API_KEY", "sk-key")
    (tmp_path / "minicc.yaml").write_text(
        """
providers:
  primary:
    base_url: https://a.test/v1
    model: m
default_provider: primary
failover:
  chain: [primary, ghost]
""",
        encoding="utf-8",
    )

    with pytest.raises(MisconfigurationError, match="unknown route"):
        load_settings()


def test_load_settings_failover_unknown_code_fails(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MINICC_API_KEY", "sk-key")
    (tmp_path / "minicc.yaml").write_text(
        """
providers:
  primary:
    base_url: https://a.test/v1
    model: m
failover:
  chain: [primary]
  on: [NOT_A_CODE]
""",
        encoding="utf-8",
    )

    with pytest.raises(MisconfigurationError, match="unknown codes"):
        load_settings()


def test_load_settings_reads_aux(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MINICC_API_KEY", "sk-key")
    (tmp_path / "minicc.yaml").write_text(
        """
providers:
  primary:
    base_url: https://a.test/v1
    model: main-model
  auxroute:
    base_url: https://aux.test/v1
    model: aux-model
default_provider: primary
aux:
  provider: auxroute
  model: aux-override
""",
        encoding="utf-8",
    )

    settings = load_settings()
    assert settings.aux is not None
    assert settings.aux.provider == "auxroute"
    assert settings.aux.model == "aux-override"


def test_load_settings_aux_defaults_to_none(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MINICC_API_KEY", "sk-key")
    (tmp_path / "minicc.yaml").write_text(_minimal_providers(), encoding="utf-8")

    settings = load_settings()
    assert settings.aux is None


def test_load_settings_aux_unknown_route_fails(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MINICC_API_KEY", "sk-key")
    (tmp_path / "minicc.yaml").write_text(
        """
providers:
  primary:
    base_url: https://a.test/v1
    model: m
aux:
  provider: ghost
""",
        encoding="utf-8",
    )

    with pytest.raises(MisconfigurationError, match="unknown route"):
        load_settings()


def test_load_yaml_config_rejects_non_mapping(tmp_path) -> None:
    config = tmp_path / "minicc.yaml"
    config.write_text("- not\n- mapping\n", encoding="utf-8")

    try:
        load_yaml_config(config)
    except ValueError as exc:
        assert "YAML mapping" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_load_settings_rejects_unknown_prompt_layout(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MINICC_API_KEY", "sk-key")
    (tmp_path / "minicc.yaml").write_text(
        _minimal_providers() + "context:\n  prompt_layout: typo\n",
        encoding="utf-8",
    )

    try:
        load_settings()
    except ValueError as exc:
        assert "context.prompt_layout" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_load_settings_reads_context_window_and_ratios(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MINICC_API_KEY", "sk-key")
    (tmp_path / "minicc.yaml").write_text(
        """
providers:
  primary:
    base_url: https://provider.test/v1
    model: test-model
    context_window: 128000
default_provider: primary
context:
  threshold_ratio: 0.75
  retain_ratio: 0.2
  max_overflow_retries: 3
""",
        encoding="utf-8",
    )

    settings = load_settings()

    assert settings.default_route.context_window == 128000
    assert settings.context.threshold_ratio == 0.75
    assert settings.context.retain_ratio == 0.2
    assert settings.context.max_overflow_retries == 3


def test_load_settings_defaults_context_window_to_none(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MINICC_API_KEY", "sk-key")
    (tmp_path / "minicc.yaml").write_text(_minimal_providers(), encoding="utf-8")

    settings = load_settings()

    assert settings.default_route.context_window is None
    assert settings.context.threshold_ratio == 0.8
    assert settings.context.retain_ratio == 0.16
    assert settings.context.max_overflow_retries == 1
