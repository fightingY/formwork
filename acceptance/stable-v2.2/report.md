# miniCC Stable V2.2 Working-Memory Acceptance

Status: **PASS**
Source commit: `15fadae08d7d424853ba24b4dca534501493a183`

## Aggregate

- Cases / pairs / runs: `3` / `9` / `27`
- Follow-up key-fact accuracy M0/M1: `100.00%` / `100.00%`
- Repeated source-file reads M0 -> M1: `9 -> 0`
- Prompt tokens M0 -> M1: `36878 -> 26617`
- Old-run leaks / irrelevant injections / invalid adoptions: `0` / `0` / `0`
- Provider retried requests: `0`

## Cases and raw commands

### M01_service_contract_follow_up

Suite: `suite-20260802-130812-5862115e`

- Attempt 1: M0 reads `1`, M1 reads `0`
  - M0 `["cat docs/SERVICE_CONTRACT.md", "cat src/service.py", "cat > src/service.py << 'EOF'\nSERVICE_NAME = \"atlas-router\"\nHEALTH_PATH = \"/readyz\"\nREADY_BODY = \"atlas-ready-v2\"\nRETRY_BUDGET = 4\n\n\ndef readiness_response() -> tuple[str, str, int]:\n    return HEALTH_PATH, READY_BODY, RETRY_BUDGET\nEOF\npython -m unittest discover -s tests -v"]`
  - M1 `["cat src/service.py", "sed -i 's/SERVICE_NAME = \"legacy-router\"/SERVICE_NAME = \"atlas-router\"/; s/HEALTH_PATH = \"\\/health\"/HEALTH_PATH = \"\\/readyz\"/; s/READY_BODY = \"legacy-ready\"/READY_BODY = \"atlas-ready-v2\"/; s/RETRY_BUDGET = 1/RETRY_BUDGET = 4/' src/service.py && python -m unittest discover -s tests -v"]`
- Attempt 2: M0 reads `1`, M1 reads `0`
  - M0 `["cat docs/SERVICE_CONTRACT.md", "cat src/service.py", "cat > src/service.py << 'EOF'\nSERVICE_NAME = \"atlas-router\"\nHEALTH_PATH = \"/readyz\"\nREADY_BODY = \"atlas-ready-v2\"\nRETRY_BUDGET = 4\n\n\ndef readiness_response() -> tuple[str, str, int]:\n    return HEALTH_PATH, READY_BODY, RETRY_BUDGET\nEOF\npython -m unittest discover -s tests -v"]`
  - M1 `["cat src/service.py", "cat > src/service.py << 'EOF'\nSERVICE_NAME = \"atlas-router\"\nHEALTH_PATH = \"/readyz\"\nREADY_BODY = \"atlas-ready-v2\"\nRETRY_BUDGET = 4\n\n\ndef readiness_response() -> tuple[str, str, int]:\n    return HEALTH_PATH, READY_BODY, RETRY_BUDGET\nEOF\npython -m unittest discover -s tests -v"]`
- Attempt 3: M0 reads `1`, M1 reads `0`
  - M0 `["cat docs/SERVICE_CONTRACT.md", "cat src/service.py", "cat > src/service.py << 'EOF'\nSERVICE_NAME = \"atlas-router\"\nHEALTH_PATH = \"/readyz\"\nREADY_BODY = \"atlas-ready-v2\"\nRETRY_BUDGET = 4\n\n\ndef readiness_response() -> tuple[str, str, int]:\n    return HEALTH_PATH, READY_BODY, RETRY_BUDGET\nEOF\npython -m unittest discover -s tests -v"]`
  - M1 `["cat src/service.py", "cat > src/service.py << 'EOF'\nSERVICE_NAME = \"atlas-router\"\nHEALTH_PATH = \"/readyz\"\nREADY_BODY = \"atlas-ready-v2\"\nRETRY_BUDGET = 4\n\n\ndef readiness_response() -> tuple[str, str, int]:\n    return HEALTH_PATH, READY_BODY, RETRY_BUDGET\nEOF\npython -m unittest discover -s tests -v"]`

### M02_deploy_cli_follow_up

Suite: `suite-20260802-131105-3763ea38`

- Attempt 1: M0 reads `1`, M1 reads `0`
  - M0 `["cat src/deploy_cli.py", "cat docs/DEPLOYMENT_CONTRACT.md", "sed -i 's/COMMAND_NAME = \"legacy-deploy\"/COMMAND_NAME = \"orbit-deploy\"/; s/DEFAULT_REGION = \"us-east-1\"/DEFAULT_REGION = \"ap-southeast-1\"/; s/CONFIG_ENV = \"LEGACY_CONFIG\"/CONFIG_ENV = \"ORBIT_CONFIG\"/; s/MAX_PARALLEL = 1/MAX_PARALLEL = 6/' src/deploy_cli.py && python -m unittest discover -s tests -v"]`
  - M1 `["cat src/deploy_cli.py", "sed -i 's/COMMAND_NAME = \"legacy-deploy\"/COMMAND_NAME = \"orbit-deploy\"/; s/DEFAULT_REGION = \"us-east-1\"/DEFAULT_REGION = \"ap-southeast-1\"/; s/CONFIG_ENV = \"LEGACY_CONFIG\"/CONFIG_ENV = \"ORBIT_CONFIG\"/; s/MAX_PARALLEL = 1/MAX_PARALLEL = 6/' src/deploy_cli.py && python -m unittest discover -s tests -v"]`
- Attempt 2: M0 reads `1`, M1 reads `0`
  - M0 `["ls -la", "cat docs/DEPLOYMENT_CONTRACT.md", "cat src/deploy_cli.py", "cat > src/deploy_cli.py << 'EOF'\nCOMMAND_NAME = \"orbit-deploy\"\nDEFAULT_REGION = \"ap-southeast-1\"\nCONFIG_ENV = \"ORBIT_CONFIG\"\nMAX_PARALLEL = 6\n\n\ndef defaults() -> tuple[str, str, str, int]:\n    return COMMAND_NAME, DEFAULT_REGION, CONFIG_ENV, MAX_PARALLEL\nEOF\npython -m unittest discover -s tests -v"]`
  - M1 `["cat src/deploy_cli.py", "cat > src/deploy_cli.py << 'EOF'\nCOMMAND_NAME = \"orbit-deploy\"\nDEFAULT_REGION = \"ap-southeast-1\"\nCONFIG_ENV = \"ORBIT_CONFIG\"\nMAX_PARALLEL = 6\n\n\ndef defaults() -> tuple[str, str, str, int]:\n    return COMMAND_NAME, DEFAULT_REGION, CONFIG_ENV, MAX_PARALLEL\nEOF\npython -m unittest discover -s tests -v"]`
- Attempt 3: M0 reads `1`, M1 reads `0`
  - M0 `["ls -la", "cat docs/DEPLOYMENT_CONTRACT.md", "cat src/deploy_cli.py", "cat > src/deploy_cli.py << 'EOF'\nCOMMAND_NAME = \"orbit-deploy\"\nDEFAULT_REGION = \"ap-southeast-1\"\nCONFIG_ENV = \"ORBIT_CONFIG\"\nMAX_PARALLEL = 6\n\n\ndef defaults() -> tuple[str, str, str, int]:\n    return COMMAND_NAME, DEFAULT_REGION, CONFIG_ENV, MAX_PARALLEL\nEOF\npython -m unittest discover -s tests -v"]`
  - M1 `["cat src/deploy_cli.py", "sed -i 's/COMMAND_NAME = \"legacy-deploy\"/COMMAND_NAME = \"orbit-deploy\"/; s/DEFAULT_REGION = \"us-east-1\"/DEFAULT_REGION = \"ap-southeast-1\"/; s/CONFIG_ENV = \"LEGACY_CONFIG\"/CONFIG_ENV = \"ORBIT_CONFIG\"/; s/MAX_PARALLEL = 1/MAX_PARALLEL = 6/' src/deploy_cli.py && python -m unittest discover -s tests -v"]`

### M03_validator_contract_follow_up

Suite: `suite-20260802-131409-441c511f`

- Attempt 1: M0 reads `1`, M1 reads `0`
  - M0 `["cat docs/VALIDATION_CONTRACT.md", "cat src/validator.py", "sed -i 's/SCHEMA_VERSION = \"2025-legacy\"/SCHEMA_VERSION = \"2026-08\"/; s/REQUIRED_FIELD = \"id\"/REQUIRED_FIELD = \"request_id\"/; s/ERROR_CODE_MISSING = \"E_ID_MISSING\"/ERROR_CODE_MISSING = \"E_REQUEST_ID_REQUIRED\"/; s/MAX_PAYLOAD_BYTES = 1024/MAX_PAYLOAD_BYTES = 4096/' src/validator.py && python -m unittest discover -s tests -v"]`
  - M1 `["cat src/validator.py", "sed -i 's/SCHEMA_VERSION = \"2025-legacy\"/SCHEMA_VERSION = \"2026-08\"/; s/REQUIRED_FIELD = \"id\"/REQUIRED_FIELD = \"request_id\"/; s/ERROR_CODE_MISSING = \"E_ID_MISSING\"/ERROR_CODE_MISSING = \"E_REQUEST_ID_REQUIRED\"/; s/MAX_PAYLOAD_BYTES = 1024/MAX_PAYLOAD_BYTES = 4096/' src/validator.py && python -m unittest discover -s tests -v"]`
- Attempt 2: M0 reads `1`, M1 reads `0`
  - M0 `["cat docs/VALIDATION_CONTRACT.md", "cat src/validator.py", "sed -i 's/SCHEMA_VERSION = \"2025-legacy\"/SCHEMA_VERSION = \"2026-08\"/; s/REQUIRED_FIELD = \"id\"/REQUIRED_FIELD = \"request_id\"/; s/ERROR_CODE_MISSING = \"E_ID_MISSING\"/ERROR_CODE_MISSING = \"E_REQUEST_ID_REQUIRED\"/; s/MAX_PAYLOAD_BYTES = 1024/MAX_PAYLOAD_BYTES = 4096/' src/validator.py && python -m unittest discover -s tests -v"]`
  - M1 `["cat src/validator.py", "cat > src/validator.py << 'EOF'\nSCHEMA_VERSION = \"2026-08\"\nREQUIRED_FIELD = \"request_id\"\nERROR_CODE_MISSING = \"E_REQUEST_ID_REQUIRED\"\nMAX_PAYLOAD_BYTES = 4096\n\n\ndef validate(payload: dict[str, object]) -> str:\n    if REQUIRED_FIELD not in payload:\n        return ERROR_CODE_MISSING\n    return \"ok\"\nEOF\npython -m unittest discover -s tests -v"]`
- Attempt 3: M0 reads `1`, M1 reads `0`
  - M0 `["cat docs/VALIDATION_CONTRACT.md", "cat src/validator.py", "sed -i 's/SCHEMA_VERSION = \"2025-legacy\"/SCHEMA_VERSION = \"2026-08\"/; s/REQUIRED_FIELD = \"id\"/REQUIRED_FIELD = \"request_id\"/; s/ERROR_CODE_MISSING = \"E_ID_MISSING\"/ERROR_CODE_MISSING = \"E_REQUEST_ID_REQUIRED\"/; s/MAX_PAYLOAD_BYTES = 1024/MAX_PAYLOAD_BYTES = 4096/' src/validator.py && python -m unittest discover -s tests -v"]`
  - M1 `["cat src/validator.py", "sed -i 's/SCHEMA_VERSION = \"2025-legacy\"/SCHEMA_VERSION = \"2026-08\"/; s/REQUIRED_FIELD = \"id\"/REQUIRED_FIELD = \"request_id\"/; s/ERROR_CODE_MISSING = \"E_ID_MISSING\"/ERROR_CODE_MISSING = \"E_REQUEST_ID_REQUIRED\"/; s/MAX_PAYLOAD_BYTES = 1024/MAX_PAYLOAD_BYTES = 4096/' src/validator.py && python -m unittest discover -s tests -v"]`
