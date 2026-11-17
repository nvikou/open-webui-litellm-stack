"""AetherGate Grand Admin — FastAPI backend."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
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
MONITOR_BACKOFF_MAX = float(
    os.environ.get("ADMIN_MONITOR_BACKOFF_MAX", "900")
)
WEBHOOK_RETRIES = int(os.environ.get("ADMIN_WEBHOOK_RETRIES", "3"))
WEBHOOK_BACKOFF_BASE = float(
    os.environ.get("ADMIN_WEBHOOK_BACKOFF", "0.5")
)
WEBHOOK_BODY_LIMIT = 500
WEBHOOK_TIMEOUT = 5.0
AUTH_MAX_FAILURES = int(os.environ.get("ADMIN_AUTH_MAX_FAILURES", "5"))
AUTH_LOCK_BASE_SECONDS = float(
    os.environ.get("ADMIN_AUTH_LOCK_BASE", "2")
)
AUTH_LOCK_MAX_SECONDS = float(
    os.environ.get("ADMIN_AUTH_LOCK_MAX", "900")
)

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
_lock = threading.Lock()
_audit_lock = threading.Lock()
_auth_gate_lock = threading.Lock()
_metrics_lock = threading.Lock()
# ip -> {"count": int, "locked_until": monotonic seconds}
_auth_failures: dict[str, dict[str, float | int]] = {}
_webhook_failures = 0
_webhook_successes = 0


class RestoreBody(BaseModel):
    file: str
    confirm: str = ""
    dry_run: bool = True


_PG_DUMP_MARKERS = (
    b"PostgreSQL database cluster dump",
    b"PostgreSQL database dump",
    b"-- PostgreSQL",
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


def _client_ip(request: Request) -> str:
    """Peer address only — do not trust X-Forwarded-For here."""
    if request.client is None or not request.client.host:
        return "unknown"
    return request.client.host


def _auth_lockout_remaining(ip: str) -> float:
    now = time.monotonic()
    with _auth_gate_lock:
        entry = _auth_failures.get(ip)
        if entry is None:
            return 0.0
        return max(0.0, float(entry["locked_until"]) - now)


def _auth_register_failure(ip: str) -> tuple[int, float]:
    """Record a failed attempt; return (failure_count, lockout_seconds)."""
    now = time.monotonic()
    with _auth_gate_lock:
        entry = _auth_failures.setdefault(
            ip, {"count": 0, "locked_until": 0.0}
        )
        count = int(entry["count"]) + 1
        entry["count"] = count
        if count < AUTH_MAX_FAILURES:
            entry["locked_until"] = 0.0
            return count, 0.0
        exp = count - AUTH_MAX_FAILURES
        delay = min(
            AUTH_LOCK_MAX_SECONDS,
            AUTH_LOCK_BASE_SECONDS * (2**exp),
        )
        entry["locked_until"] = now + delay
        return count, delay


def _auth_clear_failures(ip: str) -> None:
    with _auth_gate_lock:
        _auth_failures.pop(ip, None)


def _require_auth(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(security),
) -> str:
    if not ADMIN_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ADMIN_PASSWORD not configured",
        )

    ip = _client_ip(request)
    remaining = _auth_lockout_remaining(ip)
    if remaining > 0:
        retry_after = max(1, int(remaining))
        _audit(
            actor=ip,
            action="auth_lockout",
            ok=False,
            detail={
                "ip": ip,
                "retry_after_seconds": retry_after,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed auth attempts",
            headers={"Retry-After": str(retry_after)},
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
        failures, lockout = _auth_register_failure(ip)
        _audit(
            actor=credentials.username,
            action="auth_failure",
            ok=False,
            detail={
                "ip": ip,
                "failures": failures,
                "lockout_seconds": lockout,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

    _auth_clear_failures(ip)
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
    """Read alerts.json; treat missing/corrupt as empty."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not ALERTS_FILE.exists():
        return []
    try:
        return json.loads(ALERTS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.exception("failed to read alerts file")
        return []


def _save_alerts(alerts: list[dict]) -> None:
    """Atomically replace alerts.json (temp file + os.replace).

    Callers must hold ``_lock``. Alerts stay on the local filesystem
    rather than Postgres: this process monitors the database itself,
    so a DB outage must not erase the "database down" trail.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        alerts[-100:],
        ensure_ascii=False,
        indent=2,
    )
    fd, tmp_name = tempfile.mkstemp(
        prefix="alerts.",
        suffix=".tmp",
        dir=str(DATA_DIR),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, ALERTS_FILE)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        logger.exception("atomic alerts write failed")
        raise


def _truncate_body(raw: bytes, limit: int = WEBHOOK_BODY_LIMIT) -> str:
    """Decode and cap a response body for safe log lines."""
    text = raw[:limit].decode("utf-8", errors="replace")
    if len(raw) > limit:
        return text + "…"
    return text


def _webhook_metrics() -> dict[str, int]:
    with _metrics_lock:
        return {
            "webhook_successes": _webhook_successes,
            "webhook_failures": _webhook_failures,
        }


def _post_webhook(payload: dict) -> bool:
    """POST alert payload with retries; never raise to callers.

    Logs status + truncated body on failure, applies exponential
    backoff between attempts, and increments in-process counters.
    """
    global _webhook_failures, _webhook_successes

    if not ALERT_WEBHOOK:
        return False

    data = json.dumps(payload).encode("utf-8")
    attempts = max(1, WEBHOOK_RETRIES)
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(
            ALERT_WEBHOOK,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                req, timeout=WEBHOOK_TIMEOUT
            ) as resp:
                resp.read(WEBHOOK_BODY_LIMIT + 1)
            with _metrics_lock:
                _webhook_successes += 1
            return True
        except urllib.error.HTTPError as exc:
            body = b""
            try:
                body = exc.read(WEBHOOK_BODY_LIMIT + 1)
            except OSError:
                body = b""
            logger.warning(
                "webhook HTTP failure attempt=%s/%s "
                "status=%s body=%r",
                attempt,
                attempts,
                exc.code,
                _truncate_body(body),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "webhook failure attempt=%s/%s error=%s",
                attempt,
                attempts,
                exc,
            )
        if attempt < attempts:
            delay = WEBHOOK_BACKOFF_BASE * (2 ** (attempt - 1))
            time.sleep(delay)

    with _metrics_lock:
        _webhook_failures += 1
        failures = _webhook_failures
    logger.error(
        "webhook exhausted retries failures_total=%s",
        failures,
    )
    return False


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


def _record_alert(alert: dict) -> None:
    """Append one alert under the shared alerts lock."""
    with _lock:
        alerts = _load_alerts()
        alerts.append(alert)
        _save_alerts(alerts)


def _emit_transition(
    *,
    sid: str,
    label: str,
    ok: bool,
    detail: str | None,
) -> None:
    """Persist and fan-out a down/up transition alert."""
    alert = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "service": sid,
        "label": label,
        "detail": detail if not ok else "recovered",
        "level": "up" if ok else "down",
    }
    _record_alert(alert)
    _post_webhook({"product": "AetherGate", **alert})


def _backoff_delay(base: float, streak: int) -> float:
    """Exponential delay for a consecutive failure streak."""
    if streak <= 0:
        return base
    return min(MONITOR_BACKOFF_MAX, base * (2 ** (streak - 1)))


def _probe_database() -> dict:
    """Shape a database probe like HTTP service checks."""
    db = _db_overview()
    return {
        "id": "database",
        "label": "PostgreSQL",
        "ok": bool(db.get("ok")),
        "detail": (
            "reachable" if db.get("ok") else db.get("error", "down")
        ),
    }


async def _monitor_loop(stop: asyncio.Event) -> None:
    """Async poll loop with per-service exponential backoff.

    Blocking probes run in a worker thread so the event loop
    stays responsive to shutdown.
    """
    prev: dict[str, bool] = {}
    streaks: dict[str, int] = {}
    next_due: dict[str, float] = {}
    base = max(15.0, float(POLL_SECONDS))

    while not stop.is_set():
        now = time.monotonic()
        targets: list[tuple[str, str, str | None]] = [
            (key, label, url) for key, label, url in CHECKS
        ]
        targets.append(("database", "PostgreSQL", None))

        for sid, label, url in targets:
            if next_due.get(sid, 0.0) > now:
                continue
            if url is None:
                result = await asyncio.to_thread(_probe_database)
            else:
                probed = await asyncio.to_thread(_probe, url)
                result = {
                    "id": sid,
                    "label": label,
                    "ok": bool(probed.get("ok")),
                    "detail": probed.get("detail"),
                }

            ok = bool(result["ok"])
            was = prev.get(sid)
            if was is True and ok is False:
                await asyncio.to_thread(
                    _emit_transition,
                    sid=sid,
                    label=label,
                    ok=False,
                    detail=result.get("detail"),
                )
            elif was is False and ok is True:
                await asyncio.to_thread(
                    _emit_transition,
                    sid=sid,
                    label=label,
                    ok=True,
                    detail=None,
                )

            if ok:
                streaks[sid] = 0
                next_due[sid] = now + base
            else:
                streaks[sid] = streaks.get(sid, 0) + 1
                next_due[sid] = now + _backoff_delay(
                    base, streaks[sid]
                )
            prev[sid] = ok

        sleep_for = base
        if next_due:
            soonest = min(next_due.values()) - time.monotonic()
            sleep_for = max(0.5, soonest)
        try:
            await asyncio.wait_for(stop.wait(), timeout=sleep_for)
            break
        except asyncio.TimeoutError:
            continue


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Start the monitor task on boot; await it on shutdown."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stop = asyncio.Event()
    task = asyncio.create_task(
        _monitor_loop(stop),
        name="aethergate-monitor",
    )
    logger.info("monitor task started")
    try:
        yield
    finally:
        stop.set()
        try:
            await asyncio.wait_for(
                task,
                timeout=max(5.0, float(POLL_SECONDS) + 1.0),
            )
            logger.info("monitor task stopped")
        except asyncio.TimeoutError:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            logger.warning("monitor task cancelled after timeout")


app = FastAPI(
    title="AetherGate Grand Admin",
    version="1.1.0",
    docs_url="/api/docs",
    redoc_url=None,
    lifespan=_lifespan,
)


@app.get("/api/health")
def api_health() -> dict:
    return {
        "ok": True,
        "service": "aethergate-admin",
        "metrics": _webhook_metrics(),
    }


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
