"""Audit trail for financial record changes.

Every edit and every soft delete of a purchase row writes here BEFORE the
change is committed, in the same transaction. If the transaction rolls back
the log entry rolls back with it, so the log can never claim a change that
did not happen — and a change can never happen without the log entry.

The target table is cost_base_repair_log, which already carries the history
of assistant-run cost repairs. Columns:
    batch, target_table, target_id, field, old_value, new_value,
    reason, actor_user_id, repaired_at (default now())
"""
from decimal import Decimal
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.menu_engineering.cost_engine import run_cost_engine

_INSERT = text(
    """
    INSERT INTO cost_base_repair_log
        (batch, target_table, target_id, field, old_value, new_value,
         reason, actor_user_id)
    VALUES
        (:batch, :target_table, :target_id, :field, :old_value, :new_value,
         :reason, :actor_user_id)
    """
)


def _s(value) -> str | None:
    """Render a value for the log without lying about its type."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def log_change(
    db: Session,
    *,
    batch: str,
    target_table: str,
    target_id: int,
    field: str | None,
    old_value,
    new_value,
    reason: str,
    actor_user_id: int,
) -> None:
    """Write one field-level change. Caller commits."""
    db.execute(
        _INSERT,
        {
            "batch": batch,
            "target_table": target_table,
            "target_id": target_id,
            "field": field,
            "old_value": _s(old_value),
            "new_value": _s(new_value),
            "reason": reason,
            "actor_user_id": actor_user_id,
        },
    )


def log_field_diffs(
    db: Session,
    *,
    batch: str,
    target_table: str,
    target_id: int,
    before: dict,
    after: dict,
    reason: str,
    actor_user_id: int,
) -> list[str]:
    """Write one row per field that actually changed. Returns the field names.

    Fields whose value is unchanged are not logged — a log full of no-ops is
    a log nobody reads.
    """
    changed: list[str] = []
    for field, old in before.items():
        new = after.get(field)
        if _s(old) == _s(new):
            continue
        changed.append(field)
        log_change(
            db,
            batch=batch,
            target_table=target_table,
            target_id=target_id,
            field=field,
            old_value=old,
            new_value=new,
            reason=reason,
            actor_user_id=actor_user_id,
        )
    return changed


def resync_derived_costs(db: Session) -> int:
    """Recompute every menu_items cost snapshot after a purchase change.

    menu_items carries a frozen snapshot of each dish's cost that does not
    recompute on its own. Call this after ANY purchase insert, edit or soft
    delete or the menu keeps quoting pre-change costs.

    Delegates to the Python cost engine (run_cost_engine) so the purchase-edit
    path and the "Run cost engine" button share ONE cost model. The old SQL
    resync_derived_costs() function read the v_menu_item_cost VIEW -- a second,
    diverging model that lacked the engine's combo/overhead/pcs handling and let
    the 100x scale bug slip in; it is no longer used. Runs inside the caller's
    transaction (no commit). Returns the number of active food items recomputed.
    """
    return int(run_cost_engine(db).get("items_updated", 0))
