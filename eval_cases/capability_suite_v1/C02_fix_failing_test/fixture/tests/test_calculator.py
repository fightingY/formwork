import unittest

from src.calculator import add, multiply


class CalculatorTests(unittest.TestCase):
    def test_add(self) -> None:
        self.assertEqual(add(2, 3), 5)

    def test_multiply(self) -> None:
        self.assertEqual(multiply(4, 3), 12)


if __name__ == "__main__":
    unittest.main()
