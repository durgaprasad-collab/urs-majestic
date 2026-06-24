#!/usr/bin/env python3
"""
URS Majestic — Automated project setup.
Idempotent: safe to run multiple times.

Usage:
    python setup.py
"""

import os
import sys
import subprocess
import pathlib
from urllib.parse import urlparse

ROOT = pathlib.Path(__file__).parent.resolve()
BACKEND = ROOT / "backend"
VENV = ROOT / ".venv"
DOTENV = BACKEND / ".env"

if sys.platform == "win32":
    PYTHON = VENV / "Scripts" / "python.exe"
    PIP = VENV / "Scripts" / "pip.exe"
    ALEMBIC = VENV / "Scripts" / "alembic.exe"
else:
    PYTHON = VENV / "bin" / "python"
    PIP = VENV / "bin" / "pip"
    ALEMBIC = VENV / "bin" / "alembic"


def ok(msg):
    print(f"  [OK] {msg}")


def info(msg):
    print(f"  [..] {msg}")


def fail(msg):
    print(f"  [!!] {msg}")


def banner(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def run(cmd, **kwargs):
    """Run a command, streaming output. Raises on failure."""
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(str(c) for c in cmd)}")
    return result


# ─── Step 1: Virtual environment ────────────────────────────────────────────

def step_create_venv():
    banner("Step 1: Virtual environment")
    if VENV.exists() and PYTHON.exists():
        ok(f"venv already exists at {VENV}")
        return
    info(f"Creating venv at {VENV} ...")
    run([sys.executable, "-m", "venv", str(VENV)])
    ok(f"Created venv at {VENV}")


# ─── Step 2: Install requirements ───────────────────────────────────────────

def step_install_requirements():
    banner("Step 2: Install requirements")
    req = BACKEND / "requirements.txt"
    if not req.exists():
        raise FileNotFoundError(f"requirements.txt not found at {req}")
    info("Installing packages (this may take a minute on first run) ...")
    run([str(PIP), "install", "-q", "-r", str(req)])
    ok("All packages installed.")


# ─── Helper: parse .env file ────────────────────────────────────────────────

def load_dotenv() -> dict:
    if not DOTENV.exists():
        fail(f".env not found at {DOTENV}")
        print()
        print("  ACTION REQUIRED:")
        print(f"    copy {BACKEND / '.env.example'}  ->  {DOTENV}")
        print("    then set DATABASE_URL and SECRET_KEY")
        sys.exit(1)
    env: dict[str, str] = {}
    with open(DOTENV) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


# ─── Step 3: Create database ────────────────────────────────────────────────

def step_create_database():
    banner("Step 3: Create PostgreSQL database")
    env = load_dotenv()
    db_url = env.get("DATABASE_URL", "")
    if not db_url:
        raise ValueError("DATABASE_URL is missing from .env")

    parsed = urlparse(db_url)
    dbname = parsed.path.lstrip("/")
    user = parsed.username or "postgres"
    password = parsed.password or ""
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432

    info(f"Checking database '{dbname}' on {host}:{port} ...")

    create_script = f"""
import sys
import psycopg2

try:
    conn = psycopg2.connect(
        dbname="postgres",
        user={repr(user)},
        password={repr(password)},
        host={repr(host)},
        port={repr(port)},
        connect_timeout=10,
    )
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", ({repr(dbname)},))
    if cur.fetchone():
        print("EXISTS")
    else:
        cur.execute("CREATE DATABASE {dbname}")
        print("CREATED")
    cur.close()
    conn.close()
except Exception as e:
    print(f"ERROR: {{e}}", file=sys.stderr)
    sys.exit(1)
"""
    result = subprocess.run(
        [str(PYTHON), "-c", create_script],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())

    outcome = result.stdout.strip()
    if outcome == "EXISTS":
        ok(f"Database '{dbname}' already exists.")
    elif outcome == "CREATED":
        ok(f"Database '{dbname}' created.")
    else:
        raise RuntimeError(f"Unexpected output: {outcome!r}\n{result.stderr}")


# ─── Step 4: Alembic migrations ─────────────────────────────────────────────

def step_run_migrations():
    banner("Step 4: Alembic migrations")
    env_vars = load_dotenv()
    env_copy = os.environ.copy()
    env_copy.update(env_vars)

    info("Running: alembic upgrade head ...")
    run(
        [str(ALEMBIC), "upgrade", "head"],
        cwd=str(BACKEND),
        env=env_copy,
    )
    ok("Migrations applied.")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 60)
    print("  URS Majestic — Project Setup")
    print("=" * 60)

    steps = [
        ("Create virtual environment", step_create_venv),
        ("Install requirements", step_install_requirements),
        ("Create PostgreSQL database", step_create_database),
        ("Run Alembic migrations", step_run_migrations),
    ]

    for name, fn in steps:
        try:
            fn()
        except SystemExit:
            raise
        except Exception as exc:
            fail(f"{name} FAILED: {exc}")
            sys.exit(1)

    print("\n" + "=" * 60)
    print("  Setup complete!")
    print("=" * 60)
    print()
    print("Next steps:")
    print()
    print("  Windows:")
    print("    .venv\\Scripts\\activate")
    print("    cd backend")
    print("    uvicorn app.main:app --reload")
    print()
    print("  Health check:  http://localhost:8000/health")
    print("  API docs:      http://localhost:8000/docs")
    print()


if __name__ == "__main__":
    main()
