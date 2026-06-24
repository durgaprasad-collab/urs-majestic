# URS Majestic — Backend

Restaurant ordering and analytics platform. FastAPI + PostgreSQL.

## Quick Start

```bash
# 1. Copy and fill in credentials
copy backend\.env.example backend\.env
# edit backend\.env — set DATABASE_URL password and SECRET_KEY

# 2. Run automated setup (creates venv, installs deps, creates DB, runs migrations)
python setup.py

# 3. Start the server
.venv\Scripts\activate
cd backend
uvicorn app.main:app --reload
```

- Health: http://localhost:8000/health
- Docs:   http://localhost:8000/docs

## Structure

```
backend/app/
  main.py            FastAPI entry point
  core/              config, database engine, JWT/password helpers
  models/            SQLAlchemy ORM models
  schemas/           Pydantic v2 request/response schemas
  api/routes/        REST endpoints (menu, orders, customers)
  services/          6 agent modules (stub, extend as needed)
  workers/           Background task stubs
```

## Environment Variables

| Variable | Description |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `SECRET_KEY` | JWT signing secret (min 32 chars) |
| `ALGORITHM` | JWT algorithm (default: HS256) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Token TTL (default: 30) |

Works identically on Render/Supabase/Neon — change only `DATABASE_URL`.
