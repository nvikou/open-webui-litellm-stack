# Security — AetherGate Grand Admin

This document describes the attack surface of the Grand Admin
service (`admin/backend`) and the controls that bound it.

## Trust model

- Grand Admin is an **internal ops plane**, not a public product UI.
- Callers authenticate with HTTP Basic
  (`ADMIN_USERNAME` / `ADMIN_PASSWORD`).
- `ADMIN_PASSWORD` is mandatory: if unset, authenticated routes
  fail closed with HTTP 503.
- Peer IP for lockout is taken from the TCP peer only —
  `X-Forwarded-For` is **not** trusted (spoofable).

## Destructive operations

| Action | Controls |
| --- | --- |
| `POST /api/backup` | Basic Auth + append-only audit event |
| `POST /api/restore` | Basic Auth + `dry_run` default + filename `confirm` token + automatic safety backup before apply + audit |

Restore confirmation must exactly match the backup basename.
A failed safety backup aborts the restore (fail closed).

## Authentication abuse

Failed Basic Auth attempts are counted **per peer IP**.
After `ADMIN_AUTH_MAX_FAILURES` (default 5), the IP is locked with
exponential backoff up to `ADMIN_AUTH_LOCK_MAX`.
Each failure and lockout is written to the audit log.

## Outbound health probes (SSRF surface)

The monitor and `/api/status` call `_probe()` against URLs from:

- `OPENWEBUI_HEALTH`
- `LITELLM_HEALTH`
- `SSO_HEALTH`

Controls:

1. **Scheme allowlist** — only `http` and `https`.
2. **Host allowlist** — default Docker DNS names
   (`open-webui`, `litellm`, `authentik-server`) plus loopback;
   extend with comma-separated `ADMIN_PROBE_ALLOW_HOSTS`.
3. **No embedded credentials** in the URL.
4. **Strict timeout** — `ADMIN_PROBE_TIMEOUT` (default 4s).
5. **No redirect following** — a 3xx response is treated as failure
   so a compromised target cannot bounce the probe to an
   off-allowlist host (e.g. cloud metadata).

Database reachability uses `psql` to `POSTGRES_HOST`, not HTTP.

## Alert webhook (residual outbound)

`ADMIN_ALERT_WEBHOOK` is an operator-configured egress URL.
It is **not** host-allowlisted like probes: treat it as a trusted
ops setting, keep it on an internal receiver, and monitor
`/api/health` → `metrics.webhook_failures`.

## Durable evidence

| Artifact | Location | Notes |
| --- | --- | --- |
| Audit log | `$ADMIN_DATA_DIR/audit.log` | NDJSON, append-only, fsynced |
| Alerts | `$ADMIN_DATA_DIR/alerts.json` | Atomic replace under lock |

Both live on the local data volume so a Postgres restore cannot
erase the trail of the restore (or of a database outage).

## Operator checklist

- [ ] Set a strong unique `ADMIN_PASSWORD`
- [ ] Restrict network exposure of port 9100 to ops networks
- [ ] Keep probe hosts inside `ADMIN_PROBE_ALLOW_HOSTS` when
      overriding `*_HEALTH` URLs
- [ ] Point `ADMIN_ALERT_WEBHOOK` only at a trusted receiver
- [ ] Retain / ship `audit.log` off-box if compliance requires it
