from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    temperature: float = 0.0


def load_settings() -> Settings:
    load_dotenv_file(Path.cwd() / ".env")

    temperature_raw = os.getenv("MINICC_TEMPERATURE", "0")
    try:
        temperature = float(temperature_raw)
    except ValueError:
        temperature = 0.0

    return Settings(
        base_url=os.getenv("MINICC_BASE_URL"),
        api_key=os.getenv("MINICC_API_KEY"),
        model=os.getenv("MINICC_MODEL"),
        temperature=temperature,
    )


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
