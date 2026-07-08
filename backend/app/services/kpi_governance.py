"""KPI Governance (Daily Brief v3, requirement 11).

A single source of truth for what every KPI on the brief *means*: its plain
definition, the formula behind it, the data source, how often it refreshes, and
who owns it. This metadata is intentionally NOT rendered as visible UI — the
route embeds it as a hidden JSON block so that future AI explainability features
(an "explain this number" affordance, the AI Morning Brief, an audit view) have
a machine-readable contract to read, without cluttering the owner's screen.

Keep keys in sync with the KPI keys used by daily_brief_v3.build_brief().
"""

# key -> governance metadata
KPI_REGISTRY: dict[str, dict[str, str]] = {
    "yesterday_net_sales": {
        "definition": "Total net sales across all channels for the latest reported business day.",
        "formula": "SUM(daily_channel_sales.net_sales) WHERE business_date = reporting_date",
        "source": "daily_channel_sales (Petpooja / Zomato / Swiggy imports)",
        "refresh": "Daily, on file upload",
        "owner": "Restaurant Manager",
    },
    "pct_vs_prior_day": {
        "definition": "Change in net sales versus the immediately preceding business day.",
        "formula": "100 * (today_net - prior_day_net) / prior_day_net",
        "source": "v_kpi_yesterday_sales / v_ceo_brief_summary",
        "refresh": "Daily, on file upload",
        "owner": "Restaurant Manager",
    },
    "pct_vs_same_weekday": {
        "definition": "Change in net sales versus the same weekday last week (like-for-like).",
        "formula": "100 * (today_net - same_weekday_last_week_net) / same_weekday_last_week_net",
        "source": "v_ceo_brief_summary",
        "refresh": "Daily, on file upload",
        "owner": "Restaurant Manager",
    },
    "monthly_target": {
        "definition": "Owner-configured net-sales goal for the calendar month.",
        "formula": "settings.MONTHLY_SALES_TARGET (0 = not configured)",
        "source": "Configuration (environment)",
        "refresh": "On configuration change",
        "owner": "Owner",
    },
    "mtd_sales": {
        "definition": "Cumulative net sales from the 1st of the month through the reporting date.",
        "formula": "SUM(net_sales) WHERE business_date BETWEEN month_start AND reporting_date",
        "source": "daily_channel_sales",
        "refresh": "Daily, on file upload",
        "owner": "Restaurant Manager",
    },
    "target_achievement_pct": {
        "definition": "How much of the monthly target has been achieved so far.",
        "formula": "100 * mtd_sales / monthly_target",
        "source": "Derived (mtd_sales, monthly_target)",
        "refresh": "Daily",
        "owner": "Owner",
    },
    "expected_mtd_sales": {
        "definition": "Where month-to-date sales should be by now to stay on a straight-line pace.",
        "formula": "monthly_target * days_elapsed / days_in_month",
        "source": "Derived (monthly_target, calendar)",
        "refresh": "Daily",
        "owner": "Owner",
    },
    "target_variance": {
        "definition": "Amount ahead of (or behind) the expected month-to-date pace.",
        "formula": "mtd_sales - expected_mtd_sales",
        "source": "Derived",
        "refresh": "Daily",
        "owner": "Owner",
    },
    "estimated_contribution": {
        "definition": "Net sales minus estimated food cost. ESTIMATE — a flat food-cost "
        "assumption until per-item recipe costing is available.",
        "formula": "net_sales * (1 - ASSUMED_FOOD_COST_PCT)",
        "source": "daily_channel_sales x settings.ASSUMED_FOOD_COST_PCT",
        "refresh": "Daily",
        "owner": "Owner",
    },
    "contribution_pct": {
        "definition": "Estimated contribution as a share of net sales.",
        "formula": "100 * (1 - ASSUMED_FOOD_COST_PCT)",
        "source": "Derived",
        "refresh": "Daily",
        "owner": "Owner",
    },
    "seven_day_average": {
        "definition": "Average daily net sales over the last seven days with data.",
        "formula": "AVG(net_sales per day) over the last 7 days",
        "source": "daily_channel_sales",
        "refresh": "Daily",
        "owner": "Restaurant Manager",
    },
    "channel_split": {
        "definition": "Net sales attributed to each sales channel for the reporting date.",
        "formula": "net_sales grouped by channel (Counter/Zomato/Swiggy/Website)",
        "source": "v_ceo_brief_summary / daily_channel_sales",
        "refresh": "Daily, on file upload",
        "owner": "Restaurant Manager",
    },
    "delivery_discount_pct": {
        "definition": "Restaurant-funded discount as a share of gross order value on delivery.",
        "formula": "100 * restaurant_discount / gross_order_value",
        "source": "daily_channel_sales",
        "refresh": "Daily, on file upload",
        "owner": "Restaurant Manager",
    },
    "data_trust": {
        "definition": "Whether every channel reconciles against its cross-check for the period.",
        "formula": "'DATA TRUSTED' when unexplained_mismatches = 0 else 'NEEDS ATTENTION'",
        "source": "v_data_trust",
        "refresh": "Daily, on reconciliation",
        "owner": "Restaurant Manager",
    },
    "customer_ratings": {
        "definition": "Public platform ratings (Google / Zomato / Swiggy). Manual input "
        "until platform APIs are connected; unset renders as 'Not connected'.",
        "formula": "settings.{GOOGLE,ZOMATO,SWIGGY}_RATING (0 = not connected)",
        "source": "Configuration (manual) — future: platform APIs",
        "refresh": "Manual",
        "owner": "Owner",
    },
    "review_counts": {
        "definition": "Counter feedback captured via the QR review form.",
        "formula": "COUNT(customer_feedback), COUNT WHERE created_at >= now()-7d",
        "source": "customer_feedback",
        "refresh": "Real-time on submission",
        "owner": "Restaurant Manager",
    },
    "avg_prep_time": {
        "definition": "Average kitchen preparation time per order.",
        "formula": "settings.AVG_PREP_TIME_MIN (0 = not tracked; manual for now)",
        "source": "Configuration (manual) — future: POS KOT timestamps",
        "refresh": "Manual",
        "owner": "Kitchen Lead",
    },
}


def registry() -> dict[str, dict[str, str]]:
    """The full governance catalog (hidden JSON payload for the brief)."""
    return KPI_REGISTRY
