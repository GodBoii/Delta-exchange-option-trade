from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    supabase_url: str = Field(validation_alias="NEXT_PUBLIC_SUPABASE_URL")
    supabase_publishable_key: str = Field(validation_alias="NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY")
    supabase_service_role_key: str = Field(validation_alias="SUPABASE_SERVICE_ROLE_KEY")
    delta_production_url: str = "https://api.india.delta.exchange"
    scheduler_enabled: bool = True
    scheduler_poll_seconds: float = 2.0
    max_entry_lateness_seconds: int = 60
    frontend_origins: str = "http://localhost:3000,https://delta-exchange-option-trade.vercel.app"
    frontend_origin_regex: str = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"
    log_level: str = "INFO"

    @field_validator("scheduler_poll_seconds")
    @classmethod
    def validate_poll_interval(cls, value: float) -> float:
        return max(1.0, value)

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.frontend_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
