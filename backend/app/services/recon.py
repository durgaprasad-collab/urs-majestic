"""Reads the data-reconciliation views (v_recon_daily, v_recon_channel_status,
v_data_trust) and writes acknowledgements to recon_exceptions. Views are
queried directly rather than modeled (nothing to write through the ORM)."""
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.recon_exception import ReconException

CHECK_NAME_BY_CHANNEL = {
    "petpooja": "itemsales_vs_channel",
}
DEFAULT_CHECK_NAME = "orders_vs_channel"


def check_name_for_channel(channel: str) -> str:
    return CHECK_NAME_BY_CHANNEL.get(channel, DEFAULT_CHECK_NAME)


def get_data_trust(db: Session) -> dict | None:
    row = db.execute(text("SELECT * FROM v_data_trust")).mappings().first()
    return dict(row) if row else None


def get_channel_status(db: Session) -> list[dict]:
    rows = db.execute(text("SELECT * FROM v_recon_channel_status ORDER BY channel")).mappings().all()
    return [dict(r) for r in rows]


def get_daily_recon(db: Session, status: str | None = None) -> list[dict]:
    if status:
        rows = db.execute(
            text("SELECT * FROM v_recon_daily WHERE status = :status ORDER BY business_date, channel"),
            {"status": status},
        ).mappings().all()
    else:
        rows = db.execute(
            text("SELECT * FROM v_recon_daily ORDER BY business_date, channel")
        ).mappings().all()
    return [dict(r) for r in rows]


def upsert_exception(
    db: Session,
    *,
    business_date,
    channel: str,
    explanation: str,
    acknowledged_by: str,
    expected=None,
    actual=None,
) -> ReconException:
    check_name = check_name_for_channel(channel)
    stmt = pg_insert(ReconException).values(
        business_date=business_date,
        channel=channel,
        check_name=check_name,
        expected=expected,
        actual=actual,
        explanation=explanation,
        acknowledged_by=acknowledged_by,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["business_date", "channel", "check_name"],
        set_={
            "expected": stmt.excluded.expected,
            "actual": stmt.excluded.actual,
            "explanation": stmt.excluded.explanation,
            "acknowledged_by": stmt.excluded.acknowledged_by,
        },
    ).returning(ReconException.id)
    exc_id = db.execute(stmt).scalar_one()
    db.flush()
    # populate_existing: force a fresh read — the row may already be in the
    # identity map from a prior upsert and would otherwise return stale data.
    return db.get(ReconException, exc_id, populate_existing=True)
