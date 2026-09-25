from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    supabase_url: str = Field(validation_alias="NEXT_PUBLIC_SUPABASE_URL")
    supabase_publishable_key: str = Field(validation_alias="NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY")
    supabase_service_role_key: str = Field(validation_alias="SUPABASE_SERVICE_ROLE_KEY")
    application_storage: Literal["convex", "local"] = "convex"
    local_database_url: str | None = None
    local_reader_database_url: str | None = None
    recovery_mirror_enabled: bool = True
    convex_url: str | None = Field(default=None, validation_alias="CONVEX_URL")
    convex_sync_secret: str | None = Field(default=None, validation_alias="CONVEX_SYNC_SECRET")
    convex_trading_secret: str | None = Field(default=None, validation_alias="CONVEX_TRADING_SECRET")
    convex_order_journal_enabled: bool = False
    convex_library_enabled: bool = False
    convex_accounts_enabled: bool = False
    convex_runtime_enabled: bool = False
    shared_analysis_enabled: bool = True
    convex_credential_key: str | None = None
    analysis_service_secret: str | None = None
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
    def validate_convex_cutover(self) -> "Settings":
        if self.application_storage == "local":
            if not self.local_database_url:
                raise ValueError("Local application storage requires LOCAL_DATABASE_URL")
            if not self.trading_writer_enabled and not self.local_reader_database_url:
                raise ValueError("Local read replicas require LOCAL_READER_DATABASE_URL")
            if not self.analysis_service_secret:
                raise ValueError("Local application storage requires ANALYSIS_SERVICE_SECRET")
            if self.recovery_mirror_enabled and (not self.convex_url or not self.convex_trading_secret):
                raise ValueError("Local application storage requires Convex recovery-mirror credentials")
            if not all((self.convex_runtime_enabled, self.convex_library_enabled,
                        self.convex_accounts_enabled, self.convex_order_journal_enabled)):
                raise ValueError("Local application storage requires the complete trading cutover")
            return self
        if self.convex_runtime_enabled and not all(
            (self.convex_library_enabled, self.convex_accounts_enabled, self.convex_order_journal_enabled)
        ):
            raise ValueError("Convex runtime requires library, account and order-journal storage together")
        if any(
            (
                self.convex_runtime_enabled,
                self.convex_library_enabled,
                self.convex_accounts_enabled,
                self.convex_order_journal_enabled,
            )
        ) and not all((self.convex_url, self.convex_trading_secret)):
            raise ValueError("Enabled Convex storage requires its URL and trading service secret")
        if self.convex_accounts_enabled and not self.convex_credential_key:
            raise ValueError("Convex account storage requires CONVEX_CREDENTIAL_KEY")
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
