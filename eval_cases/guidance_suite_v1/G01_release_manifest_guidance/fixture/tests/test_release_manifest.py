import unittest

from src.release_manifest import build_manifest


class ReleaseManifestTests(unittest.TestCase):
    def test_preserves_legacy_identifier_and_sorts_files(self) -> None:
        self.assertEqual(
            build_manifest("v3.2.0", ["z.py", "a.py", "m.py"]),
            {"release_id": "v3.2.0", "files": ["a.py", "m.py", "z.py"]},
        )

    def test_does_not_mutate_the_input_list(self) -> None:
        files = ["b.py", "a.py"]
        build_manifest("v3.2.0", files)
        self.assertEqual(files, ["b.py", "a.py"])


if __name__ == "__main__":
    unittest.main()
