import unittest

from src.validator import validate_code


class ValidatorTests(unittest.TestCase):
    def test_valid_code_after_large_log(self) -> None:
        accepted = validate_code("GOOD-123")
        if not accepted:
            for index in range(3000):
                print(f"noise line {index:04d}: this line exists only to force artifact storage")
                if index == 1733:
                    print(
                        "FIRST_CAUSAL_FAILURE "
                        "contract=VALIDATION_CONTRACT.md rule=accepted-prefix-v2"
                    )
        self.assertTrue(
            accepted,
            "FIRST_CAUSAL_FAILURE "
            "contract=VALIDATION_CONTRACT.md rule=accepted-prefix-v2",
        )

    def test_z_contract_examples(self) -> None:
        self.assertTrue(validate_code("GOOD-0"))
        for value in (
            "OK-123",
            "good-123",
            "GOOD-",
            "GOOD-12A",
            "GOOD-X1",
            "GOOD-1X2",
            "GOOD-１２",
        ):
            with self.subTest(value=value):
                self.assertFalse(validate_code(value))

    def test_z_non_string_is_rejected(self) -> None:
        for value in (None, 123, b"GOOD-1", ["GOOD-1"], True, object()):
            with self.subTest(value=value):
                self.assertFalse(validate_code(value))


if __name__ == "__main__":
    unittest.main()
