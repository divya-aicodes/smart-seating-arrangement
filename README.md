# SeatPlan — Examination Operations

SeatPlan is a production-oriented Flask application for preparing, validating,
and auditing examination seating plans. It combines a light, responsive web
interface with database-backed institutional or reproducible synthetic data,
constraint-aware seat allocation, persisted reports, and export workflows.

**Live application:** [smart-seating-arrangement-tan.vercel.app](https://smart-seating-arrangement-tan.vercel.app/)

**Source:** [github.com/divya-aicodes/smart-seating-arrangement](https://github.com/divya-aicodes/smart-seating-arrangement)

## What the application does

- Imports real student and examination records or generates seeded synthetic data.
- Stores datasets, students, subjects, exams, rooms, invigilators, seating runs,
  allocations, imports, and audit events in a relational database.
- Produces room-by-room seating plans while checking capacity, class and subject
  adjacency, accessibility needs, forbidden positions, and invigilator coverage.
- Explains validation results using graph coloring, the handshaking lemma,
  pigeonhole reasoning, inclusion-exclusion, and chromatic-polynomial analysis.
- Exports persisted seating runs as CSV, XLSX, and PDF reports.
- Provides a versioned JSON API under `/api/v1` with an OpenAPI document at
  `/api/v1/openapi.json`.

## Interface

The frontend is server-rendered with Jinja and rebuilt around a professional,
light-only operations interface. It uses restrained motion, responsive layouts,
semantic controls, keyboard-visible focus states, and reduced-motion support.
There is no login or sign-up screen; public planning and reporting workflows open
directly from the main navigation.

## Architecture

| Layer | Implementation |
| --- | --- |
| Web application | Flask 3, Jinja templates, semantic HTML, CSS, vanilla JavaScript |
| Data access | Flask-SQLAlchemy and Flask-Migrate |
| Local database | SQLite at `instance/smart_seating.db` |
| Production database | PostgreSQL through `DATABASE_URL` |
| Optimization and mathematics | NetworkX, SymPy, application constraint modules |
| Reports | OpenPyXL and ReportLab |
| Deployment | Vercel Python runtime through `api/index.py` |

The frontend does not depend on hard-coded sample arrays. Its selectable exams,
rooms, students, seating runs, report summaries, and allocation details are read
from persisted records. Seeded synthetic datasets use the same schema and routes
as imported institutional data.

## Security controls

- State-changing browser and API requests require CSRF validation.
- CORS is deny-by-default for cross-origin requests and accepts only exact origins
  listed in `CORS_ALLOWED_ORIGINS`.
- Credentialed CORS remains disabled unless explicitly enabled.
- Production startup requires an explicit database URL, secret key, and allowed
  host list.
- Responses apply defensive headers including `X-Frame-Options: DENY`,
  MIME-sniffing protection, referrer and permissions restrictions, and HSTS in
  production.
- Uploads and request payloads are size-limited and validated before persistence.

## Local development

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python app.py
```

Open `http://127.0.0.1:5000/`.

Run the complete test suite:

```powershell
python -m unittest discover -s tests -v
```

## Production deployment on Vercel

Vercel detects the root Flask application and imports it through
[`api/index.py`](api/index.py). Configure these project environment variables:

```text
APP_ENV=production
SECRET_KEY=<at least 32 random characters>
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DATABASE
ALLOWED_HOSTS=<your-project>.vercel.app
SESSION_COOKIE_SECURE=true
SESSION_COOKIE_SAMESITE=Lax
CORS_ALLOWED_ORIGINS=
CORS_ALLOW_CREDENTIALS=false
TRUST_PROXY_HEADERS=false
```

Keep `CORS_ALLOWED_ORIGINS` empty when the browser and API use the same origin.
If a separate frontend is introduced, list only its exact HTTPS origin. Do not
use wildcard origins for this application.

Vercel storage is ephemeral, so production must use PostgreSQL rather than the
local SQLite file. Apply migrations to the production database before relying on
the persisted data workflows:

```powershell
$env:APP_ENV = "production"
$env:DATABASE_URL = "postgresql://USER:PASSWORD@HOST:5432/DATABASE"
$env:SECRET_KEY = "<your secret>"
$env:ALLOWED_HOSTS = "<your-project>.vercel.app"
python -m flask --app app db upgrade
```

Operational deployment guidance is also available in
[`DEPLOYMENT.md`](DEPLOYMENT.md), with database-specific setup in
[`DATABASE_SETUP.md`](DATABASE_SETUP.md) and API details in [`API.md`](API.md).

## Repository layout

```text
api/                 Vercel entrypoint
seating/             constraint and mathematical allocation modules
templates/           server-rendered application screens
static/              light-theme styles, interactions, and favicon
migrations/          database migrations
tests/               API, browser route, persistence, security, and solver tests
app.py                application configuration and planning workflow
models.py             relational domain model
```

## Important production boundary

The repository supplies application and deployment configuration, but a durable
production database, backups, monitoring, and account access policies must be
configured in the target environment. Do not describe persisted production data
as durable until the deployed application is connected to PostgreSQL and its
migrations have been applied.
