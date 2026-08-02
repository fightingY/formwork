SERVICE_NAME = "legacy-router"
HEALTH_PATH = "/health"
READY_BODY = "legacy-ready"
RETRY_BUDGET = 1


def readiness_response() -> tuple[str, str, int]:
    return HEALTH_PATH, READY_BODY, RETRY_BUDGET
