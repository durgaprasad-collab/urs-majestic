"""Reads the two dashboard KPI views (v_kpi_yesterday_sales,
v_channel_upload_status). Both are plain SQL views maintained outside the
ORM — queried directly rather than modeled, since there's nothing to write."""
from sqlalchemy import text
from sqlalchemy.orm import Session


def get_yesterday_sales(db: Session) -> dict | None:
    row = db.execute(text("SELECT * FROM v_kpi_yesterday_sales")).mappings().first()
    return dict(row) if row else None


def get_channel_upload_status(db: Session) -> list[dict]:
    rows = db.execute(text("SELECT * FROM v_channel_upload_status ORDER BY channel")).mappings().all()
    return [dict(r) for r in rows]
