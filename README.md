# AetherGate

Docker stack: Open WebUI + LiteLLM + admin console — chat UI, OpenAI-compatible API, and ops panel for local & cloud LLMs.

## Layout

```
litellm/     → LiteLLM config
admin/       → Grand Admin (FastAPI backend + frontend)
infra/       → postgres, caddy, authentik, portal, ollama
docs/        → go-live guide, branding
scripts/     → secrets, e2e, SSO check, backup
```

| Service | URL |
|---------|-----|
| Portal | http://localhost |
| Grand Admin | http://localhost:9100 (Basic Auth) |
| Open WebUI | http://localhost:3000 |
| LiteLLM | http://localhost:4000/ui |
| Authentik | http://localhost:9000 |
| Postgres | localhost:5432 |

## Design rule

Open WebUI and LiteLLM stay **100% native** — no forks, no panel patching. Shared PostgreSQL. Authentik SSO. Grand Admin handles AetherGate ops only.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python scripts/generate_secrets.py
docker compose up -d --build
python scripts/e2e_test.py
python scripts/setup_sso.py
```

Production TLS: see `docs/GO_LIVE.md` and `docker-compose.prod.yml`.

## Docs

- [Go-Live guide](docs/GO_LIVE.md)
- [Branding](docs/BRANDING.md)
