"""Stock kept at home, and moving it to the restaurant.

ingredient_stock is what the kitchen can use (the restaurant). home_stock is the
same shape for goods stored at home: purchases marked Home post here (migration
0056 trigger), a physical count sets it, and move_home_to_restaurant() carries
quantity across in one transaction without touching purchases or costs.
"""
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.services.stock_count import _in_item_unit


class StockMoveError(ValueError):
    """A refusal the buyer can act on (not enough at home, bad quantity)."""


def _message(exc: DBAPIError) -> str:
    return str(getattr(exc, "orig", exc)).splitlines()[0].replace("ERROR:", "").strip()


def home_balances(db: Session) -> dict[int, dict]:
    """{ingredient_id: latest home row} with who wrote it."""
    rows = db.execute(text("""
        SELECT DISTINCT ON (h.ingredient_id) h.ingredient_id, h.on_hand_qty, h.unit, h.counted_at, h.note, u.name AS by_name
          FROM home_stock h LEFT JOIN users u ON u.id = h.counted_by
         ORDER BY h.ingredient_id, h.counted_at DESC, h.id DESC
    """)).mappings().all()
    return {r["ingredient_id"]: dict(r) for r in rows}


def _item(db: Session, ingredient_id: int) -> dict | None:
    row = db.execute(text("SELECT id, unit::text AS unit, pack_size_g FROM ingredients WHERE id = :i AND is_active"),
                     {"i": ingredient_id}).mappings().first()
    return dict(row) if row else None


def _per_kg(item: dict, stock_unit: str) -> float | None:
    from app.services.stock_count import UNIT_FACTOR
    if item["unit"] == "pcs" and item["pack_size_g"]:
        return 1000.0 / float(item["pack_size_g"])
    return UNIT_FACTOR.get((stock_unit, item["unit"]))


def balances_in_item_unit(db: Session, ingredient_id: int) -> dict:
    """Restaurant and home quantity now, in the item's own unit (what the page shows)."""
    item = _item(db, ingredient_id)
    rest = db.execute(text("SELECT on_hand_qty, unit FROM ingredient_stock WHERE ingredient_id = :i "
                           "ORDER BY counted_at DESC, id DESC LIMIT 1"), {"i": ingredient_id}).mappings().first()
    home = db.execute(text("SELECT on_hand_qty, unit FROM home_stock WHERE ingredient_id = :i "
                           "ORDER BY counted_at DESC, id DESC LIMIT 1"), {"i": ingredient_id}).mappings().first()

    def conv(row):
        if row is None:
            return None
        return _in_item_unit(dict(row), item, _per_kg(item, row["unit"]))
    return {"unit": item["unit"], "restaurant": conv(rest), "home": conv(home)}


def move_to_restaurant(db: Session, ingredient_id: int, qty: float, user_id: int, note: str | None) -> dict:
    item = _item(db, ingredient_id)
    if item is None:
        raise StockMoveError("Item not found")
    try:
        transfer_id = db.execute(
            text("SELECT move_home_to_restaurant(:i, :q, :u, :by, :n)"),
            {"i": ingredient_id, "q": qty, "u": item["unit"], "by": user_id, "n": note},
        ).scalar_one()
        db.commit()
    except DBAPIError as exc:
        db.rollback()
        raise StockMoveError(_message(exc)) from exc
    return {"transfer_id": transfer_id, **balances_in_item_unit(db, ingredient_id)}


def set_home_count(db: Session, ingredient_id: int, qty: float, user_id: int) -> dict:
    """A physical count of what is at home: becomes the balance (purchases entered
    before it are taken as already included, like a restaurant count)."""
    item = _item(db, ingredient_id)
    if item is None:
        raise StockMoveError("Item not found")
    try:
        db.execute(text("""
            INSERT INTO home_stock (ingredient_id, on_hand_qty, unit, counted_by, note)
            VALUES (:i, stock_qty_to_target(:i, :q, :u), stock_target_unit(:i), :by, 'home_count')
        """), {"i": ingredient_id, "q": qty, "u": item["unit"], "by": user_id})
        db.commit()
    except DBAPIError as exc:
        db.rollback()
        raise StockMoveError(_message(exc)) from exc
    return balances_in_item_unit(db, ingredient_id)
