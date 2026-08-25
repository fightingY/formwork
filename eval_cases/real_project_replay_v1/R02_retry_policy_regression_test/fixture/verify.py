from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


def compile_and_run(policy: str, test_source: Path) -> int:
    with tempfile.TemporaryDirectory(prefix="minicc-r02-") as raw_build:
        build = Path(raw_build)
        policy_source = build / "RetryPolicy.java"
        policy_source.write_text(policy, encoding="utf-8")
        try:
            compiled = subprocess.run(
                [
                    "javac",
                    "-encoding",
                    "UTF-8",
                    "-d",
                    str(build),
                    str(policy_source),
                    str(test_source),
                ],
                text=True,
                capture_output=True,
            )
        except FileNotFoundError as exc:
            print(f"Java toolchain is unavailable: {exc}")
            return 2
        if compiled.returncode != 0:
            print(compiled.stdout, end="")
            print(compiled.stderr, end="")
            return compiled.returncode
        try:
            checked = subprocess.run(
                ["java", "-cp", str(build), "RetryPolicyBoundaryTest"],
                text=True,
                capture_output=True,
            )
        except FileNotFoundError as exc:
            print(f"Java runtime is unavailable: {exc}")
            return 2
        return checked.returncode


def main() -> int:
    root = Path(__file__).resolve().parent
    policy = (root / "RetryPolicy.java").read_text(encoding="utf-8")
    test_source = root / "RetryPolicyBoundaryTest.java"
    correct_status = compile_and_run(policy, test_source)
    if correct_status == 2:
        return 2
    if correct_status != 0:
        print("Boundary test does not pass against the correct implementation.")
        return 1

    mutants = {
        "max-boundary": policy.replace(
            "retryCount >= MAX_RETRY_COUNT", "retryCount > MAX_RETRY_COUNT"
        ),
        "exception-contract": policy.replace(
            "new IllegalArgumentException", "new IllegalStateException"
        ),
    }
    for name, mutant in mutants.items():
        if mutant == policy:
            print(f"Verifier could not construct {name} mutant.")
            return 2
        mutant_status = compile_and_run(mutant, test_source)
        if mutant_status == 2:
            return 2
        if mutant_status == 0:
            print(f"Boundary test did not detect {name} mutant.")
            return 1
    print("Boundary regression test passed and detected both mutants.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
