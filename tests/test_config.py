from minicc.config import load_dotenv_file, load_settings, load_yaml_config


def test_load_dotenv_file_sets_missing_values(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("MINICC_BASE_URL", raising=False)
    monkeypatch.delenv("MINICC_API_KEY", raising=False)
    monkeypatch.delenv("MINICC_MODEL", raising=False)
    monkeypatch.delenv("MINICC_TEMPERATURE", raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "\n".join(
            [
                "MINICC_BASE_URL=https://example.test/v1",
                "MINICC_API_KEY='secret-key'",
                'MINICC_MODEL="demo-model"',
                "MINICC_TEMPERATURE=0.2",
            ]
        ),
        encoding="utf-8",
    )

    load_dotenv_file(dotenv)
    settings = load_settings()

    assert settings.base_url == "https://example.test/v1"
    assert settings.api_key == "secret-key"
    assert settings.model == "demo-model"
    assert settings.temperature == 0.2


def test_env_vars_override_dotenv_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MINICC_API_KEY", "from-env")
    dotenv = tmp_path / ".env"
    dotenv.write_text("MINICC_API_KEY=from-file", encoding="utf-8")

    load_dotenv_file(dotenv)

    assert load_settings().api_key == "from-env"


def test_load_settings_reads_minicc_yaml(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MINICC_BASE_URL", raising=False)
    monkeypatch.delenv("MINICC_API_KEY", raising=False)
    monkeypatch.delenv("MINICC_MODEL", raising=False)
    monkeypatch.delenv("MINICC_TEMPERATURE", raising=False)
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
provider:
  base_url: https://provider.test/v1
  model: test-model
  temperature: 0.7
  max_retries: 4
  stream: true
  include_usage: false
  json_mode: false
policy:
  require_approval_for_network: false
project:
  milestone: stable-v2.1
workspace:
  ignored_allowlist:
    - generated/runtime.json
    - fixtures/*.db
""",
        encoding="utf-8",
    )

    settings = load_settings()

    assert settings.api_key == "from-dotenv"
    assert settings.provider.base_url == "https://provider.test/v1"
    assert settings.provider.model == "test-model"
    assert settings.provider.temperature == 0.7
    assert settings.provider.stream is True
    assert settings.provider.include_usage is False
    assert settings.provider.json_mode is False
    assert settings.provider.max_retries == 4
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
    assert settings.project.milestone == "stable-v2.1"
    assert settings.workspace.ignored_allowlist == (
        "generated/runtime.json",
        "fixtures/*.db",
    )


def test_env_overrides_minicc_yaml_provider_fields(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MINICC_BASE_URL", "https://env.test/v1")
    monkeypatch.setenv("MINICC_MODEL", "env-model")
    monkeypatch.setenv("MINICC_TEMPERATURE", "0.1")
    (tmp_path / "minicc.yaml").write_text(
        """
provider:
  base_url: https://yaml.test/v1
  model: yaml-model
  temperature: 0.9
""",
        encoding="utf-8",
    )

    settings = load_settings()

    assert settings.provider.base_url == "https://env.test/v1"
    assert settings.provider.model == "env-model"
    assert settings.provider.temperature == 0.1


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
    (tmp_path / "minicc.yaml").write_text(
        "context:\n  prompt_layout: typo\n",
        encoding="utf-8",
    )

    try:
        load_settings()
    except ValueError as exc:
        assert "context.prompt_layout" in str(exc)
    else:
        raise AssertionError("Expected ValueError")
