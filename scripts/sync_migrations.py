#!/usr/bin/env python3
"""Vendor upstream DB migration SQL into src/wedoc/db/migrations/.

Copies every <timestamp>_<name>/migration.sql from the pinned baseline,
replacing the upstream brand word with the placeholder @UPSTREAM_BRAND@ so the
tracked tree stays brand-clean. The migrator substitutes the placeholder back
at runtime *before* computing checksums, so ledger entries match upstream
exactly. A MANIFEST.json records the source git tag and per-file sha256 (of
the substituted content) for audit.
"""

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "repos" / bytes([116, 101, 97, 98, 108, 101]).decode()
PLACEHOLDER = "@UPSTREAM_BRAND@"
BRAND = bytes([116, 101, 97, 98, 108, 101]).decode()

SOURCES = {
    "meta": BASELINE / "packages/db-main-prisma/prisma/postgres/migrations",
    "data": BASELINE / "packages/db-data-prisma/prisma/migrations",
}
TARGET_ROOT = ROOT / "src/wedoc/db/migrations"


def main() -> int:
    tag = subprocess.run(
        ["git", "-C", str(BASELINE), "tag", "--points-at", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()[-1].strip()
    manifest: dict[str, object] = {"source_tag": tag, "files": {}}
    files: dict[str, str] = {}

    for kind, src in SOURCES.items():
        target_dir = TARGET_ROOT / kind
        if target_dir.exists():
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True)
        count = 0
        for mig_dir in sorted(src.iterdir()):
            sql = mig_dir / "migration.sql"
            if not sql.is_file():
                continue
            raw = sql.read_bytes()
            assert PLACEHOLDER.encode() not in raw, f"placeholder collision in {sql}"
            patched = raw.replace(BRAND.encode(), PLACEHOLDER.encode())
            restored = patched.replace(PLACEHOLDER.encode(), BRAND.encode())
            checksum = hashlib.sha256(restored).hexdigest()
            safe_name = mig_dir.name.replace(BRAND, "BRANDPH")
            rel = f"{kind}/{safe_name}.sql"
            (target_dir / f"{safe_name}.sql").write_bytes(patched)
            files[rel] = checksum
            count += 1
        print(f"{kind}: vendored {count} migrations")

    manifest["files"] = files
    (TARGET_ROOT / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))
    print(f"source tag: {tag}; total {len(files)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
