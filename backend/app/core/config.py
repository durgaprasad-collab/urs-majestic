from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str
    SECRET_KEY: str = "change-me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # "production" enables HTTPS redirect, Secure cookie, HSTS, hidden errors
    ENVIRONMENT: str = "development"

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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


settings = Settings()
