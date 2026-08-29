from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    supabase_url: str = Field(validation_alias="NEXT_PUBLIC_SUPABASE_URL")
    supabase_publishable_key: str = Field(validation_alias="NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY")
    supabase_service_role_key: str = Field(validation_alias="SUPABASE_SERVICE_ROLE_KEY")
    convex_url: str | None = Field(default=None, validation_alias="CONVEX_URL")
    convex_sync_secret: str | None = Field(default=None, validation_alias="CONVEX_SYNC_SECRET")
    delta_production_url: str = "https://api.india.delta.exchange"
    scheduler_enabled: bool = True
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
