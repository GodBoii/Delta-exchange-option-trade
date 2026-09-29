from functools import lru_cache

from pydantic import Field, field_validator, model_validator
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
    frontend_origins: str = (
        "http://localhost:3000,"
        "https://delta-exchange-option-trade.vercel.app,"
        "https://tradecognition.online,"
        "https://www.tradecognition.online"
    )
    frontend_origin_regex: str = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"
    log_level: str = "INFO"
    market_history_path: str = "data/market-history.sqlite"
    binance_futures_base_url: str = "https://fapi.binance.com"
    analysis_service_secret: str = ""

    @field_validator("binance_base_url", "binance_ws_url", "delta_public_base_url")
    @classmethod
    def clean_base_url(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("delta_symbol")
    @classmethod
    def supported_delta_symbol(cls, value: str) -> str:
        if value not in {"BTCUSD", "ETHUSD"}:
            raise ValueError("DELTA_SYMBOL must be BTCUSD or ETHUSD")
        return value

    @model_validator(mode="after")
    def matching_market_symbols(self) -> "Settings":
        if self.binance_symbol != f"{self.base_asset}USDT" or self.delta_symbol != f"{self.base_asset}USD":
            raise ValueError("BINANCE_SYMBOL and DELTA_SYMBOL must refer to the same supported asset")
        return self

    @property
    def market_route(self) -> str:
        """URL segment for this instance's market, e.g. ``btcusd`` or ``ethusd``."""
        return self.delta_symbol.lower()

    @property
    def base_asset(self) -> str:
        quote = "USDT"
        symbol = self.binance_symbol.upper()
        return symbol[: -len(quote)] if symbol.endswith(quote) else symbol

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip().rstrip("/") for origin in self.frontend_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
