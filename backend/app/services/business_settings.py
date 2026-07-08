"""Business Settings service — the ONE canonical source of truth for the
Restaurant OS's financial model: fixed recurring expenses, desired profit,
contribution-margin assumption, and growth target.

Every other module (target engine, KPIs, future forecasting / AI / marketing)
must read its financial assumptions through here rather than storing its own,
so the whole system stays consistent as URS Majestic grows.
"""
import decimal
from datetime import date
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.business import (
    FixedExpense,
    BusinessSetting,
    FREQUENCY_MONTHS,
    SETTING_DESIRED_PROFIT,
    SETTING_CONTRIBUTION_MARGIN_PCT,
    SETTING_GROWTH_PCT,
)

D = decimal.Decimal
_CENT = D("0.01")

# Suggested categories for the expense form + summary grouping. Free-text is
# allowed (an unknown category simply forms its own summary row), so new cost
# types never need a code change.
CATEGORIES = ["Premises", "Salaries", "Utilities", "Marketing", "Compliance", "Finance", "Other"]

FREQUENCY_LABELS = {
    "monthly": "Monthly",
    "quarterly": "Quarterly",
    "half_yearly": "Half-Yearly",
    "yearly": "Yearly",
}

# Fallback assumptions if a setting has never been written. Seeding installs real
# rows; these keep the engine safe on a brand-new DB.
_SETTING_DEFAULTS = {
    SETTING_DESIRED_PROFIT: D("100000"),
    SETTING_CONTRIBUTION_MARGIN_PCT: D("60"),
    SETTING_GROWTH_PCT: D("10"),
}


def monthly_equivalent(amount: decimal.Decimal, frequency: str) -> decimal.Decimal:
    months = FREQUENCY_MONTHS.get(frequency, 1)
    return (D(str(amount)) / months).quantize(_CENT)


# ── expenses ─────────────────────────────────────────────────────────────────
def get_active_expenses(db: Session, as_of: date | None = None) -> list[FixedExpense]:
    """Expenses that are active and within their effective window on `as_of`."""
    as_of = as_of or date.today()
    stmt = (
        select(FixedExpense)
        .where(
            FixedExpense.active.is_(True),
            FixedExpense.effective_from <= as_of,
            (FixedExpense.effective_to.is_(None)) | (FixedExpense.effective_to >= as_of),
        )
        .order_by(FixedExpense.category, FixedExpense.name)
    )
    return list(db.execute(stmt).scalars())


def get_all_expenses(db: Session) -> list[FixedExpense]:
    """Every expense (active + inactive) for the management page."""
    return list(
        db.execute(select(FixedExpense).order_by(FixedExpense.active.desc(), FixedExpense.category, FixedExpense.name)).scalars()
    )


def get_expense(db: Session, expense_id: int) -> FixedExpense | None:
    return db.get(FixedExpense, expense_id)


def monthly_fixed_total(db: Session, as_of: date | None = None) -> decimal.Decimal:
    return sum((e.monthly_equivalent for e in get_active_expenses(db, as_of)), D(0)).quantize(_CENT)


def category_summary(db: Session, as_of: date | None = None) -> tuple[list[dict], decimal.Decimal]:
    """[{category, monthly}] sorted by cost desc, plus the grand total. Every
    suggested category is present (0 if none) so the summary reads consistently."""
    totals: dict[str, decimal.Decimal] = {c: D(0) for c in CATEGORIES}
    for e in get_active_expenses(db, as_of):
        totals[e.category] = totals.get(e.category, D(0)) + e.monthly_equivalent
    rows = [{"category": c, "monthly": v.quantize(_CENT)} for c, v in totals.items()]
    rows.sort(key=lambda r: r["monthly"], reverse=True)
    grand = sum((r["monthly"] for r in rows), D(0)).quantize(_CENT)
    return rows, grand


def create_expense(db: Session, *, name, category, amount, frequency, effective_from=None,
                   effective_to=None, notes=None, active=True) -> FixedExpense:
    exp = FixedExpense(
        name=name.strip(),
        category=(category or "Other").strip(),
        amount=D(str(amount)),
        frequency=frequency,
        active=active,
        effective_from=effective_from or date.today(),
        effective_to=effective_to,
        notes=(notes or None),
    )
    db.add(exp)
    db.commit()
    db.refresh(exp)
    return exp


def update_expense(db: Session, expense_id: int, **fields) -> FixedExpense | None:
    exp = db.get(FixedExpense, expense_id)
    if not exp:
        return None
    for k in ("name", "category", "amount", "frequency", "active", "effective_from", "effective_to", "notes"):
        if k in fields and fields[k] is not None or (k in ("effective_to", "notes") and k in fields):
            setattr(exp, k, fields[k])
    if "amount" in fields and fields["amount"] is not None:
        exp.amount = D(str(fields["amount"]))
    db.commit()
    db.refresh(exp)
    return exp


def set_expense_active(db: Session, expense_id: int, active: bool) -> FixedExpense | None:
    exp = db.get(FixedExpense, expense_id)
    if not exp:
        return None
    exp.active = active
    db.commit()
    db.refresh(exp)
    return exp


# ── settings (append-only history) ───────────────────────────────────────────
def get_setting(db: Session, key: str, as_of: date | None = None) -> decimal.Decimal:
    """Current value of a setting = latest effective row on/before `as_of`.
    Falls back to the built-in default if never configured."""
    as_of = as_of or date.today()
    stmt = (
        select(BusinessSetting.value)
        .where(BusinessSetting.setting_key == key, BusinessSetting.effective_from <= as_of)
        .order_by(BusinessSetting.effective_from.desc(), BusinessSetting.id.desc())
        .limit(1)
    )
    val = db.execute(stmt).scalar_one_or_none()
    return D(str(val)) if val is not None else _SETTING_DEFAULTS.get(key, D(0))


def set_setting(db: Session, key: str, value, *, effective_from=None, note=None, created_by=None) -> BusinessSetting:
    """Append a new value — never overwrites history."""
    row = BusinessSetting(
        setting_key=key,
        value=D(str(value)),
        effective_from=effective_from or date.today(),
        note=note,
        created_by=created_by,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_financials(db: Session, as_of: date | None = None) -> dict:
    """The three tunable assumptions, current values — in one query."""
    as_of = as_of or date.today()
    keys = [SETTING_DESIRED_PROFIT, SETTING_CONTRIBUTION_MARGIN_PCT, SETTING_GROWTH_PCT]
    stmt = (
        select(BusinessSetting.setting_key, BusinessSetting.value)
        .where(BusinessSetting.setting_key.in_(keys), BusinessSetting.effective_from <= as_of)
        .order_by(BusinessSetting.setting_key, BusinessSetting.effective_from.desc(), BusinessSetting.id.desc())
        .distinct(BusinessSetting.setting_key)
    )
    current = {k: D(str(v)) for k, v in db.execute(stmt).all()}
    return {
        "desired_profit": current.get(SETTING_DESIRED_PROFIT, _SETTING_DEFAULTS[SETTING_DESIRED_PROFIT]),
        "margin_pct": current.get(SETTING_CONTRIBUTION_MARGIN_PCT, _SETTING_DEFAULTS[SETTING_CONTRIBUTION_MARGIN_PCT]),
        "growth_pct": current.get(SETTING_GROWTH_PCT, _SETTING_DEFAULTS[SETTING_GROWTH_PCT]),
    }


def setting_history(db: Session, key: str, limit: int = 12) -> list[BusinessSetting]:
    stmt = (
        select(BusinessSetting)
        .where(BusinessSetting.setting_key == key)
        .order_by(BusinessSetting.effective_from.desc(), BusinessSetting.id.desc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars())
