"""
Menu engineering analysis — BCG-style classification of menu items.

Classification uses population medians (not fixed thresholds) so results
re-calibrate automatically as sales data grows.

    Star      = high volume + high contribution  → feature / upsell
    Workhorse = high volume + low contribution   → raise price or bundle
    Puzzle    = low  volume + high contribution  → promote / reposition
    Dog       = low  volume + low contribution   → candidate to cut
"""

import statistics
from decimal import Decimal
from typing import Any
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models.menu_item import MenuItem
from app.models.item_sale import ItemSale

RECOMMENDATIONS = {
    "Star":      "Feature prominently and upsell — highest value driver.",
    "Workhorse": "High volume but thin margin — consider a price increase or combo bundle.",
    "Puzzle":    "Good margin but low volume — promote, reposition, or add to combos.",
    "Dog":       "Low volume and low margin — review for removal or reformulation.",
}


def get_analysis(db: Session) -> list[dict[str, Any]]:
    """Return menu engineering analysis for all food items with sales data."""

    # Aggregate sales per canonical item name
    sales_agg = (
        db.query(
            ItemSale.item_name,
            func.sum(ItemSale.qty).label("units"),
            func.sum(ItemSale.revenue).label("revenue"),
        )
        .group_by(ItemSale.item_name)
        .all()
    )
    if not sales_agg:
        return []

    sales_map: dict[str, dict] = {
        r.item_name: {"units": Decimal(str(r.units)), "revenue": Decimal(str(r.revenue))}
        for r in sales_agg
    }

    # Load food items that appear in sales
    food_items: list[MenuItem] = (
        db.query(MenuItem)
        .filter(MenuItem.is_food.is_(True), MenuItem.is_active.is_(True))
        .all()
    )

    rows = []
    for item in food_items:
        if item.name not in sales_map:
            continue
        s = sales_map[item.name]
        units = s["units"]
        revenue = s["revenue"]

        # Use derived cost when the engine has run; fall back to the per-item DB estimate.
        confidence = getattr(item, "cost_confidence", "none") or "none"
        derived = getattr(item, "derived_food_cost_pct", None)
        if confidence != "none" and derived is not None:
            fcp = Decimal(str(derived))
            cost_basis = "Derived"
        else:
            fcp = item.food_cost_pct  # read from DB — never hardcoded here
            cost_basis = "Estimated"
            confidence = "none"

        avg_price = revenue / units if units else Decimal("0")
        margin_pct = Decimal("1") - fcp
        contribution_per_unit = avg_price * margin_pct
        total_contribution = contribution_per_unit * units

        rows.append({
            "item": item.name,
            "category": item.category,
            "units": float(units),
            "revenue": float(revenue),
            "avg_price": float(avg_price.quantize(Decimal("0.01"))),
            "food_cost_pct": float(fcp),
            "contribution_per_unit": float(contribution_per_unit.quantize(Decimal("0.01"))),
            "total_contribution": float(total_contribution.quantize(Decimal("0.01"))),
            "margin_pct": float(margin_pct),
            "cost_basis": cost_basis,
            "cost_confidence": confidence,
        })

    if not rows:
        return []

    # Compute medians for classification
    median_units = statistics.median(r["units"] for r in rows)
    median_cpu = statistics.median(r["contribution_per_unit"] for r in rows)

    for r in rows:
        high_vol = r["units"] >= median_units
        high_cpu = r["contribution_per_unit"] >= median_cpu
        if high_vol and high_cpu:
            cls = "Star"
        elif high_vol and not high_cpu:
            cls = "Workhorse"
        elif not high_vol and high_cpu:
            cls = "Puzzle"
        else:
            cls = "Dog"
        r["classification"] = cls
        r["recommendation"] = RECOMMENDATIONS[cls]

    rows.sort(key=lambda r: r["total_contribution"], reverse=True)
    return rows
