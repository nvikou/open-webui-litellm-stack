"""AetherGate Grand Admin — FastAPI backend."""

from __future__ import annotations

import json
import logging
import os
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "/backups"))
DATA_DIR = Path(os.environ.get("ADMIN_DATA_DIR", "/data"))
ALERTS_FILE = DATA_DIR / "alerts.json"
AUDIT_FILE = DATA_DIR / "audit.log"
logger = logging.getLogger("aethergate.admin")
FRONTEND_DIR = Path(
    os.environ.get(
        "FRONTEND_DIR",
        str(Path(__file__).resolve().parents[1] / "frontend"),
    )
)
ADMIN_USER = os.environ.get("ADMIN_USERNAME", "grandadmin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ALERT_WEBHOOK = os.environ.get("ADMIN_ALERT_WEBHOOK", "").strip()
POLL_SECONDS = int(os.environ.get("ADMIN_MONITOR_SECONDS", "60"))

CHECKS = [
    (
        "openwebui",
        "Open WebUI",
        os.environ.get("OPENWEBUI_HEALTH", "http://open-webui:8080/health"),
    ),
    (
        "litellm",
        "LiteLLM",
        os.environ.get(
            "LITELLM_HEALTH",
            "http://litellm:4000/health/liveliness",
        ),
    ),
    (
        "sso",
        "Authentik",
        os.environ.get(
            "SSO_HEALTH",
            "http://authentik-server:9000/-/health/live/",
        ),
    ),
]

security = HTTPBasic(auto_error=False)
_monitor_started = False
_lock = threading.Lock()
_audit_lock = threading.Lock()


class RestoreBody(BaseModel):
    file: str
    confirm: str = ""
    dry_run: bool = True


_PG_DUMP_MARKERS = (
    b"PostgreSQL database cluster dump",
    b"PostgreSQL database dump",
    b"-- PostgreSQL",
)

app = FastAPI(
    title="AetherGate Grand Admin",
    version="1.1.0",
    docs_url="/api/docs",
    redoc_url=None,
)


def _audit(
    *,
    actor: str,
    action: str,
    ok: bool,
    detail: dict[str, Any] | None = None,
) -> None:
    """Append one NDJSON event to the durable audit log.

    Stored on the local filesystem (not Postgres) so a cluster
    restore cannot erase evidence of the restore itself. Append-
    only + fsync keeps a usable trail under concurrent writers.
    """
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "actor": actor,
        "action": action,
        "ok": ok,
        "detail": detail or {},
    }
    line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with _audit_lock:
            with AUDIT_FILE.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())
    except OSError:
        logger.exception(
            "audit write failed action=%s actor=%s",
            action,
            actor,
        )


def _probe(url: str, timeout: float = 4.0) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return {
                "ok": True,
                "status": resp.status,
                "detail": f"HTTP {resp.status}",
            }
    except urllib.error.HTTPError as exc:
        ok = exc.code < 500
        return {"ok": ok, "status": exc.code, "detail": f"HTTP {exc.code}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": None, "detail": str(exc)}


def _require_auth(
    credentials: HTTPBasicCredentials | None = Depends(security),
) -> str:
    if not ADMIN_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ADMIN_PASSWORD not configured",
        )
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Auth required",
            headers={"WWW-Authenticate": "Basic"},
        )
    user_ok = secrets.compare_digest(credentials.username, ADMIN_USER)
    pass_ok = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def _db_overview() -> dict:
    user = os.environ.get("POSTGRES_USER", "aethergate")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    host = os.environ.get("POSTGRES_HOST", "postgres")
    env = os.environ.copy()
    env["PGPASSWORD"] = password
    proc = subprocess.run(
        [
            "psql",
            "-h",
            host,
            "-U",
            user,
            "-d",
            "postgres",
            "-tAc",
            "SELECT datname FROM pg_database "
            "WHERE datistemplate = false ORDER BY 1;",
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr.strip()}
    databases = [
        line.strip() for line in proc.stdout.splitlines() if line.strip()
    ]
    schemas: list[str] = []
    if "ai_platform_db" in databases:
        schema_proc = subprocess.run(
            [
                "psql",
                "-h",
                host,
                "-U",
                user,
                "-d",
                "ai_platform_db",
                "-tAc",
                "SELECT nspname FROM pg_namespace "
                "WHERE nspname NOT LIKE 'pg_%' "
                "AND nspname <> 'information_schema' "
                "ORDER BY 1;",
            ],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        if schema_proc.returncode == 0:
            schemas = [
                line.strip()
                for line in schema_proc.stdout.splitlines()
                if line.strip()
            ]
    return {
        "ok": True,
        "host": f"{host}:5432",
        "databases": databases,
        "ai_platform_schemas": schemas,
    }


def _run_backup() -> dict:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = BACKUP_DIR / f"aethergate_{stamp}.sql"
    user = os.environ.get("POSTGRES_USER", "aethergate")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    host = os.environ.get("POSTGRES_HOST", "postgres")
    env = os.environ.copy()
    env["PGPASSWORD"] = password
    with out.open("wb") as fh:
        proc = subprocess.run(
            ["pg_dumpall", "-h", host, "-U", user],
            stdout=fh,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
        )
    if proc.returncode != 0:
        if out.exists():
            out.unlink(missing_ok=True)
        return {
            "ok": False,
            "error": proc.stderr.decode("utf-8", errors="replace"),
        }
    return {"ok": True, "file": out.name, "path": str(out)}


def _list_backups() -> list[dict]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for path in sorted(BACKUP_DIR.glob("aethergate_*.sql"), reverse=True):
        items.append(
            {
                "file": path.name,
                "size_bytes": path.stat().st_size,
                "mtime": datetime.fromtimestamp(
                    path.stat().st_mtime, tz=timezone.utc
                ).isoformat(),
            }
        )
    return items


def _confirm_matches(confirm: str, expected: str) -> bool:
    """Timing-safe equality; unequal lengths never match."""
    if len(confirm) != len(expected):
        return False
    return secrets.compare_digest(confirm, expected)


def _resolve_dump(filename: str) -> tuple[Path | None, str | None]:
    """Resolve a backup name under BACKUP_DIR or return an error."""
    safe = Path(filename).name
    if not safe.startswith("aethergate_") or not safe.endswith(".sql"):
        return None, "invalid backup name"
    dump = (BACKUP_DIR / safe).resolve()
    try:
        dump.relative_to(BACKUP_DIR.resolve())
    except ValueError:
        return None, "invalid backup name"
    if not dump.is_file():
        return None, "backup not found"
    return dump, None


def _validate_dump(dump: Path) -> dict:
    """Validate a pg_dumpall SQL file without applying it."""
    size = dump.stat().st_size
    if size == 0:
        return {"ok": False, "error": "empty dump"}
    with dump.open("rb") as fh:
        head = fh.read(8192)
    if b"\x00" in head:
        return {"ok": False, "error": "dump looks binary, not SQL"}
    if not any(marker in head for marker in _PG_DUMP_MARKERS):
        return {
            "ok": False,
            "error": "dump missing PostgreSQL dump header",
        }
    return {
        "ok": True,
        "file": dump.name,
        "size_bytes": size,
    }


def _run_restore(filename: str, *, dry_run: bool = True) -> dict:
    """Validate or apply a dump; fail closed without a safety backup."""
    dump, err = _resolve_dump(filename)
    if err is not None:
        return {"ok": False, "error": err, "dry_run": dry_run}

    validation = _validate_dump(dump)
    if not validation.get("ok"):
        return {**validation, "dry_run": dry_run}
    if dry_run:
        return {**validation, "dry_run": True}

    safety = _run_backup()
    if not safety.get("ok"):
        return {
            "ok": False,
            "dry_run": False,
            "error": (
                "safety backup failed: "
                + str(safety.get("error", "unknown"))
            ),
        }

    user = os.environ.get("POSTGRES_USER", "aethergate")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    host = os.environ.get("POSTGRES_HOST", "postgres")
    env = os.environ.copy()
    env["PGPASSWORD"] = password
    with dump.open("rb") as fh:
        proc = subprocess.run(
            ["psql", "-h", host, "-U", user, "-d", "postgres"],
            stdin=fh,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
        )
    if proc.returncode != 0:
        return {
            "ok": False,
            "dry_run": False,
            "safety_backup": safety["file"],
            "error": proc.stderr.decode("utf-8", errors="replace"),
        }
    return {
        "ok": True,
        "file": dump.name,
        "dry_run": False,
        "safety_backup": safety["file"],
    }


def _load_alerts() -> list[dict]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not ALERTS_FILE.exists():
        return []
    try:
        return json.loads(ALERTS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _save_alerts(alerts: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ALERTS_FILE.write_text(
        json.dumps(alerts[-100:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _post_webhook(payload: dict) -> None:
    if not ALERT_WEBHOOK:
        return
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        ALERT_WEBHOOK,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception:  # noqa: BLE001
        pass


def _collect_status() -> dict:
    services = []
    for key, label, url in CHECKS:
        services.append({"id": key, "label": label, "url": url, **_probe(url)})
    db = _db_overview()
    services.append(
        {
            "id": "database",
            "label": "PostgreSQL",
            "url": "postgres:5432",
            "ok": bool(db.get("ok")),
            "status": 200 if db.get("ok") else None,
            "detail": (
                "reachable" if db.get("ok") else db.get("error", "down")
            ),
        }
    )
    return {
        "product": "AetherGate",
        "role": "Grand Admin",
        "all_ok": all(s["ok"] for s in services),
        "services": services,
        "links": {
            "openwebui": os.environ.get(
                "OPENWEBUI_URL", "http://localhost:3000"
            ),
            "litellm": os.environ.get(
                "LITELLM_URL", "http://localhost:4000/ui"
            ),
            "sso": os.environ.get("SSO_URL", "http://localhost:9000"),
            "portal": os.environ.get("PORTAL_URL", "http://localhost/"),
            "docs": "/api/docs",
        },
        "database": db,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def _monitor_loop() -> None:
    prev: dict[str, bool] = {}
    while True:
        status_payload = _collect_status()
        for svc in status_payload["services"]:
            sid = svc["id"]
            ok = bool(svc["ok"])
            was = prev.get(sid)
            if was is True and ok is False:
                alert = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "service": sid,
                    "label": svc["label"],
                    "detail": svc.get("detail"),
                    "level": "down",
                }
                with _lock:
                    alerts = _load_alerts()
                    alerts.append(alert)
                    _save_alerts(alerts)
                _post_webhook({"product": "AetherGate", **alert})
            elif was is False and ok is True:
                alert = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "service": sid,
                    "label": svc["label"],
                    "detail": "recovered",
                    "level": "up",
                }
                with _lock:
                    alerts = _load_alerts()
                    alerts.append(alert)
                    _save_alerts(alerts)
                _post_webhook({"product": "AetherGate", **alert})
            prev[sid] = ok
        time.sleep(max(15, POLL_SECONDS))


@app.on_event("startup")
def _startup() -> None:
    global _monitor_started
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if not _monitor_started:
        thread = threading.Thread(target=_monitor_loop, daemon=True)
        thread.start()
        _monitor_started = True


@app.get("/api/health")
def api_health() -> dict:
    return {"ok": True, "service": "aethergate-admin"}


@app.get("/api/status")
def api_status(_: str = Depends(_require_auth)) -> dict:
    return _collect_status()


@app.get("/api/db")
def api_db(_: str = Depends(_require_auth)) -> dict:
    return _db_overview()


@app.get("/api/backups")
def api_backups(_: str = Depends(_require_auth)) -> dict:
    return {"ok": True, "items": _list_backups()}


@app.post("/api/backup")
def api_backup(actor: str = Depends(_require_auth)) -> dict:
    result = _run_backup()
    _audit(
        actor=actor,
        action="backup",
        ok=bool(result.get("ok")),
        detail={
            "file": result.get("file"),
            "error": result.get("error"),
        },
    )
    return result


@app.post("/api/restore")
def api_restore(
    body: RestoreBody,
    actor: str = Depends(_require_auth),
) -> dict:
    safe_name = Path(body.file).name
    if not body.dry_run and not _confirm_matches(
        body.confirm, safe_name
    ):
        _audit(
            actor=actor,
            action="restore",
            ok=False,
            detail={
                "file": safe_name,
                "dry_run": False,
                "error": "confirm mismatch",
            },
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="confirm must exactly match file name",
        )

    result = _run_restore(body.file, dry_run=body.dry_run)
    _audit(
        actor=actor,
        action="restore",
        ok=bool(result.get("ok")),
        detail={
            "file": safe_name,
            "dry_run": body.dry_run,
            "safety_backup": result.get("safety_backup"),
            "error": result.get("error"),
        },
    )
    return result


@app.get("/api/alerts")
def api_alerts(_: str = Depends(_require_auth)) -> dict:
    with _lock:
        return {"ok": True, "items": list(reversed(_load_alerts()))}


@app.get("/login")
def login_hint() -> JSONResponse:
    return JSONResponse(
        {
            "message": "Use HTTP Basic Auth (ADMIN_USERNAME / ADMIN_PASSWORD)",
            "username_env": "ADMIN_USERNAME",
        }
    )


@app.get("/")
def index(_: str = Depends(_require_auth)) -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


if (FRONTEND_DIR / "assets").is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=str(FRONTEND_DIR / "assets")),
        name="assets",
    )
