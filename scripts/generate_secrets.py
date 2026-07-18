"""Generate AetherGate secrets and merge into .env."""

from __future__ import annotations

import secrets
import string
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
EXAMPLE_PATH = ROOT / ".env.example"

FIXED = {
    "POSTGRES_USER": "aethergate",
    "OPENWEBUI_BASE_URL": "http://localhost:3000",
    "LITELLM_BASE_URL": "http://localhost:4000",
    "AUTHENTIK_PUBLIC_URL": "http://host.docker.internal:9000",
    "OPENWEBUI_OAUTH_CLIENT_ID": "aethergate-openwebui",
    "LITELLM_OAUTH_CLIENT_ID": "aethergate-litellm",
    "LITELLM_UI_USERNAME": "admin",
    "AUTHENTIK_BOOTSTRAP_EMAIL": "admin@example.com",
    "PRODUCT_NAME": "AetherGate",
    "ADMIN_USERNAME": "grandadmin",
    "OLLAMA_PULL_MODEL": "llama3.2",
}


def _token(n: int = 48) -> str:
    return secrets.token_urlsafe(n)


def _password(n: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def _master_key() -> str:
    return "sk-" + secrets.token_urlsafe(32)


def _parse_env(text: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def _dump_env(data: dict[str, str]) -> str:
    order = [
        "PRODUCT_NAME",
        "OPENAI_API_KEY",
        "HF_TOKEN",
        "ANTHROPIC_API_KEY",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "LITELLM_MASTER_KEY",
        "LITELLM_UI_USERNAME",
        "LITELLM_UI_PASSWORD",
        "WEBUI_SECRET_KEY",
        "AUTHENTIK_SECRET_KEY",
        "AUTHENTIK_BOOTSTRAP_EMAIL",
        "AUTHENTIK_BOOTSTRAP_PASSWORD",
        "AUTHENTIK_BOOTSTRAP_TOKEN",
        "OPENWEBUI_BASE_URL",
        "LITELLM_BASE_URL",
        "AUTHENTIK_PUBLIC_URL",
        "OPENWEBUI_OAUTH_CLIENT_ID",
        "OPENWEBUI_OAUTH_CLIENT_SECRET",
        "LITELLM_OAUTH_CLIENT_ID",
        "LITELLM_OAUTH_CLIENT_SECRET",
        "ADMIN_USERNAME",
        "ADMIN_PASSWORD",
        "ADMIN_ALERT_WEBHOOK",
        "OLLAMA_PULL_MODEL",
    ]
    lines = [
        "# AetherGate — secrets (ne jamais committer)",
        "# Frontend = Open WebUI | Backend = LiteLLM | SSO = Authentik",
        "",
    ]
    seen: set[str] = set()
    for key in order:
        if key in data:
            lines.append(f"{key}={data[key]}")
            seen.add(key)
    for key in sorted(data):
        if key not in seen:
            lines.append(f"{key}={data[key]}")
    return "\n".join(lines) + "\n"


def main() -> None:
    current: dict[str, str] = {}
    if ENV_PATH.exists():
        current = _parse_env(ENV_PATH.read_text(encoding="utf-8"))

    generated = {
        **FIXED,
        "POSTGRES_PASSWORD": current.get("POSTGRES_PASSWORD") or _password(28),
        "LITELLM_MASTER_KEY": current.get("LITELLM_MASTER_KEY") or _master_key(),
        "LITELLM_UI_PASSWORD": current.get("LITELLM_UI_PASSWORD") or _password(20),
        "WEBUI_SECRET_KEY": current.get("WEBUI_SECRET_KEY") or _token(32),
        "AUTHENTIK_SECRET_KEY": current.get("AUTHENTIK_SECRET_KEY") or _token(40),
        "AUTHENTIK_BOOTSTRAP_PASSWORD": (
            current.get("AUTHENTIK_BOOTSTRAP_PASSWORD") or _password(20)
        ),
        "AUTHENTIK_BOOTSTRAP_TOKEN": (
            current.get("AUTHENTIK_BOOTSTRAP_TOKEN") or _token(24)
        ),
        "OPENWEBUI_OAUTH_CLIENT_SECRET": (
            current.get("OPENWEBUI_OAUTH_CLIENT_SECRET") or _token(24)
        ),
        "LITELLM_OAUTH_CLIENT_SECRET": (
            current.get("LITELLM_OAUTH_CLIENT_SECRET") or _token(24)
        ),
        "ADMIN_PASSWORD": current.get("ADMIN_PASSWORD") or _password(20),
    }

    # Preserve API keys already present
    for key in ("OPENAI_API_KEY", "HF_TOKEN", "ANTHROPIC_API_KEY"):
        if key in current and current[key]:
            generated[key] = current[key]
        elif key not in generated:
            generated[key] = current.get(key, "")

    merged = {**current, **generated}
    ENV_PATH.write_text(_dump_env(merged), encoding="utf-8")
    print(f"Wrote {ENV_PATH}")
    print(f"SSO admin: {merged['AUTHENTIK_BOOTSTRAP_EMAIL']}")
    print("Passwords generated (see .env). Do not commit .env.")


if __name__ == "__main__":
    main()
