from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

HARNESS = r"""
import java.util.Locale;

public final class ShopCacheKeyBuilderVerifier {
    private static void equals(String expected, String actual) {
        if (!expected.equals(actual)) {
            throw new AssertionError("expected=" + expected + ", actual=" + actual);
        }
    }

    private static void rejects(Runnable operation) {
        try {
            operation.run();
        } catch (IllegalArgumentException expected) {
            return;
        }
        throw new AssertionError("expected IllegalArgumentException");
    }

    public static void main(String[] args) {
        Locale original = Locale.getDefault();
        try {
            Locale.setDefault(Locale.forLanguageTag("tr-TR"));
            equals("cache:shop:42", ShopCacheKeyBuilder.shopKey(42));
            equals(
                "cache:shop:search:7:hot-pot:p3",
                ShopCacheKeyBuilder.searchKey("  HOT   Pot  ", 7, 3)
            );
            equals(
                "cache:shop:search:2:izakaya:p1",
                ShopCacheKeyBuilder.searchKey("IZAKAYA", 2, 1)
            );
            rejects(() -> ShopCacheKeyBuilder.searchKey(null, 1, 1));
            rejects(() -> ShopCacheKeyBuilder.searchKey("   ", 1, 1));
            rejects(() -> ShopCacheKeyBuilder.searchKey("food", 0, 1));
            rejects(() -> ShopCacheKeyBuilder.searchKey("food", 1, 0));
        } finally {
            Locale.setDefault(original);
        }
    }
}
"""


def main() -> int:
    root = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix="minicc-r03-") as raw_build:
        build = Path(raw_build)
        verifier = build / "ShopCacheKeyBuilderVerifier.java"
        verifier.write_text(HARNESS, encoding="utf-8")
        try:
            compiled = subprocess.run(
                [
                    "javac",
                    "-encoding",
                    "UTF-8",
                    "-d",
                    str(build),
                    str(root / "ShopCacheKeyBuilder.java"),
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
                ["java", "-cp", str(build), "ShopCacheKeyBuilderVerifier"],
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
