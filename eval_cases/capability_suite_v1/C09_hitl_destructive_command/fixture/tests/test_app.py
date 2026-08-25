import unittest

from src.app import status


class AppTests(unittest.TestCase):
    def test_status(self) -> None:
        self.assertEqual(status(), "ok")


if __name__ == "__main__":
    unittest.main()
