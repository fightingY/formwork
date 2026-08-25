import os
import unittest

from src.config import load_database_url, load_log_level


class ConfigTests(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("APP_DATABASE_URL", None)
        os.environ.pop("APP_LOG_LEVEL", None)

    def test_defaults(self) -> None:
        self.assertEqual(load_database_url(), "sqlite:///app.db")
        self.assertEqual(load_log_level(), "INFO")

    def test_env_values(self) -> None:
        os.environ["APP_DATABASE_URL"] = " postgres://db "
        os.environ["APP_LOG_LEVEL"] = " debug "
        self.assertEqual(load_database_url(), "postgres://db")
        self.assertEqual(load_log_level(), "DEBUG")


if __name__ == "__main__":
    unittest.main()
