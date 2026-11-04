# Phase 7: production deployment

The application is packaged as one Flask/Gunicorn web service backed by PostgreSQL.
The same container serves the web interface and `/api/v1`; no static student, exam,
room, or allocation records are introduced by deployment.

## Production requirements

- PostgreSQL with persistent, encrypted storage and automated backups
- HTTPS terminated by a trusted load balancer or reverse proxy
- A unique `SECRET_KEY` of at least 32 characters stored in the platform secret manager
- Exact public hostnames in `ALLOWED_HOSTS`
- Database migrations executed once before new application replicas receive traffic
- A trusted network or external access-control gateway if records are not public

Production mode fails closed when the secret is missing or placeholder-valued, the
database is not PostgreSQL, or secure session cookies are disabled.

## Container image

```powershell
docker build -t smart-seating:phase-7 .
```

The image runs as a non-root user, emits Gunicorn access/error logs to standard output,
and exposes port `8000`. Its health check calls `/api/v1/health`, which verifies database
reachability.

`RUN_MIGRATIONS=true` makes the entrypoint run `flask db upgrade` before Gunicorn. Use
that setting for a single-instance Compose deployment. On a multi-replica platform, run
the same migration command as a one-off release job and leave `RUN_MIGRATIONS=false` on
all web replicas.

## Docker Compose

1. Copy `.env.production.example` to `.env.production`.
2. Replace both `[REDACTED_SECRET]` placeholders. The PostgreSQL password must be
   URL-safe because it is included in `DATABASE_URL`.
3. Set `ALLOWED_HOSTS` to the public DNS name.
   Set `HEALTHCHECK_HOST` to one hostname from that allowlist.
4. Start the stack:

```powershell
docker compose --env-file .env.production up --build -d
docker compose --env-file .env.production ps
```

Compose starts PostgreSQL first, waits for it to become healthy, applies migrations, and
then starts Gunicorn. PostgreSQL data is stored in the named `postgres-data` volume.
The web container is read-only apart from an in-memory `/tmp` filesystem. Port 8000 is
bound to loopback by default so a host-level reverse proxy can reach it without exposing
Gunicorn directly on every network interface.

## Reverse proxy and cookies

The production cookie is `Secure`, `HttpOnly`, and `SameSite=Lax`. Do not expose the
container directly to the internet. Route HTTPS traffic from the reverse proxy to port
8000 and set `TRUST_PROXY_HEADERS=true` only when clients cannot bypass that proxy.

For a separate browser frontend, use exact HTTPS origins in `CORS_ALLOWED_ORIGINS`. If
cross-site session cookies are genuinely required, all three settings are required:

```text
CORS_ALLOW_CREDENTIALS=true
SESSION_COOKIE_SAMESITE=None
SESSION_COOKIE_SECURE=true
```

CSRF tokens remain mandatory for POST requests, and wildcard CORS origins are rejected.

## Release and rollback

Before each release:

1. Back up PostgreSQL and verify that the backup can be restored.
2. Run the CI test and container-build jobs.
3. Apply `python -m flask --app app db upgrade` as one release job.
4. Deploy the immutable image and verify `/api/v1/health`, the optimizer page, one
   read-only API call, and a report download.

Rollback the application image independently when no migration was applied. When a
release includes a migration, use a tested database restore or an explicitly reviewed
Alembic downgrade; never delete or recreate the production database to roll back.

## Monitoring and backups

Application logs are emitted as one JSON object per line and include a bounded
`request_id` when a Flask request is active. Collect Gunicorn and application logs
centrally and alert on repeated 5xx responses, failed health
checks, database saturation, and rejected cross-origin writes. Back up PostgreSQL
on a schedule appropriate to institutional recovery requirements. The project does not
claim that backups, TLS, monitoring, or cloud deployment exist until they are configured
and verified on the selected hosting platform.
