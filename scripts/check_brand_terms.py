#!/usr/bin/env python3
"""Scan all git-tracked files for upstream reference product names.

The banned literal is assembled at runtime so this script itself stays clean.
Exit code 1 if any tracked file contains it (case-insensitive).
"""

import subprocess
import sys

BANNED = [bytes(seq).decode() for seq in ([116, 101, 97, 98, 108, 101],)]


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line for line in out.splitlines() if line.strip()]


def main() -> int:
    failures: list[str] = []
    for path in tracked_files():
        lowered_path = path.lower()
        for word in BANNED:
            if word in lowered_path:
                failures.append(f"{path}: filename")
                break
        try:
            with open(path, encoding="utf-8", errors="strict") as f:
                text = f.read()
        except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
            continue
        lowered = text.lower()
        for word in BANNED:
            if word in lowered:
                lines = [
                    i + 1
                    for i, line in enumerate(lowered.splitlines())
                    if word in line
                ]
                failures.append(f"{path}: lines {lines}")
    if failures:
        print("Banned brand term found in tracked files:")
        print("\n".join(failures))
        return 1
    print("OK: all non-ignored files clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
