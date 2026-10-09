"""Underlying assets that have their own automation agent.

BTC rows written before ETH existed carry no ``asset`` field, so a missing value means BTC.
BTC run keys stay unprefixed for the same reason; every other asset prefixes its keys so
fixed-session and follow-up rows never collide with BTC rows under the same owner.
"""

from typing import Any, Literal, get_args

Asset = Literal["BTC", "ETH"]
ASSETS: tuple[Asset, ...] = get_args(Asset)
DEFAULT_ASSET: Asset = "BTC"


def parse_asset(value: Any) -> Asset:
    """Return a known asset, treating a missing value as the legacy BTC agent."""
    if value is None or value == "":
        return DEFAULT_ASSET
    normalized = str(value).upper()
    if normalized not in ASSETS:
        raise ValueError(f"Unsupported automation asset: {value}")
    return normalized  # type: ignore[return-value]


def run_asset(row: dict[str, Any] | None) -> Asset:
    return parse_asset((row or {}).get("asset"))


def strategy_asset(row: dict[str, Any]) -> Asset:
    """A strategy's asset comes from its instrument; manual strategies never carry ``asset``."""
    instrument = (row.get("definition_json") or {}).get("instrument") or {}
    return parse_asset(instrument.get("underlying") or row.get("asset"))


def asset_run_key(asset: Asset, key: str) -> str:
    return key if asset == DEFAULT_ASSET else f"{asset}:{key}"


def enabled_assets(settings: Any) -> tuple[Asset, ...]:
    """Assets whose agents the scheduler runs, from ``AUTOMATION_ASSETS`` (default: all)."""
    configured = getattr(settings, "automation_assets", None)
    if not configured:
        return ASSETS
    values = [item.strip() for item in str(configured).split(",") if item.strip()]
    return tuple(dict.fromkeys(parse_asset(value) for value in values))


def automation_asset_enabled(settings: dict[str, Any], asset: Asset) -> bool:
    """Old settings enable both assets until the owner explicitly pauses one."""
    return bool(settings.get("enabled", True) and settings.get("asset_enabled", {}).get(asset, True))
