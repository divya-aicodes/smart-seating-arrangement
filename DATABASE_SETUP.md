# Database setup

The application uses SQLAlchemy and Alembic migrations through Flask-Migrate.
SQLite is the default local database; PostgreSQL is the production target.

## Local database

Install the declared dependencies and apply all migrations:

```powershell
python -m pip install -r requirements.txt
python -m flask --app app db upgrade
```

The local database is created at `instance/smart_seating.db`. Database files are
ignored by Git. The migration creates the schema only and does not insert sample,
synthetic, or institutional records.

## PostgreSQL

Set the connection URL before running migrations:

```powershell
$env:DATABASE_URL = "postgresql+psycopg://USER:PASSWORD@HOST:5432/DATABASE"
python -m flask --app app db upgrade
```

The application also normalizes provider-style `postgres://` and `postgresql://`
URLs to the Psycopg 3 driver.

## Migration workflow

After an intentional model change:

```powershell
python -m flask --app app db migrate -m "describe the schema change"
python -m flask --app app db upgrade
```

Review generated migration files before applying them. Back up production data before
schema changes and run migrations in staging first.

## Data lineage

Real and synthetic records use the same normalized tables. A dataset records its
`source_type`, version, and optional generation seed. Synthetic data must always be
stored with `source_type = synthetic`; it is never presented as institutional data.

## Institution and academic foundation

Migration `b6f0a9e2c741` adds institutions, departments, specialisations, academic
years, semesters, sections, and dated student academic records. It also adds nullable
institution foreign keys to existing operational entities. The migration does not
invent an institution, infer a student's section, or rewrite existing rows. A later,
reviewed backfill must establish verified ownership before those links can become
mandatory or replace legacy text fields.

## Phase 3 data workflow

1. Open `/data` after applying migrations.
2. For institutional data, upload a UTF-8 CSV using the downloadable header-only
   template. The import creates a versioned real dataset, students, subjects, an exam,
   and eligible exam registrations. No example students are inserted automatically.
3. Create one or more physical rooms for that same dataset. Every seat is stored in the
   database with its row, bench, seat number, active status, and accessibility flag.
4. Alternatively, generate an explicitly labelled synthetic dataset. The supplied seed
   deterministically creates students, subjects, registrations, rooms, seats, and
   invigilators, all connected to the same dataset version.
5. Return to `/`, select the persisted exam and its rooms, and run the optimizer. A
   successful validated result is stored as a seating run with individual allocations.

The server rejects rooms from a different dataset, so real and synthetic records cannot
be mixed in one run. The optional manual CSV workflow remains available for one-time
experiments, but those records and results are not persisted.

## CORS configuration

Same-origin browser requests work without additional configuration. For a separately
hosted frontend, set `CORS_ALLOWED_ORIGINS` to the exact comma-separated origins, for
example `https://seating.example.edu`. Wildcards and suffix matching are not used.
State-changing requests carrying any unlisted `Origin` are rejected with HTTP 403.

Keep `CORS_ALLOW_CREDENTIALS=false` unless cross-origin cookies are deliberately needed.
If enabled, use HTTPS, keep the allowlist narrow, and verify the CSRF flow from the
separately hosted frontend before deployment.

## Public access mode

The login, logout, sign-up, role-gating, and security-account pages have been removed.
The optimizer, data management, reports, and REST resources open directly without a user
account. Every POST form still includes an expiring CSRF token, untrusted cross-origin
writes are rejected, responses are marked `no-store`, and business actions continue to
be written to `audit_logs` under a system or local actor.

This mode is convenient for a local demonstration but it does not provide user-level
access control. A public deployment would expose institutional seating records to every
visitor who can reach it, so use a trusted network or an external authentication gateway.

## Phase 5 operational reports

The current public interface can open `/reports` to review completed or published seating runs.
Each report is generated from persisted exams, students, rooms, seats, registrations, and
allocations. A pending, failed, or invalid run cannot be exported as an institutional
report.

Available downloads are:

- PDF: a print-ready examination summary, allocation register, and validation certificate.
- Excel: a formatted summary with formulas plus a filterable allocation table.

Report downloads are recorded in `audit_logs`. Synthetic reports retain their synthetic
source label and dataset version; they are never presented as institutional records.

## Phase 7 deployment

Production packaging, environment requirements, container operation, migrations,
rollback guidance, and monitoring boundaries are documented in `DEPLOYMENT.md`.
Production mode requires PostgreSQL, a non-placeholder secret of at least 32 characters,
and secure session cookies. The repository includes a non-root Docker image, a
PostgreSQL Compose stack, and GitHub Actions checks; these are deployment tooling, not
evidence that a live environment, backups, TLS, or monitoring have been configured.

The CI PostgreSQL job applies the complete migration chain, runs `flask db check`, and
executes a rollback smoke test. Local unit tests continue to use SQLite for speed; they
must not be treated as proof that a new migration works on PostgreSQL.
