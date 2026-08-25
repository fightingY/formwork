SCHEMA_VERSION = "2025-legacy"
REQUIRED_FIELD = "id"
ERROR_CODE_MISSING = "E_ID_MISSING"
MAX_PAYLOAD_BYTES = 1024


def validate(payload: dict[str, object]) -> str:
    if REQUIRED_FIELD not in payload:
        return ERROR_CODE_MISSING
    return "ok"
