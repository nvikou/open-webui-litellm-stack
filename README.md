# AetherGate

**Infrastructure IA complète, prête à vendre** — Docker Compose.

## Structure

```
litellm/     → config LiteLLM
admin/       → Grand Admin (FastAPI backend + frontend)
infra/       → postgres, caddy, authentik, portal, ollama
docs/        → GO_LIVE, branding
scripts/     → secrets, e2e, SSO check, backup
```

| Accès | URL |
|-------|-----|
| Portail | http://localhost |
| Grand Admin | http://localhost:9100 (Basic Auth) |
| Open WebUI | http://localhost:3000 |
| LiteLLM | http://localhost:4000/ui |
| Authentik | http://localhost:9000 |
| Postgres | localhost:5432 |

## Règle d'or

Open WebUI et LiteLLM à **100 % natifs**, zéro bridage. PostgreSQL centralisé. SSO Authentik. Grand Admin = ops AetherGate uniquement.

## Démarrage

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python scripts/generate_secrets.py
docker compose up -d --build
python scripts/e2e_test.py
python scripts/setup_sso.py
```

Prod TLS : voir `docs/GO_LIVE.md` et `docker-compose.prod.yml`.

## Docs

- [Go-Live client](docs/GO_LIVE.md)
- [Branding](docs/BRANDING.md)
- [Commercial / revente](COMMERCIAL.md)
