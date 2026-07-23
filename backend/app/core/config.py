from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str
    SECRET_KEY: str = "change-me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # "production" enables HTTPS redirect, Secure cookie, HSTS, hidden errors
    ENVIRONMENT: str = "development"

    # The restaurant's operating timezone. "Today" for same-day-export guards and
    # effective-date defaults is computed here, NOT in the server's clock (Render
    # runs UTC), so a completed IST business day is never mistaken for "today".
    BUSINESS_TIMEZONE: str = "Asia/Kolkata"

    # Locked CORS origin for the admin subdomain; empty = CORS disabled entirely
    ADMIN_ORIGIN: str = "https://admin.ursmajestic.com"

    # Upload limits
    UPLOAD_MAX_MB: int = 10

    # Login rate-limit: max attempts per window before lockout
    LOGIN_MAX_ATTEMPTS: int = 5
    LOGIN_WINDOW_SECONDS: int = 900  # 15 minutes

    # Public site origin (for feedback CORS)
    SITE_ORIGIN: str = "https://ursmajestic.com"

    # Feedback rate-limit: max submissions per IP per minute
    FEEDBACK_RATE_LIMIT: int = 5
    FEEDBACK_WINDOW_SECONDS: int = 60

    # Daily Brief v2 ──────────────────────────────────────────────────────────
    # Monthly net-sales target (₹). 0 = unset → the brief shows MTD achievement
    # without a target line. Set per outlet via env, no redeploy of code needed.
    MONTHLY_SALES_TARGET: float = 0.0
    # Assumed blended food-cost fraction for the *estimated* contribution figure,
    # until per-item costs feed the calc. 0.35 = current menu-wide average.
    ASSUMED_FOOD_COST_PCT: float = 0.35

    # Daily Brief v3 ──────────────────────────────────────────────────────────
    # Manual / gracefully-degrading inputs. All 0 = "not connected"/"not tracked"
    # until a live feed replaces them. None of these invent data — an unset value
    # renders as a clearly-labelled placeholder, never a misleading zero.
    GOOGLE_RATING: float = 0.0          # 0 = not connected
    ZOMATO_RATING: float = 0.0          # 0 = not connected
    SWIGGY_RATING: float = 0.0          # 0 = not connected
    REVIEWS_AWAITING_RESPONSE: int = -1  # -1 = not tracked
    AVG_PREP_TIME_MIN: float = 0.0      # 0 = not tracked (manual input for now)
    # Attention-rule thresholds
    RATING_ALERT_THRESHOLD: float = 4.0        # flag any live rating below this
    DELIVERY_DISCOUNT_POLICY_PCT: float = 25.0  # funded discount % of gross that's "over policy"

    # WhatsApp Daily Brief ──────────────────────────────────────────────────────
    # Meta WhatsApp Cloud API. With TOKEN/PHONE_ID empty the daily job runs in
    # dry-run only (prints, never sends), so nothing breaks before setup is done.
    WHATSAPP_TOKEN: str = ""            # permanent access token (sent as Bearer)
    WHATSAPP_PHONE_ID: str = ""         # WhatsApp phone number ID (Cloud API)
    WHATSAPP_API_VERSION: str = "v21.0"
    WHATSAPP_TEMPLATE_NAME: str = "daily_brief"   # must be approved in Meta first
    # Order-only template for the evening run (--only order); 2 params: date, list.
    WHATSAPP_ORDER_TEMPLATE_NAME: str = "order_forecast"
    WHATSAPP_TEMPLATE_LANG: str = "en"
    # Comma-separated recipients in E.164 without '+' (91 = India). Owner + kitchen.
    WHATSAPP_RECIPIENTS: str = "919150102001,919884194662,919985335358"
    # List sizes for the two single-line summaries (template params can't hold
    # newlines, so lists are separator-delimited and capped).
    WHATSAPP_PREP_LIMIT: int = 10
    WHATSAPP_ORDER_LIMIT: int = 15

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


settings = Settings()
