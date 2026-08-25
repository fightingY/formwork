import unittest

from src.parser import parse_items


class ParserTests(unittest.TestCase):
    def test_comma_separated_items(self) -> None:
        self.assertEqual(parse_items("a, b,c"), ["a", "b", "c"])


if __name__ == "__main__":
    unittest.main()
