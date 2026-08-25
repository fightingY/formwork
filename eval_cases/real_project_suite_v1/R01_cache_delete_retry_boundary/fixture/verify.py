from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

HARNESS = r"""
import java.time.Duration;
import java.time.LocalDateTime;

public final class CacheDeleteMessageVerifier {
    public static void main(String[] args) {
        long[] expected = {1, 5, 30, 300, 1800};
        CacheDeleteMessage message = new CacheDeleteMessage();
        for (int i = 0; i < expected.length; i++) {
            LocalDateTime before = LocalDateTime.now();
            message.incrementRetry();
            long scheduledMillis = Duration.between(before, message.getNextExecuteTime()).toMillis();
            long expectedMillis = expected[i] * 1000;
            if (scheduledMillis < expectedMillis - 250 || scheduledMillis > expectedMillis + 1000) {
                throw new AssertionError(
                    "retry " + (i + 1) + " scheduled " + scheduledMillis
                    + "ms, expected about " + expectedMillis + "ms"
                );
            }
            if (message.getRetryCount() != i + 1) {
                throw new AssertionError("retry count was not incremented exactly once");
            }
        }
        if (!message.isExhausted() || message.getNextDelaySeconds() != -1) {
            throw new AssertionError("message must be exhausted after the fifth retry");
        }
    }
}
"""


def main() -> int:
    root = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix="minicc-r01-") as raw_build:
        build = Path(raw_build)
        verifier = build / "CacheDeleteMessageVerifier.java"
        verifier.write_text(HARNESS, encoding="utf-8")
        try:
            compiled = subprocess.run(
                [
                    "javac",
                    "-encoding",
                    "UTF-8",
                    "-d",
                    str(build),
                    str(root / "CacheDeleteMessage.java"),
                    str(verifier),
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
                ["java", "-cp", str(build), "CacheDeleteMessageVerifier"],
                text=True,
                capture_output=True,
            )
        except FileNotFoundError as exc:
            print(f"Java runtime is unavailable: {exc}")
            return 2
        print(checked.stdout, end="")
        print(checked.stderr, end="")
        return checked.returncode


if __name__ == "__main__":
    raise SystemExit(main())
