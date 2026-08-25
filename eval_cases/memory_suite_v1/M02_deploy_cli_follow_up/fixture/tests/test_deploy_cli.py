from pathlib import Path
import unittest

from src import deploy_cli


def _contract() -> dict[str, str]:
    lines = Path("docs/DEPLOYMENT_CONTRACT.md").read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines if "=" in line)


class DeploymentContractTests(unittest.TestCase):
    def test_defaults_match_contract(self) -> None:
        contract = _contract()

        self.assertEqual(
            deploy_cli.defaults(),
            (
                contract["command_name"],
                contract["default_region"],
                contract["config_env"],
                int(contract["max_parallel"]),
            ),
        )


if __name__ == "__main__":
    unittest.main()
