"""Seed initial users into the users table.

Run ONCE after migration 0004 is applied:
    python scripts/seed_admin.py

Prints credentials to stdout — save them immediately.
If a username already exists it is skipped (idempotent).
"""
import secrets
import string
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.user import User  # noqa: F401  ensures table is known

USERS = [
    {"name": "Sayee",  "username": "sayee",  "is_admin": True},
    {"name": "Durga",  "username": "durga",  "is_admin": False},
    {"name": "Usha",   "username": "usha",   "is_admin": False},
]

_CHARS = string.ascii_letters + string.digits


def _random_password(length: int = 12) -> str:
    return "".join(secrets.choice(_CHARS) for _ in range(length))


def seed(db: Session) -> None:
    print("\nSeeding users...")
    print("-" * 40)
    for spec in USERS:
        existing = db.query(User).filter(User.username == spec["username"]).first()
        if existing:
            print(f"  {spec['username']:12s}  already exists — skipped")
            continue
        pw = _random_password()
        user = User(
            name=spec["name"],
            username=spec["username"],
            password_hash=hash_password(pw),
            is_admin=spec["is_admin"],
            is_active=True,
        )
        db.add(user)
        db.flush()
        role = "admin" if spec["is_admin"] else "staff"
        print(f"  {spec['username']:12s}  password: {pw}  ({role})")
    db.commit()
    print("-" * 40)
    print("Done. Store passwords securely — they will not be shown again.\n")


if __name__ == "__main__":
    db: Session = SessionLocal()
    try:
        seed(db)
    finally:
        db.close()
