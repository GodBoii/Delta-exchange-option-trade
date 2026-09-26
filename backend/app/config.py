from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    supabase_url: str = Field(validation_alias="NEXT_PUBLIC_SUPABASE_URL")
    supabase_publishable_key: str = Field(validation_alias="NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY")
    supabase_service_role_key: str = Field(validation_alias="SUPABASE_SERVICE_ROLE_KEY")
    local_database_url: str = Field(validation_alias="LOCAL_DATABASE_URL")
    local_reader_database_url: str | None = Field(default=None, validation_alias="LOCAL_READER_DATABASE_URL")
    # The writer keeps the analysis service's least-privilege role in sync with this URL.
    ai_database_url: str | None = Field(default=None, validation_alias="AI_DATABASE_URL")
    database_pool_size: int = Field(default=20, ge=2, le=200)
    # Fernet key for Delta API credentials at rest. Changing it makes stored credentials unreadable.
    credential_encryption_key: str = Field(validation_alias="CREDENTIAL_ENCRYPTION_KEY")
    # Authenticates the analysis service and signs short-lived chart links.
    analysis_service_secret: str = Field(min_length=16, validation_alias="ANALYSIS_SERVICE_SECRET")
    shared_analysis_enabled: bool = True
    auth_profile_cache_seconds: float = Field(default=60, ge=0, le=3600)
    chart_retention_days: int = Field(default=90, ge=1, le=3650)
    chart_link_seconds: int = Field(default=3_600, ge=60, le=86_400)
    delta_events_enabled: bool = False
    delta_public_ws_url: str = "wss://public-socket.india.delta.exchange"
    delta_private_ws_url: str = "wss://socket.india.delta.exchange"
    delta_mark_max_age_seconds: float = 5.0
    entry_fill_deadline_seconds: float = Field(default=60, ge=5, le=600)
    trading_lock_path: str = "/app/state/trading.lock"
    delta_production_url: str = "https://api.india.delta.exchange"
    scheduler_enabled: bool = True
    trading_writer_enabled: bool = True
    automation_scheduler_enabled: bool = True
    automation_analysis_concurrency: int = Field(default=3, ge=1, le=32)
    automation_recheck_concurrency: int = Field(default=4, ge=1, le=32)
    execution_account_concurrency: int = Field(default=8, ge=1, le=128)
    shared_allocation_concurrency: int = Field(default=8, ge=1, le=128)
    risk_state_persist_seconds: float = Field(default=10, ge=2, le=60)
    account_group_cache_seconds: float = Field(default=30, ge=0, le=300)
    scheduler_poll_seconds: float = 2.0
    max_entry_lateness_seconds: int = 180
    exit_verify_timeout_seconds: float = 10.0
    exit_verify_poll_seconds: float = 0.5
    frontend_origins: str = (
        "http://localhost:3000,"
        "https://delta-exchange-option-trade.vercel.app,"
        "https://tradecognition.online,"
        "https://www.tradecognition.online"
    )
    frontend_origin_regex: str = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"
    log_level: str = "INFO"

    @model_validator(mode="after")
    def validate_database_roles(self) -> "Settings":
        if not self.trading_writer_enabled and not self.local_reader_database_url:
            raise ValueError("Read replicas require LOCAL_READER_DATABASE_URL")
        return self

    @field_validator("scheduler_poll_seconds")
    @classmethod
    def validate_poll_interval(cls, value: float) -> float:
        return max(1.0, value)

    @field_validator("exit_verify_timeout_seconds")
    @classmethod
    def validate_exit_timeout(cls, value: float) -> float:
        return max(1.0, value)

    @field_validator("exit_verify_poll_seconds")
    @classmethod
    def validate_exit_poll_interval(cls, value: float) -> float:
        return max(0.1, value)

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.frontend_origins.split(",") if origin.strip()]

    @property
    def database_url(self) -> str:
        """The writer uses its own role; read replicas connect with the read-only role."""
        if self.trading_writer_enabled:
            return self.local_database_url
        return self.local_reader_database_url or self.local_database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
