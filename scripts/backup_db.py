"""Backup AetherGate PostgreSQL (Grand Admin)."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKUP_DIR = ROOT / "backups"


def main() -> int:
    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = BACKUP_DIR / f"aethergate_{stamp}.sql"
    cmd = [
        "docker",
        "compose",
        "exec",
        "-T",
        "postgres",
        "sh",
        "-c",
        "pg_dumpall -U \"$POSTGRES_USER\"",
    ]
    print(f"Backing up to {out} ...")
    with out.open("wb") as fh:
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            stdout=fh,
            stderr=subprocess.PIPE,
            check=False,
        )
    if proc.returncode != 0:
        print(proc.stderr.decode("utf-8", errors="replace"), file=sys.stderr)
        return proc.returncode
    print("Backup OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
