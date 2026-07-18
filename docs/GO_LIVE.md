# AetherGate — Guide client / Go-Live

## 1. Prérequis

| Composant | Windows | Linux |
|-----------|---------|-------|
| Docker Desktop / Engine | Oui | Oui |
| Docker Compose v2 | Inclus | `docker compose` |
| Python 3.11+ | Oui (venv) | Oui |
| RAM conseillée | 8 Go+ | 8 Go+ |
| Ports libres | 80, 3000, 4000, 5432, 9000, 9100 | idem |

## 2. Install locale (dev / démo)

```powershell
# Windows
cd "chemin\aethergate"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
# Mettre OPENAI_API_KEY et HF_TOKEN dans .env
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

## 3. Checklist go-live

- [ ] `.env` rempli (clés API + secrets générés)
- [ ] `ADMIN_PASSWORD` présent (Grand Admin)
- [ ] `docker compose ps` → healthy
- [ ] Portail http://localhost
- [ ] Grand Admin http://localhost:9100 (Basic Auth)
- [ ] Open WebUI http://localhost:3000
- [ ] LiteLLM http://localhost:4000/ui
- [ ] Authentik http://localhost:9000
- [ ] `python scripts/setup_sso.py` → OK
- [ ] Backup test depuis Grand Admin
- [ ] (Prod) `DOMAIN` + `ACME_EMAIL` + `docker-compose.prod.yml`

## 4. Premiers utilisateurs

### 4.1 Authentik (SSO)

1. Ouvre http://localhost:9000  
2. Connecte-toi avec `AUTHENTIK_BOOTSTRAP_EMAIL` / `AUTHENTIK_BOOTSTRAP_PASSWORD`  
3. Crée les utilisateurs finaux (Directory → Users)  
4. Vérifie les applications **AetherGate Open WebUI** et **AetherGate LiteLLM**

### 4.2 Open WebUI

1. http://localhost:3000  
2. Premier compte local admin **ou** « Continue with Authentik »  
3. Vérifie que les modèles LiteLLM apparaissent (Settings → Connections)

### 4.3 Créer une clé LiteLLM (API)

1. http://localhost:4000/ui  
2. Login UI (`LITELLM_UI_USERNAME` / `LITELLM_UI_PASSWORD`) ou SSO  
3. **Virtual Keys** → Create Key  
4. Copie la clé (`sk-...`)

### 4.4 Brancher Open WebUI sur LiteLLM

Déjà fait par défaut dans Compose :

- `OPENAI_API_BASE_URL=http://litellm:4000/v1`
- `OPENAI_API_KEY=<LITELLM_MASTER_KEY>`

Pour une clé dédiée (équipe) : Admin Open WebUI → Connections → remplace la clé par la virtual key LiteLLM.

## 5. Modèles

| Model name (LiteLLM) | Fournisseur | Condition |
|----------------------|-------------|-----------|
| `gpt-4o-mini` / `gpt-4o` | OpenAI | `OPENAI_API_KEY` + région autorisée |
| `claude-sonnet` | Anthropic | `ANTHROPIC_API_KEY` |
| `hf-qwen2.5-7b` | Hugging Face | `HF_TOKEN` |
| `ollama/llama3.2` | Ollama local | pull auto (`ollama-init`) |

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

DNS : `example.com`, `chat`, `llm`, `auth`, `admin` → IP serveur.

## 7. Grand Admin

- URL : http://localhost:9100  
- User : `ADMIN_USERNAME` (défaut `grandadmin`)  
- Pass : `ADMIN_PASSWORD`  
- Backup / Restore / Alertes / Statut  
- Webhook optionnel : `ADMIN_ALERT_WEBHOOK`

## 8. Support ops

```powershell
python scripts/status.py
python scripts/backup_db.py
python scripts/setup_sso.py
docker compose logs -f litellm open-webui admin
```
