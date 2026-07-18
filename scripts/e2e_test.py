"""End-to-end checks for AetherGate (front / back / DB / models)."""

from __future__ import annotations

import json
import subprocess
import sys
import time
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


def _get(url: str, headers: dict[str, str] | None = None, timeout: int = 20):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        return resp.status, body


def _post_json(url: str, payload: dict, headers: dict[str, str], timeout: int = 120):
    data = json.dumps(payload).encode("utf-8")
    hdrs = {"Content-Type": "application/json", **headers}
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, None, body


def _wait(url: str, headers: dict[str, str] | None = None, tries: int = 40) -> None:
    last = ""
    for _ in range(tries):
        try:
            status, _ = _get(url, headers=headers)
            if status < 500:
                return
            last = f"HTTP {status}"
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
        time.sleep(3)
    raise RuntimeError(f"Timeout waiting for {url}: {last}")


def _db_isolation_ok() -> bool:
    cmd = [
        "docker",
        "compose",
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "aethergate",
        "-d",
        "postgres",
        "-tAc",
        "SELECT datname FROM pg_database "
        "WHERE datname IN ('ai_platform_db','litellm','authentik') "
        "ORDER BY 1;",
    ]
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        print(proc.stderr)
        return False
    names = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    if names != {"ai_platform_db", "authentik", "litellm"}:
        return False
    schema_cmd = [
        "docker",
        "compose",
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "aethergate",
        "-d",
        "ai_platform_db",
        "-tAc",
        "SELECT 1 FROM pg_namespace WHERE nspname = 'webui';",
    ]
    schema = subprocess.run(
        schema_cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return schema.returncode == 0 and "1" in schema.stdout


def main() -> int:
    env = _load_env()
    master = env.get("LITELLM_MASTER_KEY", "")
    if not master:
        print("FAIL: LITELLM_MASTER_KEY missing in .env")
        return 1

    auth = {"Authorization": f"Bearer {master}"}
    print("== AetherGate e2e ==")
    errors: list[str] = []
    warnings: list[str] = []

    try:
        print("[..] Postgres isolation (webui + litellm + authentik)")
        if not _db_isolation_ok():
            errors.append("DB isolation incomplete")
        else:
            print("[OK] data isolation")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"db isolation: {exc}")

    try:
        print("[..] Backend LiteLLM health")
        _wait("http://localhost:4000/health/liveliness")
        print("[OK] backend health")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"backend health: {exc}")

    try:
        print("[..] Backend /models")
        status, body = _get("http://localhost:4000/models", headers=auth)
        models = json.loads(body.decode("utf-8"))
        ids = [m.get("id") for m in models.get("data", [])]
        print(f"[OK] models ({len(ids)}): {', '.join(ids[:8])}")
        if not ids:
            errors.append("no models registered")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"models: {exc}")

    if env.get("OPENAI_API_KEY"):
        print("[..] Backend chat OpenAI via LiteLLM")
        code, data, err_body = _post_json(
            "http://localhost:4000/v1/chat/completions",
            {
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "Reply with OK"}],
                "max_tokens": 16,
            },
            headers=auth,
        )
        if code == 200 and data:
            content = data["choices"][0]["message"]["content"]
            print(f"[OK] openai chat: {content!r}")
        elif err_body and "not supported" in err_body.lower():
            warnings.append(
                "OpenAI geo-blocked from this region "
                "(provider policy, not AetherGate)"
            )
            print("[WARN] OpenAI region not supported — stack OK")
        else:
            errors.append(f"openai chat HTTP {code}: {err_body}")

    if env.get("HF_TOKEN"):
        print("[..] HF model registered")
        try:
            status, body = _get("http://localhost:4000/models", headers=auth)
            models = json.loads(body.decode("utf-8"))
            ids = [m.get("id") for m in models.get("data", [])]
            if any("hf" in (i or "") or "qwen" in (i or "").lower() for i in ids):
                print("[OK] Hugging Face model registered")
            else:
                errors.append("HF model not listed")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"hf model: {exc}")

    try:
        print("[..] Frontend Open WebUI :3000")
        _wait("http://localhost:3000/")
        print("[OK] frontend")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"frontend: {exc}")

    try:
        print("[..] Portal Caddy :80")
        _wait("http://localhost/")
        print("[OK] portal")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"portal: {exc}")

    try:
        print("[..] SSO Authentik :9000")
        _wait("http://localhost:9000/-/health/live/")
        print("[OK] authentik")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"authentik: {exc}")

    print("=" * 40)
    for w in warnings:
        print(f"WARN: {w}")
    if errors:
        print("RESULT: FAIL")
        for err in errors:
            print(f" - {err}")
        return 1
    print("RESULT: Tout est OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
