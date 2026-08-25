"""Fail fast on common secrets and local paths in tracked public files."""

from __future__ import annotations

import re
import subprocess
import sys
from argparse import ArgumentParser
from pathlib import Path

SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"(?i)api_key_env\s*:\s*sk-[A-Za-z0-9_-]{16,}"),
)
ABSOLUTE_PATH_PATTERNS = (
    re.compile(r"\b[A-Za-z]:\\(?:[^\r\n`\\]|\\)+"),
    re.compile(r"(?<![A-Za-z0-9_])/(?:Users|home|mnt)/[^\r\n`]+"),
)


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], check=True, capture_output=True
    )
    return [Path(item) for item in result.stdout.decode().split("\0") if item]


def history_secret_findings() -> list[str]:
    commits = subprocess.run(
        ["git", "rev-list", "--all", "--", "minicc.yaml", ".env"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.splitlines()
    findings: list[str] = []
    for commit in commits:
        for filename in ("minicc.yaml", ".env"):
            blob = subprocess.run(
                ["git", "show", f"{commit}:{filename}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if blob.returncode != 0:
                continue
            if any(pattern.search(blob.stdout or "") for pattern in SECRET_PATTERNS):
                findings.append(f"{commit[:12]}:{filename}")
    return findings


def main() -> int:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        "--history",
        action="store_true",
        help="also scan all reachable Git revisions of minicc.yaml and .env",
    )
    args = parser.parse_args()
    findings: list[str] = []
    for local_name in ("minicc.yaml", ".env"):
        local_path = Path(local_name)
        if not local_path.is_file():
            continue
        try:
            local_text = local_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if any(pattern.search(local_text) for pattern in SECRET_PATTERNS):
            findings.append(f"local secret candidate: {local_name}")

    for path in tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for pattern in (*SECRET_PATTERNS, *ABSOLUTE_PATH_PATTERNS):
            match = pattern.search(text)
            if match:
                findings.append(f"{path}: {match.group(0)[:100]}")
                break

    if args.history:
        findings.extend(f"git history: {item}" for item in history_secret_findings())

    if findings:
        print("Public release audit failed:")
        print("\n".join(f"- {finding}" for finding in findings))
        return 1
    print("Public release audit passed for tracked text files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
