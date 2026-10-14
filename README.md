# Smart Examination Seating Arrangement

Flask application for generating and validating examination seating plans with
class, subject, accessibility, adjacency, and invigilator constraints. The
application includes a browser workflow, JSON API, CSV export, persisted exam
data, and Flask-Migrate migrations.

## Deploy to Vercel

Vercel runs the app through [`api/index.py`](api/index.py). Add these Project
Environment Variables before the first production deployment:

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

Use the PostgreSQL connection string supplied by your database provider. Vercel
does not provide durable local disk storage, so SQLite and the checked-in local
database are for development only. Run migrations once against the production
database before using persisted exam and room workflows:

```powershell
$env:APP_ENV = "production"
$env:DATABASE_URL = "postgresql://USER:PASSWORD@HOST:5432/DATABASE"
$env:SECRET_KEY = "<your secret>"
$env:ALLOWED_HOSTS = "<your-project>.vercel.app"
python -m flask --app app db upgrade
```

You can also connect the repository in Vercel and deploy with the default build
settings. The included `vercel.json` routes browser and API requests to the
Flask serverless function.

## Local development

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python app.py
```

The local app uses SQLite by default at `instance/smart_seating.db`. Run the
checks with:

```powershell
python -m unittest discover -s tests -v
```

## Production boundary

Deployment to Vercel, PostgreSQL backups, monitoring, and external access
controls are not created by this repository configuration. Configure and verify
those services in the selected accounts before describing the app as live.

The Docker and Compose files remain available for platforms that support a
long-running Flask/Gunicorn service.
