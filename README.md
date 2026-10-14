# Tynato Monitoring

Independent monitoring and alerting foundation for Tynato CRM. The API and worker run separately from CRM. The worker probes CRM health, sends email through Resend, and mirrors monitoring records to the dedicated monitoring Supabase Postgres database when available.

## Keep these directories in GitHub

- `backend/`: monitoring API, worker, durable local SQLite state, security helpers, and tests.
- `database/migrations/`: version-controlled schema migration for the dedicated monitoring Supabase project.
- `deployment/`: Docker Compose file and safe per-service environment templates.
- `frontend/`: independent Next.js monitoring dashboard.
- `docs/`: integration examples and operational notes.

Commit the application source, documentation, migration, Compose file, Dockerfile, and `.env.example` templates. Never commit real `.env`, `api.env`, `worker.env`, `.env.local`, credentials, database passwords, or API keys.

## Environment-file locations (important)

**Do not create `backend/.env` for Docker deployment.** The Compose file is `deployment/compose.yaml` and reads these files relative to that folder:

- `deployment/api.env` — only API auth, CORS and signed-event ingestion configuration.
- `deployment/worker.env` — monitoring Postgres connection, CRM probes, and email delivery configuration.
- `frontend/.env.local` — local frontend development only. Vercel gets these values through Vercel Project Settings.

Create the server files from their safe templates:

```bash
cp deployment/api.env.example deployment/api.env
cp deployment/worker.env.example deployment/worker.env
chmod 600 deployment/api.env deployment/worker.env
```

On the Oracle VM, run these commands from `/opt/tynato-monitoring`. Fill the files with real values using a secure editor. Never paste secrets into Git, the frontend, or this README.

## Required settings

### `deployment/api.env`

- `SUPABASE_URL`: the URL of the new monitoring Supabase project; used to verify Auth JWTs through its JWKS endpoint.
- `EVENT_INGEST_HMAC_SECRET`: a random secret of at least 32 characters, shared only with the CRM backend when signed event integration is enabled.
- `MONITORING_CORS_ORIGINS`: exact frontend origins, comma-separated.
- Keep `JWT_AUDIENCE=authenticated` unless the Auth configuration explicitly requires another value.

### `deployment/worker.env`

- `MONITORING_DATABASE_URL`: Postgres connection string from the **monitoring project's** Supabase Connect dialog; prefer Session Pooler for an IPv4-only Oracle VM. Add `sslmode=require` and URL-encode reserved characters in the password.
- `CRM_API_HEALTH_URL`: only set an endpoint confirmed to exist and reachable from Oracle. Do not assume `/health` exists.
- `CRM_DATABASE_URL`: optional; if enabled, use a dedicated least-privilege database account for a `SELECT 1` connectivity check.
- `RESEND_API_KEY`: a Resend sending-only key where possible.
- `ALERT_FROM_EMAIL`: sender at a verified sending domain.
- `ALERT_EMAIL_RECIPIENTS`: comma-separated Super Admin and technical operator addresses.

Never place a Supabase secret/service-role API key in either runtime env file unless a specific reviewed operation needs it. The Postgres connection string is not a Supabase API key. Do not expose secrets through `NEXT_PUBLIC_` variables.

## Supabase setup

1. Create a **dedicated monitoring Supabase project**. Do not use the CRM project.
2. Open that project's SQL Editor.
3. Run `database/migrations/001_monitoring.sql` once.
4. In Settings / API Keys, copy the Project URL and publishable key for the frontend. Do not expose secret keys to the browser.
5. Use Connect to obtain the monitoring project's Postgres connection string for the worker. Replace the password placeholder and encode reserved characters. Keep SSL enabled.

The monitoring tables have RLS enabled and intentionally have no browser-user policies. The trusted backend mirrors records over the server-side Postgres connection. Review and restrict the database role privileges for production; do not use the CRM database connection for monitoring storage.

## Resend email setup

1. Create or sign in to a Resend account.
2. Add a sending domain you control and publish the DNS records Resend provides.
3. Wait until the domain is verified.
4. Create a sending-only API key restricted to that domain if available.
5. Put it only in `deployment/worker.env`; set `ALERT_FROM_EMAIL` to an address on the verified domain.
6. Add both critical recipients to `ALERT_EMAIL_RECIPIENTS`.

Do not mark email alerts verified until a test email has actually been received.

## Local backend tests

Requires Python 3.12+:

```bash
cd backend
python -m venv .venv
# Windows CMD: .venv\Scripts\activate.bat
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
pytest -q
```

The test suite does not prove that live Supabase, Resend, Oracle, DNS or Vercel are configured correctly.

## Oracle deployment

1. Clone or copy the repository to `/opt/tynato-monitoring`.
2. Create and fill `deployment/api.env` and `deployment/worker.env` from their examples.
3. Apply the Supabase migration before starting cloud mirroring.
4. From the repository root, validate and build the isolated stack:

```bash
docker compose -p tynato-monitoring -f deployment/compose.yaml config
docker compose -p tynato-monitoring -f deployment/compose.yaml build
docker compose -p tynato-monitoring -f deployment/compose.yaml up -d
docker compose -p tynato-monitoring -f deployment/compose.yaml ps
```

The API is bound only to `127.0.0.1:18080`. The worker has no published port. Both services share only the monitoring data volume and their own explicitly scoped environment files. Configure Nginx/HTTPS only after local health and authentication checks pass.

Inspect:

```bash
docker compose -p tynato-monitoring -f deployment/compose.yaml logs --tail=100 monitor-api monitor-worker
curl -fsS http://127.0.0.1:18080/health/live
```

Rollback only this monitoring stack:

```bash
docker compose -p tynato-monitoring -f deployment/compose.yaml down
```

Do not add `-v`; that would remove the monitoring volume.

## Frontend and Vercel

For local Next.js development, copy `frontend/.env.example` to `frontend/.env.local` and set:

- `NEXT_PUBLIC_SUPABASE_URL`: monitoring project's URL.
- `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`: monitoring project's publishable key.
- `NEXT_PUBLIC_MONITORING_API_URL`: local API URL during development, or the public HTTPS API URL after Nginx/DNS configuration.

For Vercel, import this repository and set the Root Directory to `frontend`. Enter those same three values in Project Settings → Environment Variables. The browser publishable key is expected to be public; RLS and server-side API authorization remain the security controls. Never add the Supabase secret/service-role key, Postgres connection string, Resend key, or HMAC secret to frontend variables.

The API must be configured with the exact Vercel deployment origin in `MONITORING_CORS_ORIGINS`.

## Give monitoring users a trusted role

The API grants dashboard access only if the signed-in user's JWT has `app_metadata.monitoring_role` set to `super_admin` or `technical_operator`. Set this claim using `backend/scripts/set_monitoring_role.py` on a trusted administrator workstation. It prompts for the project's server-only service-role key without echoing it. Never put this key into Docker env files or browser variables. Sign out and back in after changing the role so the JWT refreshes.

## CRM event integration contract

POST events to `POST /v1/events` with `X-Monitor-Timestamp` (Unix seconds) and `X-Monitor-Signature` (`sha256=` plus lowercase hex HMAC-SHA256 of `<timestamp>.<exact raw request body>`). Share `EVENT_INGEST_HMAC_SECRET` only between monitoring API and CRM backend. Timestamps outside the configured skew window are rejected; duplicate `event_id` values are idempotent. CRM event reporting should be asynchronous and must not block normal CRM requests.

Do not send tokens, API keys, passwords, payment details or full customer records in event payloads.

## Acceptance and limitations

This package is a starter implementation, not proof of production acceptance. Test CRM outage and recovery, real email delivery, monitoring database outage and later sync, worker restart, authentication and customer isolation, and CRM regression flows before production reliance. Whole-VM failure requires external uptime monitoring because a process on the failed host cannot report its own failure.
