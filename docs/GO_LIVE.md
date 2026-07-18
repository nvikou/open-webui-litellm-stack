# AetherGate — Client Go-Live Guide

## 1. Prerequisites

| Component | Windows | Linux |
|-----------|---------|-------|
| Docker Desktop / Engine | Yes | Yes |
| Docker Compose v2 | Included | `docker compose` |
| Python 3.11+ | Yes (venv) | Yes |
| Recommended RAM | 8 GB+ | 8 GB+ |
| Free ports | 80, 3000, 4000, 5432, 9000, 9100 | same |

## 2. Local install (dev / demo)

```powershell
# Windows
cd "path\aethergate"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
# Set OPENAI_API_KEY and HF_TOKEN in .env
python scripts/generate_secrets.py
docker compose up -d --build
python scripts/e2e_test.py
python scripts/setup_sso.py
```

```bash
# Linux
cd /opt/aethergate
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python scripts/generate_secrets.py
docker compose up -d --build
python scripts/e2e_test.py
python scripts/setup_sso.py
```

## 3. Go-live checklist

- [ ] `.env` filled (API keys + generated secrets)
- [ ] `ADMIN_PASSWORD` set (Grand Admin)
- [ ] `docker compose ps` → healthy
- [ ] Portal http://localhost
- [ ] Grand Admin http://localhost:9100 (Basic Auth)
- [ ] Open WebUI http://localhost:3000
- [ ] LiteLLM http://localhost:4000/ui
- [ ] Authentik http://localhost:9000
- [ ] `python scripts/setup_sso.py` → OK
- [ ] Backup test from Grand Admin
- [ ] (Prod) `DOMAIN` + `ACME_EMAIL` + `docker-compose.prod.yml`

## 4. First users

### 4.1 Authentik (SSO)

1. Open http://localhost:9000
2. Sign in with `AUTHENTIK_BOOTSTRAP_EMAIL` / `AUTHENTIK_BOOTSTRAP_PASSWORD`
3. Create end users (Directory → Users)
4. Confirm apps **AetherGate Open WebUI** and **AetherGate LiteLLM**

### 4.2 Open WebUI

1. http://localhost:3000
2. First local admin account **or** “Continue with Authentik”
3. Confirm LiteLLM models appear (Settings → Connections)

### 4.3 Create a LiteLLM API key

1. http://localhost:4000/ui
2. UI login (`LITELLM_UI_USERNAME` / `LITELLM_UI_PASSWORD`) or SSO
3. **Virtual Keys** → Create Key
4. Copy the key (`sk-...`)

### 4.4 Point Open WebUI at LiteLLM

Already configured by default in Compose:

- `OPENAI_API_BASE_URL=http://litellm:4000/v1`
- `OPENAI_API_KEY=<LITELLM_MASTER_KEY>`

For a dedicated team key: Open WebUI Admin → Connections → replace the key with the LiteLLM virtual key.

## 5. Models

| Model name (LiteLLM) | Provider | Requirement |
|----------------------|----------|-------------|
| `gpt-4o-mini` / `gpt-4o` | OpenAI | `OPENAI_API_KEY` + allowed region |
| `claude-sonnet` | Anthropic | `ANTHROPIC_API_KEY` |
| `hf-qwen2.5-7b` | Hugging Face | `HF_TOKEN` |
| `ollama/llama3.2` | Local Ollama | auto-pull (`ollama-init`) |

## 6. Production (HTTPS)

```bash
# .env
DOMAIN=example.com
ACME_EMAIL=ops@example.com
OPENWEBUI_BASE_URL=https://chat.example.com
LITELLM_BASE_URL=https://llm.example.com
AUTHENTIK_PUBLIC_URL=https://auth.example.com

docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

DNS: `example.com`, `chat`, `llm`, `auth`, `admin` → server IP.

## 7. Grand Admin

- URL: http://localhost:9100
- User: `ADMIN_USERNAME` (default `grandadmin`)
- Pass: `ADMIN_PASSWORD`
- Backup / Restore / Alerts / Status
- Optional webhook: `ADMIN_ALERT_WEBHOOK`

## 8. Ops support

```powershell
python scripts/status.py
python scripts/backup_db.py
python scripts/setup_sso.py
docker compose logs -f litellm open-webui admin
```
