from __future__ import annotations

import json
import logging
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import httpx
from agno.tools import Toolkit

from app.assets import DEFAULT_ASSET, Asset
from app.automation_schedule import (
    IST,
    fixed_session_during_minute,
    ist_day_bounds,
    next_fixed_run,
    normalize_run_time,
    parse_aware_datetime,
    previous_fixed_run,
    utc_text,
)
from app.capital import percentage_concurrency_limit
from app.errors import MARKET_AUTH_ERROR_CODE, MARKET_AUTH_ERROR_MESSAGE, AppError
from app.exit_schedule import ExitChoice, resolve_exit_schedule
from app.models import StrategyDefinition
from app.shared_analysis import SHARED_USER_ID
from news_agent.config import RECHECK_LEAD_SECONDS, NewsAgentSettings

from .assets import PROFILES
from .curated import dumps
from .local_client import LocalResearchClient
from .preview import build_preview, resolve_contracts
from .report_data import ResearchData

RECHECK_LEAD_TIME = timedelta(seconds=RECHECK_LEAD_SECONDS)
PROPOSAL_LIFETIME = timedelta(minutes=15)
logger = logging.getLogger(__name__)


class AutomationStrategyTools(Toolkit):
    """Tools that schedule saved strategies through the existing live strategy engine."""

    def __init__(
        self,
        settings: NewsAgentSettings,
        *,
        user_id: str,
        agent_run_id: str,
        market_snapshot_id: str,
        news_analysis_id: str | None = None,
        asset: Asset = DEFAULT_ASSET,
        curated: bool = False,
        option_context: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self.settings = settings
        self.asset = asset
        self.curated = curated
        self._previews: dict[tuple, tuple[dict, dict]] = {}
        self._initial_options = (option_context or {}).get("options") or []
        self.user_id = user_id if user_id == SHARED_USER_ID else str(UUID(user_id))
        self.agent_run_id = str(UUID(agent_run_id))
        self.market_snapshot_id = str(UUID(market_snapshot_id))
        self.news_analysis_id = news_analysis_id
        self.strategy_references: dict[str, tuple[str, int]] = {}
        self.application_data = LocalResearchClient(
            settings.trade_backend_internal_url, settings.analysis_service_secret
        )
        self.runtime_data = runtime_data(settings)
        super().__init__(
            name="automation_strategy_tools",
            tools=(
                [
                    self.preview_strategy,
                    self.select_strategy_and_time,
                    self.scheduled_next_agent_run,
                ]
                if curated
                else [
                    self.show_available_strategy,
                    self.calculate_exit_time,
                    self.select_strategy_and_time,
                    self.scheduled_next_agent_run,
                ]
            ),
            instructions=(
                "Use the starting strategy catalogue. Call preview_strategy before selecting with identical "
                "strategyRef, activation_time and exit_choice. Preview liquidity is advisory. "
                "Once an action commits, do not schedule another action."
                if curated
                else "Call show_available_strategy before selecting. "
                "Use its short strategyRef in select_strategy_and_time. "
                "A committed result confirms only the action named in the result. A shared proposal requires a later "
                "recheck and account allocation; no order is submitted inside the tool call. Retry rejected calls with "
                "corrected inputs. Once either action is committed, do not call another scheduling tool in this run."
            ),
            add_instructions=True,
            **kwargs,
        )
        if curated:
            self.functions["select_strategy_and_time"].description = (
                "Commit one previously previewed starting strategyRef and time. Use the identical "
                "activation_time and exit_choice from preview_strategy. No order is placed by this call."
            )

    def starting_catalogue(self) -> dict:
        source = json.loads(self.show_available_strategy())
        rows = []
        for strategy in source["strategies"]:
            definition = strategy.pop("definition")
            comparison = self.indicative_comparison(definition)
            rows.append(
                {
                    **strategy,
                    "comparison": comparison,
                    "rules": {
                        k: definition[k]
                        for k in (
                            "category",
                            "marketOutlook",
                            "riskMode",
                            "riskBasis",
                            "stopLossPercent",
                            "takeProfitPercent",
                            "sizePolicy",
                            "exitMinutesBeforeExpiry",
                        )
                        if k in definition
                    },
                    "legs": [
                        {
                            k: leg[k]
                            for k in (
                                "position",
                                "optionType",
                                "strikeMode",
                                "strikeSteps",
                                "exactStrike",
                                "lots",
                                "orderType",
                                "limitPrice",
                                "targetProfit",
                                "stopLoss",
                                "trailStop",
                            )
                            if k in leg
                        }
                        for leg in definition.get("legs", [])
                    ],
                }
            )
        return {
            **source,
            "strategies": rows,
            "holdingChoices": {
                "intradayHours": [7, 11],
                "overnightHours": [16, 24],
                "positionalHours": [48, 72],
                "other": ["specific_time", "expiry 1 or 2"],
            },
            "comparisonBasis": "Indicative normalized leg ratio at the first expiry covering the next hour; "
            "use this for ranking, then preview the chosen holding period once.",
        }

    def indicative_comparison(self, template: dict) -> dict:
        from functools import reduce
        from math import gcd

        from .preview import number

        try:
            now = datetime.now(UTC)
            definition, schedule = resolve_exit_schedule(
                template,
                entry_at=now + timedelta(minutes=8),
                choice=ExitChoice(kind="specific_time", exit_at=now + timedelta(hours=1)),
                options=self._initial_options,
            )
            legs = resolve_contracts(definition, self._initial_options)
            divisor = reduce(gcd, (leg["lots"] for leg in legs))
            quotes = {o["symbol"]: o for o in self._initial_options}
            credit, distances, spreads = 0.0, [], []
            for leg in legs:
                row = quotes[leg["productSymbol"]]
                bid, ask = number(row.get("bestBid")), number(row.get("bestAsk"))
                multiplier = number(row.get("contractValue"))
                if not 0 <= bid <= ask or multiplier <= 0:
                    raise ValueError("Unavailable indicative quote")
                price = ask if leg["position"] == "buy" else bid
                if price <= 0:
                    raise ValueError("Unavailable indicative price")
                credit += float(price * multiplier) * (leg["lots"] // divisor) * (-1 if leg["position"] == "buy" else 1)
                if ask + bid:
                    spreads.append(float((ask - bid) / ((ask + bid) / 2) * 100))
                if leg["position"] == "sell":
                    spot, strike = number(row.get("spot")), number(row["strike"])
                    distances.append(float((strike - spot) / spot * 100) * (1 if leg["optionType"] == "call" else -1))
            return {
                "expiry": schedule["contractExpiryUtc"],
                "netCreditUsd": credit,
                "nearestShortDistancePercent": min(distances) if distances else None,
                "maximumQuoteSpreadPercent": max(spreads) if spreads else None,
            }
        except (ValueError, AppError, KeyError, ZeroDivisionError):
            return {}

    def preview_strategy(self, strategy_ref: str, activation_time: str, exit_choice: ExitChoice) -> str:
        """Preview one saved strategy's exact schedule, legs, payoff and fresh advisory liquidity.

        Use the starting strategyRef. This reads public data and never schedules or places an order.
        The same strategy_ref, activation_time and exit_choice must be used when selecting.
        exit_choice uses kind, not type: {kind: intraday|overnight|positional, hours: allowed preset},
        {kind: specific_time, exit_at: aware ISO timestamp}, or {kind: expiry, expiry_number: 1|2}.
        """
        try:
            reference = strategy_ref.strip().upper()
            selection = self.strategy_references.get(reference)
            if selection is None:
                raise ValueError("Unknown starting strategy reference")
            activation = _aware_datetime(activation_time, "activation_time")
            choice = ExitChoice.model_validate(exit_choice)
            saved_id, version = selection
            key = (saved_id, version, activation.isoformat(), choice.model_dump_json())
            self._previews.pop(key, None)
            rows, _ = self.application_data.selection_context(self.user_id, saved_id)
            if not rows or not rows[0]["enabled_for_ai"] or rows[0]["version"] != version:
                raise ValueError("Saved strategy is unavailable or changed")
            profile = PROFILES[self.asset]
            route = f"{profile.market_base_url}/api/market/{profile.market_route}"
            with httpx.Client(timeout=httpx.Timeout(20, connect=3)) as client:
                response = client.get(f"{route}/option-catalogue")
                response.raise_for_status()
                catalogue = response.json()
                if catalogue.get("underlying") != self.asset:
                    raise ValueError("Option asset mismatch")
                options = catalogue.get("options") or []
                definition, schedule = resolve_exit_schedule(
                    rows[0]["definition_json"], entry_at=activation, choice=choice, options=options
                )
                resolved = resolve_contracts(definition, options)
                response = client.post(
                    f"{route}/selected-contracts", json={"symbols": [leg["productSymbol"] for leg in resolved]}
                )
                response.raise_for_status()
                selected = response.json()
                if selected.get("asset") != self.asset:
                    raise ValueError("Selected option asset mismatch")
                result = build_preview(
                    definition, resolved, selected.get("contracts") or [], int(datetime.now(UTC).timestamp() * 1000)
                )
                if self.settings.analysis_service_secret:
                    watch = client.post(
                        f"{route}/watch-expiries",
                        json={"expiries": [schedule["contractExpiryUtc"]]},
                        headers={"X-Analysis-Secret": self.settings.analysis_service_secret},
                    )
                    watch.raise_for_status()
            self._previews[key] = (definition, schedule)
            return dumps({"valid": True, "schedule": schedule, **result})
        except httpx.HTTPError as error:
            status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
            path = error.request.url.path if error.request is not None else "unknown"
            logger.warning(
                "Strategy preview service failed run_id=%s asset=%s path=%s status=%s",
                self.agent_run_id, self.asset, path, status,
            )
            authentication_failed = status in {401, 403}
            return json.dumps({
                "valid": False,
                "errorCode": (
                    MARKET_AUTH_ERROR_CODE if authentication_failed else "market_service_unavailable"
                ),
                "reason": (
                    MARKET_AUTH_ERROR_MESSAGE
                    if authentication_failed else "Selected public option evidence is unavailable"
                ),
            })
        except (ValueError, AppError) as error:
            return json.dumps(
                {
                    "valid": False,
                    "reason": str(error),
                }
            )

    def show_available_strategy(self) -> str:
        """Return enabled strategies with descriptions, run-local references, and complete definitions."""
        rows, capital_settings = self.application_data.selection_context(self.user_id)
        context = self.runtime_data.request_sync(
            "runtimeAutomation:context", {"userId": self.user_id, "runId": self.agent_run_id}
        )
        maximum = percentage_concurrency_limit(str(capital_settings["allocation_mode"]))
        occupied = context["occupied"]
        visible = sorted(
            (
                row
                for row in rows
                if row["enabled_for_ai"]
                and (self.user_id != SHARED_USER_ID or row.get("user_id") is None)
                # Each agent lists only strategies written for its own underlying.
                and ((row.get("definition_json") or {}).get("instrument") or {}).get("underlying") == self.asset
            ),
            key=lambda row: (str(row["name"]).casefold(), str(row["id"])),
        )
        self.strategy_references = {
            f"S{index:02d}": (str(row["id"]), int(row["version"])) for index, row in enumerate(visible, start=1)
        }
        return json.dumps(
            {
                "strategies": [
                    {
                        "strategyRef": f"S{index:02d}",
                        "version": row["version"],
                        "name": row["name"],
                        "description": row["definition_json"].get("description", ""),
                        "definition": row["definition_json"],
                        "currentAvailability": "unavailable"
                        if self.user_id != SHARED_USER_ID and maximum and occupied >= maximum
                        else "available_for_live_schedule",
                        "reasonUnavailable": ["Every account capital allocation is occupied"]
                        if self.user_id != SHARED_USER_ID and maximum and occupied >= maximum
                        else None,
                    }
                    for index, row in enumerate(visible, start=1)
                ],
                "activeSlotCount": occupied,
                "maximumSlots": maximum or "calculated_at_entry",
            }
        )

    def _committed_action(self) -> dict[str, Any] | None:
        state = read_automation_state(self.settings, user_id=self.user_id, agent_run_id=self.agent_run_id)
        outcome = state.get("outcome") if state else None
        if not outcome:
            return None
        selected = outcome == "strategy_selected"
        follow_up = outcome == "wait_and_run_again"
        message = (
            "A shared strategy proposal was already recorded in this agent run. Do not schedule another action."
            if selected and self.user_id == SHARED_USER_ID
            else "A strategy was already scheduled in this agent run. Do not schedule another action."
            if selected
            else "A follow-up agent run was already scheduled or reused. Do not schedule another action."
            if follow_up
            else "This agent run already committed an action. Do not schedule another action."
        )
        return {
            "success": True,
            "status": "already_committed",
            "outcome": outcome,
            "newActionCreated": False,
            "strategyScheduled": selected and self.user_id != SHARED_USER_ID,
            "sharedProposalRecorded": selected and self.user_id == SHARED_USER_ID,
            "accountSchedulingPending": selected and self.user_id == SHARED_USER_ID,
            "agentRunScheduled": follow_up,
            "canRetry": False,
            "message": message,
        }

    def _failed_action(self, error: Exception, *, action: str) -> dict[str, Any]:
        try:
            committed = self._committed_action()
        except Exception:
            logger.exception("Could not verify automation tool outcome run_id=%s", self.agent_run_id)
            committed = None
            verified = False
        else:
            verified = True
        if committed:
            return committed
        if not verified:
            logger.exception("Automation %s tool outcome is unknown run_id=%s", action, self.agent_run_id)
            return {
                "success": False,
                "status": "unconfirmed",
                "outcome": None,
                "strategyScheduled": None,
                "sharedProposalRecorded": None,
                "agentRunScheduled": None,
                "canRetry": True,
                "errorCode": "commit_unconfirmed",
                "message": (
                    "The action could not be confirmed. Retry this tool; the run guard will prevent a duplicate."
                ),
            }
        if isinstance(error, ValueError):
            return {
                "success": False,
                "status": "rejected",
                "outcome": None,
                "strategyScheduled": False,
                "sharedProposalRecorded": False,
                "agentRunScheduled": False,
                "canRetry": True,
                "errorCode": "invalid_tool_input",
                "message": str(error),
            }
        logger.exception("Automation %s tool failed run_id=%s", action, self.agent_run_id, exc_info=error)
        return {
            "success": False,
            "status": "rejected",
            "outcome": None,
            "strategyScheduled": False,
            "sharedProposalRecorded": False,
            "agentRunScheduled": False,
            "canRetry": True,
            "errorCode": error.code if isinstance(error, AppError) else "tool_failed",
            "message": (
                f"{error.message} No action was committed. Retry if the setup remains valid."
                if isinstance(error, AppError)
                else "The action was not committed. Retry with corrected inputs or record no trade."
            ),
        }

    def select_strategy_and_time(
        self,
        strategy_ref: str,
        activation_time: str,
        ai_confidence: float,
        reasoning_summary: str,
        supporting_signals: list[str],
        invalidation_signals: list[str],
        exit_choice: dict[str, Any],
    ) -> str:
        """Schedule the strategyRef returned by show_available_strategy.

        First call calculate_exit_time with the same entry time, strategyRef and exit_choice.
        exit_choice is {kind: intraday|overnight|positional, hours: allowed preset},
        {kind: specific_time, exit_at: aware ISO timestamp}, or
        {kind: expiry, expiry_number: 1|2}. The server resolves a listed contract
        covering the chosen exit. Risk, legs and sizing remain owned by the saved strategy.
        Returns JSON confirming a scheduled account strategy or a shared proposal.
        Rejected calls may be corrected and retried. A committed run cannot schedule again.
        """
        try:
            committed = self._committed_action()
            if committed:
                return json.dumps(committed)
            reference = strategy_ref.strip().upper()
            selection = self.strategy_references.get(reference)
            if selection is None:
                raise ValueError(
                    "Unknown strategyRef. Call show_available_strategy and use one of its short references."
                )
            saved_id, saved_strategy_version = selection
            result = json.loads(
                self._select_strategy_and_time(
                    saved_id=saved_id,
                    saved_strategy_version=saved_strategy_version,
                    activation_time=activation_time,
                    ai_confidence=ai_confidence,
                    reasoning_summary=reasoning_summary,
                    supporting_signals=supporting_signals,
                    invalidation_signals=invalidation_signals,
                    exit_choice=exit_choice,
                )
            )
            shared = self.user_id == SHARED_USER_ID
            return json.dumps(
                {
                    **result,
                    "success": True,
                    "status": "committed",
                    "newActionCreated": True,
                    "strategyScheduled": not shared,
                    "sharedProposalRecorded": shared,
                    "agentRunScheduled": False,
                    "activationRecheckScheduled": True,
                    "accountSchedulingPending": shared,
                    "canRetry": False,
                    "message": (
                        "Shared proposal and recheck recorded. Account strategies are scheduled "
                        "only after recheck and allocation."
                        if shared
                        else "Account strategy and activation recheck scheduled. No order has been placed yet."
                    ),
                }
            )
        except Exception as error:
            return json.dumps(self._failed_action(error, action="strategy scheduling"))

    def _select_strategy_and_time(
        self,
        *,
        saved_id: str,
        saved_strategy_version: int,
        activation_time: str,
        ai_confidence: float,
        reasoning_summary: str,
        supporting_signals: list[str],
        invalidation_signals: list[str],
        exit_choice: dict[str, Any],
    ) -> str:
        activation = _future_datetime(activation_time, "activation_time")
        expiry = activation + PROPOSAL_LIFETIME
        recheck_at = activation - RECHECK_LEAD_TIME
        if recheck_at <= datetime.now(UTC):
            raise ValueError("activation_time must leave more than seven minutes for the activation recheck")
        fixed_session = fixed_session_during_minute(activation)
        if fixed_session:
            raise ValueError(
                f"activation_time cannot be during the fixed {fixed_session.trigger.replace('_', ' ')} review at "
                f"{utc_text(fixed_session.scheduled_for)}"
            )
        if not 0 <= ai_confidence <= 1:
            raise ValueError("ai_confidence must be between 0 and 1")
        if not reasoning_summary.strip():
            raise ValueError("reasoning_summary is required")

        choice = ExitChoice.model_validate(exit_choice)
        if self.curated:
            key = (saved_id, saved_strategy_version, activation.isoformat(), choice.model_dump_json())
            if key not in self._previews:
                raise ValueError("Call preview_strategy with these exact selection arguments first")
            rows, _ = self.application_data.selection_context(self.user_id, saved_id)
            if not rows or not rows[0]["enabled_for_ai"] or rows[0]["version"] != saved_strategy_version:
                raise ValueError("Saved strategy is unavailable or changed")
            context = self.runtime_data.request_sync(
                "runtimeAutomation:context", {"userId": self.user_id, "runId": self.agent_run_id}
            )
            if not context.get("snapshot") or context["snapshot"]["id"] != self.market_snapshot_id:
                raise ValueError("Current market snapshot is unavailable")
            live_definition, schedule = deepcopy(self._previews[key])
        else:
            live_definition, schedule = self._resolve_selection(saved_id, saved_strategy_version, activation, choice)
        exit_at = datetime.fromisoformat(schedule["exitUtc"])
        if self.user_id == SHARED_USER_ID:
            return json.dumps(
                {
                    **self.runtime_data.request_sync(
                        "sharedAnalysis:publish",
                        {
                            "runId": self.agent_run_id,
                            "candidates": [{"id": saved_id, "version": saved_strategy_version}],
                            "activation": activation.isoformat(),
                            "expiry": expiry.isoformat(),
                            "exit": exit_at.isoformat(),
                            "definitionJson": json.dumps(live_definition),
                            "confidence": ai_confidence,
                            "reasoning": reasoning_summary.strip(),
                            "supporting": supporting_signals,
                            "invalidation": invalidation_signals,
                            "snapshotId": self.market_snapshot_id,
                        },
                        mutation=True,
                    ),
                    "schedule": schedule,
                }
            )
        return json.dumps(
            {
                **self.runtime_data.request_sync(
                    "runtimeAutomation:schedule",
                    {
                        "userId": self.user_id,
                        "runId": self.agent_run_id,
                        "savedId": saved_id,
                        "savedVersion": saved_strategy_version,
                        "activation": activation.isoformat(),
                        "expiry": expiry.isoformat(),
                        "exit": exit_at.isoformat(),
                        "recheck": recheck_at.isoformat(),
                        "definitionJson": json.dumps(live_definition),
                        "confidence": ai_confidence,
                        "reasoning": reasoning_summary.strip(),
                        "supporting": supporting_signals,
                        "invalidation": invalidation_signals,
                        "snapshotId": self.market_snapshot_id,
                        "newsId": self.news_analysis_id,
                    },
                    mutation=True,
                ),
                "schedule": schedule,
            }
        )

    def _resolve_selection(
        self, saved_id: str, expected_version: int, activation: datetime, choice: ExitChoice
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        rows, _ = self.application_data.selection_context(self.user_id, saved_id)
        if not rows or not rows[0]["enabled_for_ai"]:
            raise ValueError("Selected strategy is unavailable")
        strategy = rows[0]
        if strategy["version"] != expected_version:
            raise ValueError("The saved strategy version changed")
        context = self.runtime_data.request_sync(
            "runtimeAutomation:context", {"userId": self.user_id, "runId": self.agent_run_id}
        )
        snapshot = context.get("snapshot")
        if not snapshot or snapshot["id"] != self.market_snapshot_id:
            raise ValueError("Current market snapshot is unavailable")
        market = snapshot["market_json"]
        option_context = market.get("executionOptionContext") or market.get("deltaOptionContext") or {}
        if option_context.get("underlying") != self.asset:
            raise ValueError("Option chain does not match the selected asset")
        return resolve_exit_schedule(
            strategy["definition_json"],
            entry_at=activation,
            choice=choice,
            options=option_context.get("options") or [],
        )

    def calculate_exit_time(self, strategy_ref: str, activation_time: str, exit_choice: dict[str, Any]) -> str:
        """Preview the exact holding duration, option expiry and safety cutoff without scheduling."""
        try:
            selection = self.strategy_references.get(strategy_ref.strip().upper())
            if selection is None:
                raise ValueError("Unknown strategyRef. Call show_available_strategy first")
            _, schedule = self._resolve_selection(
                selection[0],
                selection[1],
                _aware_datetime(activation_time, "activation_time"),
                ExitChoice.model_validate(exit_choice),
            )
            return json.dumps({"valid": True, "schedule": schedule})
        except (ValueError, AppError) as error:
            return json.dumps({"valid": False, "reason": str(error)})

    def scheduled_next_agent_run(
        self,
        next_run_time: str,
        reason_for_waiting: str,
        signals_to_inspect: list[str],
    ) -> str:
        """Schedule one follow-up or reuse a pending review; return confirmed JSON status."""
        try:
            committed = self._committed_action()
            if committed:
                return json.dumps(committed)
            result = json.loads(self._schedule_next_agent_run(next_run_time, reason_for_waiting, signals_to_inspect))
            return json.dumps(
                {
                    **result,
                    "success": True,
                    "status": "committed",
                    "agentRunScheduled": True,
                    "strategyScheduled": False,
                    "sharedProposalRecorded": False,
                    "newActionCreated": not bool(
                        result.get("reusedExistingRun") or result.get("rescheduledExistingFollowUp")
                    ),
                    "canRetry": False,
                    "message": (
                        "A future agent review is scheduled or an existing review was reused. No trade was scheduled."
                    ),
                }
            )
        except Exception as error:
            return json.dumps(self._failed_action(error, action="follow-up scheduling"))

    def _schedule_next_agent_run(
        self,
        next_run_time: str,
        reason_for_waiting: str,
        signals_to_inspect: list[str],
    ) -> str:
        now = datetime.now(UTC)
        next_run = normalize_run_time(next_run_time, "next_run_time", now=now)
        reason = reason_for_waiting.strip()
        signals = [signal.strip() for signal in signals_to_inspect if signal.strip()][:10]
        if not reason:
            raise ValueError("reason_for_waiting is required")
        day_start, day_end = ist_day_bounds(now)
        return json.dumps(
            self.runtime_data.request_sync(
                "runtimeAutomation:followup",
                {
                    "userId": self.user_id,
                    "runId": self.agent_run_id,
                    "next": next_run.isoformat(),
                    "reason": reason,
                    "signals": signals,
                    "fixed": next_fixed_run(now).scheduled_for.isoformat(),
                    "previous": previous_fixed_run(now).scheduled_for.isoformat(),
                    "dayStart": day_start.isoformat(),
                    "dayEnd": day_end.isoformat(),
                    "snapshotId": self.market_snapshot_id,
                    "newsId": self.news_analysis_id,
                },
                mutation=True,
            )
        )


class DropStrategyTools(Toolkit):
    """Allow a recheck agent to cancel only the proposal assigned to its run."""

    def __init__(
        self,
        settings: NewsAgentSettings,
        *,
        user_id: str,
        agent_run_id: str,
        proposal_id: str,
        **kwargs: Any,
    ) -> None:
        self.user_id = user_id if user_id == SHARED_USER_ID else str(UUID(user_id))
        self.agent_run_id = str(UUID(agent_run_id))
        self.proposal_id = str(UUID(proposal_id))
        self.runtime_data = runtime_data(settings)
        super().__init__(
            name="activation_recheck_tools",
            tools=[self.drop_strategy],
            instructions="drop_strategy can cancel only the scheduled strategy bound to this activation recheck.",
            add_instructions=True,
            **kwargs,
        )

    def drop_strategy(self, strategy_name: str, activation_time: str, reason: str) -> str:
        """Cancel the supplied scheduled strategy because its original market setup is no longer valid."""
        reason = reason.strip()
        if not reason:
            raise ValueError("reason is required")
        requested_activation = _aware_datetime(activation_time, "activation_time")
        return json.dumps(
            self.runtime_data.request_sync(
                "runtimeAutomation:recheck",
                {
                    "userId": self.user_id,
                    "runId": self.agent_run_id,
                    "proposalId": self.proposal_id,
                    "drop": True,
                    "name": strategy_name,
                    "activation": requested_activation.isoformat(),
                    "reason": reason,
                },
                mutation=True,
            )
        )


def snapshot_json(value: Any) -> str:
    """Preserve timestamp meaning without silently stringifying unsupported values."""

    def encode(item: Any) -> str:
        if isinstance(item, datetime):
            if item.tzinfo is None or item.utcoffset() is None:
                raise ValueError("Snapshot timestamps must include a timezone")
            return item.isoformat()
        raise TypeError(f"Unsupported snapshot value: {type(item).__name__}")

    return json.dumps(value, default=encode, allow_nan=False)


def save_market_snapshot(
    settings: NewsAgentSettings,
    *,
    user_id: str,
    agent_run_id: str,
    market_packet: dict[str, Any],
    account_context: dict[str, Any],
) -> str:
    data = runtime_data(settings)
    return data.request_sync(
        "runtimeAutomation:saveSnapshot",
        {
            "userId": user_id,
            "runId": agent_run_id,
            "marketJson": snapshot_json(market_packet),
            "accountJson": snapshot_json(account_context),
        },
        mutation=True,
    )


def read_parent_run_context(settings: NewsAgentSettings, *, user_id: str, agent_run_id: str) -> dict[str, Any] | None:
    """Read only the scheduling parent's report, never unrelated recent decisions."""
    data = runtime_data(settings)
    parent = data.request_sync("runtimeAutomation:context", {"userId": user_id, "runId": agent_run_id})["parent"]
    return (
        {
            "runId": parent["id"],
            "scheduledFor": parent["scheduled_for"],
            "startedAt": parent.get("started_at"),
            "completedAt": parent.get("completed_at"),
            "trigger": parent["trigger"],
            "outcome": parent.get("outcome"),
            "finalResponse": parent.get("report_markdown"),
        }
        if parent
        else None
    )


def read_automation_state(settings: NewsAgentSettings, *, user_id: str, agent_run_id: str) -> dict[str, Any] | None:
    data = LocalResearchClient(settings.trade_backend_internal_url, settings.analysis_service_secret)
    row = data.request_sync("runtimeAutomation:context", {"userId": user_id, "runId": agent_run_id})["run"]
    return {"outcome": row.get("outcome"), "market_snapshot_id": row.get("market_snapshot_id")}


def confirm_activation_recheck(
    settings: NewsAgentSettings,
    *,
    user_id: str,
    agent_run_id: str,
    proposal_id: str,
) -> str:
    """Record the no-tool recheck outcome without reopening or changing the strategy."""
    data = runtime_data(settings)
    return data.request_sync(
        "runtimeAutomation:recheck",
        {
            "userId": user_id,
            "runId": agent_run_id,
            "proposalId": proposal_id,
            "drop": False,
        },
        mutation=True,
    )["outcome"]


def runtime_data(settings: NewsAgentSettings) -> ResearchData:
    client = LocalResearchClient(settings.trade_backend_internal_url, settings.analysis_service_secret)
    return ResearchData(client, settings.require_database_url)


def _future_datetime(value: str, field: str) -> datetime:
    return parse_aware_datetime(value, field)


def _aware_datetime(value: str, field: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ValueError(f"{field} must be a timezone-aware ISO-8601 timestamp") from error
    if parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(UTC)


def materialize_live_definition(
    definition: dict[str, Any],
    *,
    activation: datetime,
    option_context: dict[str, Any],
    holding_policy: Literal["saved", "intraday", "overnight", "positional", "hold_to_expiry"] = "saved",
    planned_exit_time: str | None = None,
    expiry_policy: Literal["same_day", "next_day", "7_day", "30_day"] | None = None,
) -> tuple[dict[str, Any], datetime]:
    """Resolve a holding decision without changing the saved definition, risk or leg structure."""
    if holding_policy not in {"saved", "intraday", "overnight", "positional", "hold_to_expiry"}:
        raise ValueError("Unsupported holding policy")
    timed = holding_policy in {"intraday", "overnight", "positional"}
    if timed != (planned_exit_time is not None):
        raise ValueError("A planned exit time is required only for an explicit timed holding policy")
    if activation.utcoffset() is None:
        raise ValueError("Activation must include a timezone")
    live = deepcopy(definition)
    live.pop("selectionCriteria", None)
    if expiry_policy is not None:
        if expiry_policy not in {"same_day", "next_day", "7_day", "30_day"}:
            raise ValueError("Unsupported expiry policy")
        live["expiryPolicy"] = expiry_policy
    expiry = resolve_option_expiry(
        policy=str(live.get("expiryPolicy") or "same_day"),
        activation=activation,
        options=option_context.get("options") or [],
    )
    expiry_date = expiry.astimezone(IST).date().isoformat()
    for leg in live.get("legs") or []:
        leg["expiry"] = expiry_date

    entry = live.get("entry") if isinstance(live.get("entry"), dict) else {}
    if holding_policy != "saved":
        live["holdingMode"] = "hold_to_expiry" if holding_policy == "hold_to_expiry" else "intraday"
        entry["strategyType"] = {"overnight": "btst", "positional": "positional"}.get(holding_policy, "intraday")
    try:
        old_entry = datetime.fromisoformat(str(entry.get("entryAt")).replace("Z", "+00:00"))
        old_exit = datetime.fromisoformat(str(entry.get("exitAt")).replace("Z", "+00:00"))
        duration = max(timedelta(minutes=1), old_exit - old_entry)
    except (TypeError, ValueError):
        duration = timedelta(hours=7)

    expiry_exit = expiry - timedelta(minutes=int(live.get("exitMinutesBeforeExpiry") or 5))
    if timed:
        exit_at = _aware_datetime(planned_exit_time, "planned_exit_time")
        if exit_at > expiry_exit:
            raise ValueError("Planned exit exceeds the selected contract expiry safety buffer")
        entry_day = activation.astimezone(IST).date()
        exit_day = exit_at.astimezone(IST).date()
        if holding_policy == "intraday" and exit_day != entry_day:
            raise ValueError("Intraday exit must be on the entry date in Asia/Kolkata")
        if holding_policy == "overnight" and exit_day != entry_day + timedelta(days=1):
            raise ValueError("Overnight exit must be on the following date in Asia/Kolkata")
    elif str(live.get("holdingMode")) == "hold_to_expiry":
        exit_at = expiry_exit
    else:
        exit_at = min(activation + duration, expiry_exit)
    if exit_at <= activation:
        raise ValueError("The resolved option expiry does not leave enough time to run this strategy")
    if holding_policy == "hold_to_expiry":
        days_held = (exit_at.astimezone(IST).date() - activation.astimezone(IST).date()).days
        entry["strategyType"] = "intraday" if days_held == 0 else "btst" if days_held == 1 else "positional"

    entry["entryAt"] = activation.isoformat()
    entry["exitAt"] = exit_at.isoformat()
    live["entry"] = entry
    live["acknowledgement"] = True
    validated = StrategyDefinition.model_validate(live)
    return validated.model_dump(mode="json", exclude_none=True), exit_at


def resolve_option_expiry(
    *,
    policy: str,
    activation: datetime,
    options: list[dict[str, Any]],
) -> datetime:
    expiries = sorted(
        {
            parsed
            for option in options
            if isinstance(option, dict) and (parsed := _parse_expiry(option.get("expiry"))) is not None
        }
    )
    if not expiries:
        raise ValueError("Delta returned no live option expiries for this underlying")

    local_date = activation.astimezone(IST).date()
    if policy == "same_day":
        candidates = [expiry for expiry in expiries if expiry.astimezone(IST).date() == local_date]
    elif policy == "next_day":
        candidates = [expiry for expiry in expiries if expiry.astimezone(IST).date() > local_date]
    else:
        days = 7 if policy == "7_day" else 30 if policy == "30_day" else None
        if days is None:
            raise ValueError(f"Unsupported expiry policy: {policy}")
        target = local_date + timedelta(days=days)
        candidates = [expiry for expiry in expiries if expiry.astimezone(IST).date() >= target]
    if not candidates:
        raise ValueError(f"No listed Delta expiry satisfies the {policy} policy")
    return candidates[0]


def _parse_expiry(value: Any) -> datetime | None:
    if not value:
        return None
    normalized = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
