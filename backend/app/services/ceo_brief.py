"""Reads the CEO Daily Brief views (v_ceo_brief_summary, v_ceo_brief_menu,
v_ceo_brief_actions). Views are queried directly — nothing to write."""
from sqlalchemy import text
from sqlalchemy.orm import Session


def get_summary(db: Session) -> dict | None:
    row = db.execute(text("SELECT * FROM v_ceo_brief_summary")).mappings().first()
    return dict(row) if row else None


def get_menu(db: Session) -> list[dict]:
    rows = db.execute(text("SELECT * FROM v_ceo_brief_menu")).mappings().all()
    return [dict(r) for r in rows]


def get_actions(db: Session) -> list[dict]:
    rows = db.execute(text("SELECT * FROM v_ceo_brief_actions ORDER BY priority")).mappings().all()
    return [dict(r) for r in rows]
