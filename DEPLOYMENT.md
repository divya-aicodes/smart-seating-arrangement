# Production deployment

The application is packaged as a Flask/Gunicorn web service and an optional Celery
worker backed by PostgreSQL and Redis. PostgreSQL is the source of truth; Redis only
transports task messages. No static student, exam, room, or allocation records are
introduced by deployment.

## Production requirements

- PostgreSQL with persistent, encrypted storage and automated backups
- HTTPS terminated by a trusted load balancer or reverse proxy
- A unique `SECRET_KEY` of at least 32 characters stored in the platform secret manager
- A one-time random `INITIAL_SETUP_TOKEN` until the first administrator is created
- Exact public hostnames in `ALLOWED_HOSTS`
- Database migrations executed once before new application replicas receive traffic
- Redis and at least one Celery worker when `BACKGROUND_JOBS_ENABLED=true`
- Shared private storage or S3-compatible storage visible to both web and worker
- Shared Redis rate-limit storage when multiple web replicas are used

Production mode fails closed when the secret is missing or placeholder-valued, the
database is not PostgreSQL, or secure session cookies are disabled.

After the migration, visit `/setup` once, provide `INITIAL_SETUP_TOKEN`, create the
institution administrator, and then remove or rotate the setup token. The route returns
404 after any user exists. Do not create a default password in an image or migration.

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

Compose starts PostgreSQL and Redis, waits for them to become healthy, applies
migrations, starts Gunicorn, and then starts the Celery worker. PostgreSQL data,
Redis AOF data, and private job artifacts use separate named volumes.
The web container is read-only apart from an in-memory `/tmp` filesystem. Port 8000 is
bound to loopback by default so a host-level reverse proxy can reach it without exposing
Gunicorn directly on every network interface.

For multi-host deployments, replace the Compose local job volume with
`JOB_STORAGE_BACKEND=s3` and configure `S3_BUCKET`, `S3_PREFIX`, optional
`S3_ENDPOINT_URL`, and `S3_REGION`. Credentials use the standard AWS SDK environment
or workload identity chain and must stay in the platform secret manager.

Vercel does not run the persistent worker process defined here. Keep background jobs
disabled on Vercel unless an external Redis service, Celery worker deployment, and
shared/S3 artifact storage have all been configured. Synchronous behavior remains
available when background jobs are disabled.

After an unclean worker shutdown, run `flask --app app recover-stale-jobs` to mark
jobs that have remained `running` for more than one hour as failed. Review their
database side effects, then use the retry action; never delete job history from Redis.

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
centrally and alert on repeated 5xx responses, failed health checks, jobs stuck in
`running`, worker absence, Redis unavailability, database saturation, and rejected
cross-origin writes. Back up PostgreSQL
on a schedule appropriate to institutional recovery requirements. The project does not
claim that backups, TLS, monitoring, or cloud deployment exist until they are configured
and verified on the selected hosting platform.
