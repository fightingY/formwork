import io
import unittest
from contextlib import redirect_stdout

from src.demo_cli import greet, main


class CliTests(unittest.TestCase):
    def test_greet_function(self) -> None:
        self.assertEqual(greet("Alice"), "Hello, Alice!")

    def test_greet_command(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            main(["greet", "Alice"])
        self.assertEqual(output.getvalue().strip(), "Hello, Alice!")


if __name__ == "__main__":
    unittest.main()
