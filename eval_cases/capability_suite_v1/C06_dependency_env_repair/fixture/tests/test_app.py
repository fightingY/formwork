import unittest

from src.app import normalize_name


class AppTests(unittest.TestCase):
    def test_normalize_name(self) -> None:
        self.assertEqual(normalize_name(" ada "), "Ada")


if __name__ == "__main__":
    unittest.main()
