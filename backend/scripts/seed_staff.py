"""Seed the 4 staff accounts for the mobile app (requisitions/stock entry).

Run once:
    python scripts/seed_staff.py

6-digit numeric PINs instead of random passwords -- staff read/type these on
a phone, and numbers travel better across languages than mixed-case strings.
Prints credentials to stdout; save them immediately. Idempotent -- an
existing username is skipped.
"""
import os
import secrets
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.user import User

STAFF = [
    {"name": "Anil", "username": "anil"},
    {"name": "Anil Sada", "username": "anil_sada"},
    {"name": "Jhantu", "username": "jhantu"},
    {"name": "Prem", "username": "prem"},
]


def _random_pin() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def seed(db: Session) -> None:
    print("\nSeeding staff accounts...")
    print("-" * 40)
    for spec in STAFF:
        existing = db.query(User).filter(User.username == spec["username"]).first()
        if existing:
            print(f"  {spec['username']:12s}  already exists — skipped")
            continue
        pin = _random_pin()
        user = User(
            name=spec["name"],
            username=spec["username"],
            password_hash=hash_password(pin),
            is_admin=False,
            is_active=True,
            must_change_password=False,  # no change-password screen in the mobile app
        )
        db.add(user)
        db.flush()
        print(f"  {spec['username']:12s}  PIN: {pin}")
    db.commit()
    print("-" * 40)
    print("Done. Share each PIN with that staff member directly -- it won't be shown again.\n")


if __name__ == "__main__":
    db: Session = SessionLocal()
    try:
        seed(db)
    finally:
        db.close()
