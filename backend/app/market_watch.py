"""Publish only aggregate expiry dates to public market collectors, never account data."""

import asyncio
import os
from datetime import datetime

import httpx

from app.automation_schedule import IST
from app.assets import ASSETS


async def publish_watchlists(db, client: httpx.AsyncClient) -> None:
    secret = getattr(db.settings, "analysis_service_secret", None)
    if not secret:
        return
    rows = await db.select(
        "strategies",
        {
            "select": "definition_json",
            "status": "in.(scheduled,executing_entry,active,executing_exit,attention)",
        },
    )
    dates = {asset: set() for asset in ASSETS}
    for row in rows:
        definition = row.get("definition_json") or {}
        asset = (definition.get("instrument") or {}).get("underlying")
        if asset in dates:
            dates[asset].update(leg["expiry"] for leg in definition.get("legs", []) if leg.get("expiry"))

    async def publish(asset: str) -> None:
        if not dates[asset]:
            return
        # The trading image has no Agno/automation_agent package. Use its existing market-service env names.
        env_name = "BINANCE_INTERNAL_URL" if asset == "BTC" else "BINANCE_ETH_INTERNAL_URL"
        default_url = "http://binace:8001" if asset == "BTC" else "http://binace-eth:8001"
        base_url = (os.getenv(env_name) or default_url).rstrip("/")
        route = f"{base_url}/api/market/{asset.lower()}usd"
        response = await client.get(f"{route}/option-catalogue")
        response.raise_for_status()
        catalogue = response.json()
        if catalogue.get("underlying") != asset:
            raise ValueError("Watchlist asset mismatch")
        listed = catalogue.get("listedExpiries") or [o["expiry"] for o in catalogue.get("options") or []]
        expiries = {
            expiry
            for expiry in listed
            if datetime.fromisoformat(expiry.replace("Z", "+00:00")).astimezone(IST).date().isoformat() in dates[asset]
        }
        response = await client.post(
            f"{route}/watch-expiries", json={"expiries": sorted(expiries)}, headers={"X-Analysis-Secret": secret}
        )
        response.raise_for_status()

    results = await asyncio.gather(*(publish(asset) for asset in dates), return_exceptions=True)
    errors = [result for result in results if isinstance(result, Exception)]
    if errors:
        raise errors[0]
