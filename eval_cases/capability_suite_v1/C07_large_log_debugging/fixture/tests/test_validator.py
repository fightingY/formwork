import unittest

from src.validator import validate_code


class ValidatorTests(unittest.TestCase):
    def test_valid_code_after_large_log(self) -> None:
        for index in range(3000):
            print(f"noise line {index:04d}: this line exists only to force artifact storage")
        self.assertTrue(validate_code("GOOD-123"))


if __name__ == "__main__":
    unittest.main()
