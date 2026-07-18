"""One-shot install for AetherGate (client delivery)."""

from __future__ import annotations

import subprocess
import sys
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"


def _run(cmd: list[str], **kwargs) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True, **kwargs)


def main() -> int:
    print("=== AetherGate install ===")
    print("Frontend: Open WebUI | Backend: LiteLLM | SSO: Authentik")

    if not (ROOT / ".env").exists():
        print("ERROR: .env missing (need OPENAI_API_KEY + HF_TOKEN at least)")
        return 1

    if not VENV.exists():
        print("Creating venv ...")
        venv.EnvBuilder(with_pip=True).create(VENV)

    if sys.platform == "win32":
        py = str(VENV / "Scripts" / "python.exe")
        pip = str(VENV / "Scripts" / "pip.exe")
    else:
        py = str(VENV / "bin" / "python")
        pip = str(VENV / "bin" / "pip")

    _run([pip, "install", "-r", "requirements-dev.txt"])
    _run([py, "scripts/generate_secrets.py"])
    _run(["docker", "compose", "pull"])
    _run(["docker", "compose", "up", "-d"])
    print("Waiting for services, then run:")
    print(f"  {py} scripts/e2e_test.py")
    print(f"  {py} scripts/status.py")
    print("=== Install started ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
