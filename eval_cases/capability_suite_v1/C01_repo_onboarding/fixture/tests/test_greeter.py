import unittest

from src.tiny_greeter import greet


class GreeterTests(unittest.TestCase):
    def test_greet(self) -> None:
        self.assertEqual(greet("Ada"), "Hello, Ada!")


if __name__ == "__main__":
    unittest.main()
