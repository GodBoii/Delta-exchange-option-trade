from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    binance_base_url: str = "https://data-api.binance.vision"
    binance_ws_url: str = "wss://stream.binance.com:9443/stream"
    binance_symbol: str = "BTCUSDT"
    delta_public_base_url: str = "https://api.india.delta.exchange"
    delta_symbol: str = "BTCUSD"
    delta_context_seconds: float = Field(default=5.0, ge=1, le=60)
    delta_history_seconds: float = Field(default=300.0, ge=60, le=3600)
    market_cache_seconds: float = Field(default=2.0, ge=0.5, le=60)
    market_broadcast_seconds: float = Field(default=0.25, ge=0.1, le=2)
    cvd_window_seconds: int = Field(default=900, ge=60, le=86_400)
    frontend_origins: str = "http://localhost:3000,https://delta-exchange-option-trade.vercel.app"
    frontend_origin_regex: str = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"
    log_level: str = "INFO"

    @field_validator("binance_base_url", "binance_ws_url", "delta_public_base_url")
    @classmethod
    def clean_base_url(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.frontend_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
