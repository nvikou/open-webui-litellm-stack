"""Restore AetherGate PostgreSQL from a SQL dump."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore AetherGate DB")
    parser.add_argument("dump", type=Path, help="Path to .sql dump")
    args = parser.parse_args()
    dump = args.dump
    if not dump.is_file():
        print(f"File not found: {dump}", file=sys.stderr)
        return 1

    cmd = [
        "docker",
        "compose",
        "exec",
        "-T",
        "postgres",
        "sh",
        "-c",
        "psql -U \"$POSTGRES_USER\" -d postgres",
    ]
    print(f"Restoring {dump} ...")
    with dump.open("rb") as fh:
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            stdin=fh,
            stderr=subprocess.PIPE,
            check=False,
        )
    if proc.returncode != 0:
        print(proc.stderr.decode("utf-8", errors="replace"), file=sys.stderr)
        return proc.returncode
    print("Restore OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
