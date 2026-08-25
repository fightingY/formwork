from pathlib import Path
import unittest

from src import validator


def _contract() -> dict[str, str]:
    lines = Path("docs/VALIDATION_CONTRACT.md").read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines if "=" in line)


class ValidationContractTests(unittest.TestCase):
    def test_constants_and_missing_field_behavior_match_contract(self) -> None:
        contract = _contract()

        self.assertEqual(validator.SCHEMA_VERSION, contract["schema_version"])
        self.assertEqual(validator.REQUIRED_FIELD, contract["required_field"])
        self.assertEqual(validator.ERROR_CODE_MISSING, contract["error_code_missing"])
        self.assertEqual(validator.MAX_PAYLOAD_BYTES, int(contract["max_payload_bytes"]))
        self.assertEqual(validator.validate({}), contract["error_code_missing"])
        self.assertEqual(validator.validate({contract["required_field"]: "req-7"}), "ok")


if __name__ == "__main__":
    unittest.main()
