"""Publish only aggregate expiry dates to public market collectors, never account data."""

import asyncio
from datetime import datetime

import httpx

from app.automation_schedule import IST
from automation_agent.assets import PROFILES


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
    dates = {asset: set() for asset in PROFILES}
    for row in rows:
        definition = row.get("definition_json") or {}
        asset = (definition.get("instrument") or {}).get("underlying")
        if asset in dates:
            dates[asset].update(leg["expiry"] for leg in definition.get("legs", []) if leg.get("expiry"))

    async def publish(asset: str) -> None:
        if not dates[asset]:
            return
        profile = PROFILES[asset]
        route = f"{profile.market_base_url}/api/market/{profile.market_route}"
        response = await client.get(f"{route}/option-catalogue")
        response.raise_for_status()
        catalogue = response.json()
        if catalogue.get("underlying") != asset:
            raise ValueError("Watchlist asset mismatch")
        expiries = {
            o["expiry"]
            for o in catalogue.get("options") or []
            if datetime.fromisoformat(o["expiry"].replace("Z", "+00:00")).astimezone(IST).date().isoformat()
            in dates[asset]
        }
        response = await client.post(
            f"{route}/watch-expiries", json={"expiries": sorted(expiries)}, headers={"X-Analysis-Secret": secret}
        )
        response.raise_for_status()

    results = await asyncio.gather(*(publish(asset) for asset in dates), return_exceptions=True)
    errors = [result for result in results if isinstance(result, Exception)]
    if errors:
        raise errors[0]
