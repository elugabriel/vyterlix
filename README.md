# Vyterlix

Business-intelligence and decision-support platform for SMEs (Dolaris Limited).

- **Backend:** Python 3.11 · FastAPI · SQLAlchemy 2 · Alembic · PostgreSQL 17
- **Frontend:** plain HTML/CSS/JavaScript (no framework, no build step, no Node)
- Build progress is tracked in [`docs/CHECKLIST.md`](docs/CHECKLIST.md) — tick items in the same commit as the work.
- Architecture decisions live in [`docs/adr/`](docs/adr/) — read
  [ADR 0001](docs/adr/0001-foundational-architecture.md) before adding tables.

## Project layout

```
vyterlix/
├── backend/
│   ├── app/
│   │   ├── api/v1/        # HTTP routes, all under /api/v1
│   │   ├── core/          # config, logging, error handling
│   │   ├── db/            # SQLAlchemy base + session
│   │   ├── models/        # model registry (import every model here)
│   │   └── main.py        # create_app()
│   ├── alembic/           # database migrations
│   ├── tests/
│   ├── .env.example
│   └── pyproject.toml
├── frontend/
│   ├── index.html
│   ├── css/
│   └── js/
│       ├── config.js      # API base URL — loaded first on every page
│       ├── api.js         # shared API client (use this, not raw fetch)
│       ├── dom.js         # safe DOM helpers (escapeHtml, el)
│       └── pages/         # one script per HTML page
└── docs/adr/              # architecture decision records
```

## Prerequisites

- Python 3.11 (`py -3.11 --version`)
- PostgreSQL 17 running locally on port 5432
- Git

Commands below are for **Git Bash** on Windows, run from the repo root unless stated.

## First-time setup

### 1. Database

Create the app's database user and database (asks for your `postgres` admin password):

```bash
psql -U postgres -c "CREATE USER vyterlix WITH PASSWORD 'vyterlix';"
psql -U postgres -c "CREATE DATABASE vyterlix OWNER vyterlix;"
psql -U postgres -c "CREATE DATABASE vyterlix_test OWNER vyterlix;"
```

`vyterlix` is your dev database. `vyterlix_test` is used only by the test suite, which
migrates it automatically and rolls back every test — tests never touch dev data.

### 2. Backend environment

```bash
cd backend
py -3.11 -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
cp .env.example .env
```

Edit `backend/.env` if your database password or port differs. `.env` is git-ignored —
never commit it.

### 3. Apply migrations

```bash
cd backend
.venv/Scripts/alembic upgrade head
```

## Running locally

Use two terminals.

**Terminal 1 — API** (http://localhost:8000):

```bash
cd backend
.venv/Scripts/uvicorn app.main:app --reload
```

- Interactive API docs: http://localhost:8000/docs
- Liveness: http://localhost:8000/api/v1/health
- Readiness (checks the database): http://localhost:8000/api/v1/health/ready

**Terminal 2 — frontend** (http://localhost:5500):

```bash
python -m http.server 5500 --directory frontend
```

Open http://localhost:5500 — the page should show **API status: online**. If it shows
"unreachable", check the API is running and that `http://localhost:5500` is in
`VYTERLIX_CORS_ORIGINS`.

## Configuration

All backend settings come from `VYTERLIX_*` environment variables or `backend/.env`
(see [`backend/.env.example`](backend/.env.example)).

| Variable | Default | Notes |
|---|---|---|
| `VYTERLIX_ENV` | `dev` | `dev`, `test`, `staging` or `prod`. API docs are disabled in `prod`. |
| `VYTERLIX_LOG_LEVEL` | `INFO` | Logs are JSON, one line per event. |
| `VYTERLIX_DATABASE_URL` | local `vyterlix` DB | SQLAlchemy URL, `postgresql+psycopg://…` |
| `VYTERLIX_CORS_ORIGINS` | `["http://localhost:5500","http://127.0.0.1:5500"]` | JSON list. `*` is rejected in staging/prod. |
| `VYTERLIX_FRONTEND_BASE_URL` | `http://localhost:5500` | Used to build links in emails. |
| `VYTERLIX_EMAIL_BACKEND` | `console` | `console` logs emails instead of sending them. Refused in `prod`. |
| `VYTERLIX_EMAIL_FROM` | `Vyterlix <no-reply@vyterlix.com>` | Sender address. |
| `VYTERLIX_EMAIL_VERIFICATION_TTL_HOURS` | `24` | How long a verification link works. |
| `VYTERLIX_PASSWORD_RESET_TTL_MINUTES` | `60` | How long a password-reset link works. |
| `VYTERLIX_TOKEN_RESEND_COOLDOWN_SECONDS` | `60` | Minimum gap between "send another link" emails (verification and password reset). |
| `VYTERLIX_JWT_SECRET` | dev-only value | Signs access tokens. **Must** be a random 32+ char value in staging/prod (they refuse to start otherwise). |
| `VYTERLIX_ACCESS_TOKEN_TTL_MINUTES` | `15` | Access token lifetime. |
| `VYTERLIX_REFRESH_TOKEN_TTL_DAYS` | `30` | How long a login lasts without activity. |
| `VYTERLIX_COOKIE_SECURE` | `true` | Refresh cookie is HTTPS-only (browsers treat `http://localhost` as secure). |
| `VYTERLIX_LOGIN_MAX_FAILURES_PER_EMAIL` / `_PER_IP` | `5` / `20` | Failed logins allowed per `VYTERLIX_LOGIN_FAILURE_WINDOW_MINUTES` (`15`). |

**Emails in development:** nothing is actually sent. Look in the API terminal for a log
line with `"msg": "email.console"` — the link (e.g. to verify an email) is in its `body`.

The frontend's API address is set in [`frontend/js/config.js`](frontend/js/config.js).
Each environment's deployment replaces that one file.

## Database migrations

```bash
cd backend
.venv/Scripts/alembic revision --autogenerate -m "describe the change"   # after changing models
.venv/Scripts/alembic upgrade head                                       # apply
.venv/Scripts/alembic downgrade -1                                       # roll back one
```

Always read an autogenerated migration before applying it. New model modules must be
imported in `backend/app/models/__init__.py` or autogenerate won't see them.

## Tests and linting

```bash
cd backend
.venv/Scripts/python -m pytest
.venv/Scripts/ruff check .
.venv/Scripts/ruff format .
```

GitHub Actions runs `ruff check`, `ruff format --check` and `pytest` on every push to
`main` and on every pull request.

## Working conventions

- **Branches:** `main` is always working. Each step gets its own branch
  (e.g. `phase2-users`), merged into `main` when its tests pass.
- **Tenant isolation:** every table holding a customer's business data has an
  `organization_id`, and every query on it is scoped by organisation.
- **Money:** `NUMERIC` in the database, `Decimal` in Python — never `float`.
- **Errors:** raise `AppError` subclasses from `app.core.errors`; every error response
  uses the same `{"error": {...}}` shape.
- **Frontend safety:** put API data on the page with `textContent` or `el()` from
  `js/dom.js`, never raw `innerHTML`.
- **AI layer:** the LLM explains results; it never calculates a figure or makes up a
  conclusion. Every score, forecast and recommendation stores the data and rule/model
  version that produced it.
