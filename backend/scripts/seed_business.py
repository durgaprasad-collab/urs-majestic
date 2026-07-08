"""Seed the Business Settings module with the owner's CURRENT recurring
expenses and default financial assumptions — as editable DB rows, not code.

Idempotent: only seeds when the tables are empty, so re-running is safe. The
owner edits everything from the Owner Portal afterwards.

Run from backend/:  D:\\URS_Majestic\\.venv\\Scripts\\python.exe -m scripts.seed_business
"""
from datetime import date
from app.core.database import SessionLocal
from app.models.business import (
    FixedExpense,
    BusinessSetting,
    SETTING_DESIRED_PROFIT,
    SETTING_CONTRIBUTION_MARGIN_PCT,
    SETTING_GROWTH_PCT,
)

# (name, category, amount, frequency, notes) — the owner's current configuration.
_EXPENSES = [
    ("Rent", "Premises", 45000, "monthly", None),
    ("GST on Rent", "Premises", 8100, "monthly", None),
    ("Maintenance", "Premises", 10000, "monthly", None),
    ("Salaries", "Salaries", 115000, "monthly", None),
    ("Electricity", "Utilities", 4000, "monthly", None),
    ("Internet", "Utilities", 5000, "quarterly", "₹5,000 every 3 months"),
]

_SETTINGS = [
    (SETTING_DESIRED_PROFIT, 100000),
    (SETTING_CONTRIBUTION_MARGIN_PCT, 60),
    (SETTING_GROWTH_PCT, 10),
]


def main() -> None:
    db = SessionLocal()
    try:
        today = date.today()
        if db.query(FixedExpense).count() == 0:
            for name, cat, amt, freq, notes in _EXPENSES:
                db.add(FixedExpense(name=name, category=cat, amount=amt, frequency=freq,
                                    active=True, effective_from=today, notes=notes))
            print(f"seeded {len(_EXPENSES)} fixed expenses")
        else:
            print("fixed_expenses already populated — skipping")

        if db.query(BusinessSetting).count() == 0:
            for key, val in _SETTINGS:
                db.add(BusinessSetting(setting_key=key, value=val, effective_from=today,
                                       note="initial default", created_by="seed"))
            print(f"seeded {len(_SETTINGS)} business settings")
        else:
            print("business_settings already populated — skipping")

        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
