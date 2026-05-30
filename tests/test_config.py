from minicc.config import load_dotenv_file, load_settings


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
