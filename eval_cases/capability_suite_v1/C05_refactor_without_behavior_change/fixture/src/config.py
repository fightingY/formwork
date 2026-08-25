import os


def load_database_url() -> str:
    value = os.getenv("APP_DATABASE_URL")
    if value is None or not value.strip():
        return "sqlite:///app.db"
    return value.strip()


def load_log_level() -> str:
    value = os.getenv("APP_LOG_LEVEL")
    if value is None or not value.strip():
        return "INFO"
    return value.strip().upper()
