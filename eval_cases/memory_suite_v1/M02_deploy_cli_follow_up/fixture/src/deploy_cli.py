COMMAND_NAME = "legacy-deploy"
DEFAULT_REGION = "us-east-1"
CONFIG_ENV = "LEGACY_CONFIG"
MAX_PARALLEL = 1


def defaults() -> tuple[str, str, str, int]:
    return COMMAND_NAME, DEFAULT_REGION, CONFIG_ENV, MAX_PARALLEL
