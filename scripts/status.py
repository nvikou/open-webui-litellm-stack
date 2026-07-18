"""Status check for AetherGate frontend / backend / DB / SSO."""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_env() -> dict[str, str]:
    env_path = ROOT / ".env"
    data: dict[str, str] = {}
    if not env_path.exists():
        return data
    for line in env_path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def _http_ok(url: str, headers: dict[str, str] | None = None, timeout: int = 8) -> tuple[bool, str]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        if exc.code < 500:
            return True, f"HTTP {exc.code}"
        return False, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def main() -> int:
    env = _load_env()
    checks = [
        ("Portal (produit)", "http://localhost/"),
        ("Frontend Open WebUI", "http://localhost:3000/"),
        ("Backend LiteLLM", "http://localhost:4000/health/liveliness"),
        ("SSO Authentik", "http://localhost:9000/-/health/live/"),
    ]
    print("AetherGate status")
    print("-" * 40)
    failed = 0
    for name, url in checks:
        ok, detail = _http_ok(url)
        mark = "OK" if ok else "FAIL"
        if not ok:
            failed += 1
        print(f"[{mark}] {name}: {detail}")

    master = env.get("LITELLM_MASTER_KEY", "")
    if master:
        ok, detail = _http_ok(
            "http://localhost:4000/models",
            headers={"Authorization": f"Bearer {master}"},
        )
        mark = "OK" if ok else "FAIL"
        if not ok:
            failed += 1
        print(f"[{mark}] Backend models: {detail}")

    print("-" * 40)
    if failed:
        print(f"{failed} check(s) failed")
        return 1
    print("All services reachable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
