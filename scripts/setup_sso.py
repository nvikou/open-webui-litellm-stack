"""Verify Authentik OIDC discovery for Open WebUI + LiteLLM."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_env() -> dict[str, str]:
    data: dict[str, str] = {}
    path = ROOT / ".env"
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    env = _load_env()
    base = env.get("AUTHENTIK_PUBLIC_URL", "http://localhost:9000").rstrip("/")
    apps = [
        ("Open WebUI", "aethergate-openwebui"),
        ("LiteLLM", "aethergate-litellm"),
    ]
    print("AetherGate SSO verification")
    print("-" * 40)
    errors = 0
    for label, slug in apps:
        url = (
            f"{base}/application/o/{slug}/.well-known/openid-configuration"
        )
        try:
            doc = _get_json(url)
            issuer = doc.get("issuer", "")
            auth_ep = doc.get("authorization_endpoint", "")
            print(f"[OK] {label}: {issuer}")
            print(f"     authorize: {auth_ep}")
        except urllib.error.HTTPError as exc:
            errors += 1
            print(f"[FAIL] {label}: HTTP {exc.code} ({url})")
            print(
                "  → Connect to Authentik admin, ensure blueprint apps exist, "
                "or recreate provider/application pair."
            )
        except Exception as exc:  # noqa: BLE001
            errors += 1
            print(f"[FAIL] {label}: {exc}")

    live = f"{base}/-/health/live/"
    try:
        with urllib.request.urlopen(live, timeout=10) as resp:
            print(f"[OK] Authentik live: HTTP {resp.status}")
    except Exception as exc:  # noqa: BLE001
        errors += 1
        print(f"[FAIL] Authentik live: {exc}")

    print("-" * 40)
    if errors:
        print("SSO not fully ready — see docs/GO_LIVE.md § SSO")
        return 1
    print(
        "SSO discovery OK. Manual login test:\n"
        "  1) Open WebUI → Continue with Authentik\n"
        "  2) LiteLLM /ui → SSO (GENERIC_* env)\n"
        f"  Authentik admin: {base}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
