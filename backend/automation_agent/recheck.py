"""Read-only option evidence for the single strategy assigned to an activation recheck."""

import logging
import math
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import httpx
from agno.tools import Toolkit

from app.automation_schedule import IST
from app.errors import AppError

from .assets import AssetProfile
from .curated import dumps
from .preview import build_preview, resolve_contracts

logger = logging.getLogger(__name__)


class RecheckOptionTools(Toolkit):
    def __init__(self, *, asset: AssetProfile, selected_strategy: dict[str, Any]) -> None:
        self.asset = asset
        self._strategy = deepcopy(selected_strategy)
        self.evidence_valid = False
        self.latest_evidence: dict[str, Any] = {}
        super().__init__(
            name="assigned_option_evidence",
            tools=[self.preview_selected_strategy],
            instructions="Option evidence is bound to the assigned strategy. This tool never changes or schedules it.",
            add_instructions=True,
        )

    def collect_evidence(self) -> dict[str, Any]:
        """Refresh selected contracts and reject missing, stale or mismatched evidence."""
        self.evidence_valid = False
        definition = self._strategy["definition"]
        route = f"{self.asset.market_base_url}/api/market/{self.asset.market_route}"
        with httpx.Client(timeout=httpx.Timeout(20, connect=3)) as client:
            response = client.get(f"{route}/option-catalogue")
            response.raise_for_status()
            catalogue = response.json()
            if catalogue.get("underlying") != self.asset.code:
                raise ValueError("Recheck option asset mismatch")
            resolved = resolve_contracts(definition, catalogue.get("options") or [])
            response = client.post(
                f"{route}/selected-contracts", json={"symbols": [leg["productSymbol"] for leg in resolved]},
            )
            response.raise_for_status()
            selected = response.json()
        if selected.get("asset") != self.asset.code:
            raise ValueError("Selected option asset mismatch")
        now = datetime.now(UTC)
        quotes = selected.get("contracts") or []
        expiries = {leg["productSymbol"]: leg["expiry"] for leg in resolved}
        for quote in quotes:
            if quote.get("symbol") in expiries:
                expiry = datetime.fromisoformat(str(quote["expiry"]).replace("Z", "+00:00"))
                if expiry.utcoffset() is None or expiry <= now:
                    raise ValueError("Assigned option expiry is unavailable or expired")
                if expiry.astimezone(IST).date().isoformat() != expiries[quote["symbol"]]:
                    raise ValueError("Selected option expiry mismatch")
        result = build_preview(definition, resolved, quotes, int(now.timestamp() * 1000))
        entry = datetime.fromisoformat(str(self._strategy["activationTime"]).replace("Z", "+00:00"))
        exit_at = datetime.fromisoformat(str(
            self._strategy.get("exitTime") or definition["entry"]["exitAt"]
        ).replace("Z", "+00:00"))
        if entry.utcoffset() is None or exit_at.utcoffset() is None or exit_at <= entry:
            raise ValueError("Assigned strategy schedule is invalid")
        hours = (exit_at - entry).total_seconds() / 3600
        result["selectedLegMoveScales"] = {
            "hours": hours,
            "basis": "Selected-leg IV times sqrt(planned holding hours / 8760); not a forecast or profit threshold.",
            "legs": [
                {
                    "symbol": leg["symbol"], "expiry": expiries[leg["symbol"]], "ivPercent": leg["ivPercent"],
                    "impliedScaledMovePercent": leg["ivPercent"] * math.sqrt(hours / 8760)
                    if leg["ivPercent"] is not None else None,
                }
                for leg in result["legs"]
            ],
        }
        self.latest_evidence = {
            "valid": True, "asset": self.asset.code, "asOf": int(now.timestamp() * 1000),
            "strategyName": self._strategy["name"], "activationTime": self._strategy["activationTime"],
            "exitTime": exit_at.isoformat(), **result,
        }
        self.evidence_valid = True
        return deepcopy(self.latest_evidence)

    def preview_selected_strategy(self) -> str:
        """Refresh the assigned strategy's premiums, IV, Greeks, payoff, costs and advisory liquidity.

        No arguments: the strategy, asset, entry and exit are fixed by the recheck assignment.
        Returns valid=false if evidence cannot be read. No order or scheduling action is performed.
        """
        try:
            return dumps(self.collect_evidence())
        except httpx.HTTPError:
            self.evidence_valid = False
            logger.warning("Assigned option service unavailable asset=%s", self.asset.code, exc_info=True)
            return dumps({"valid": False, "reason": "Fresh assigned option evidence is unavailable"})
        except (ValueError, KeyError, TypeError, AppError) as error:
            self.evidence_valid = False
            logger.warning("Assigned option evidence invalid asset=%s error=%s", self.asset.code, error)
            return dumps({"valid": False, "reason": "Assigned option evidence is incomplete or invalid"})
