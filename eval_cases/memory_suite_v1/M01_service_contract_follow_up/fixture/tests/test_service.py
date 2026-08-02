from pathlib import Path
import unittest

from src import service


def _contract() -> dict[str, str]:
    lines = Path("docs/SERVICE_CONTRACT.md").read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines if "=" in line)


class ServiceContractTests(unittest.TestCase):
    def test_service_constants_match_authoritative_contract(self) -> None:
        contract = _contract()

        self.assertEqual(service.SERVICE_NAME, contract["service_name"])
        self.assertEqual(service.HEALTH_PATH, contract["health_path"])
        self.assertEqual(service.READY_BODY, contract["ready_body"])
        self.assertEqual(service.RETRY_BUDGET, int(contract["retry_budget"]))

    def test_readiness_response_uses_contract_constants(self) -> None:
        contract = _contract()

        self.assertEqual(
            service.readiness_response(),
            (
                contract["health_path"],
                contract["ready_body"],
                int(contract["retry_budget"]),
            ),
        )


if __name__ == "__main__":
    unittest.main()
