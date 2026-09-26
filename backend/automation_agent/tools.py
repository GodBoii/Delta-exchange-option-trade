from __future__ import annotations

import json
import logging
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from agno.tools import Toolkit

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
from app.errors import AppError
from app.models import StrategyDefinition
from app.shared_analysis import SHARED_USER_ID
from news_agent.config import RECHECK_LEAD_SECONDS, NewsAgentSettings

from .local_client import LocalResearchClient
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
        **kwargs: Any,
    ) -> None:
        self.settings = settings
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
            tools=[
                self.show_available_strategy,
                self.select_strategy_and_time,
                self.scheduled_next_agent_run,
            ],
            instructions=(
                "Call show_available_strategy before selecting. Use its short strategyRef in select_strategy_and_time. "
                "A committed result confirms only the action named in the result. A shared proposal requires a later "
                "recheck and account allocation; no order is submitted inside the tool call. Retry rejected calls with "
                "corrected inputs. Once either action is committed, do not call another scheduling tool in this run."
            ),
            add_instructions=True,
            **kwargs,
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
                row for row in rows
                if row["enabled_for_ai"] and (self.user_id != SHARED_USER_ID or row.get("user_id") is None)
            ),
            key=lambda row: (str(row["name"]).casefold(), str(row["id"])),
        )
        self.strategy_references = {
            f"S{index:02d}": (str(row["id"]), int(row["version"]))
            for index, row in enumerate(visible, start=1)
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
                "success": False, "status": "unconfirmed", "outcome": None,
                "strategyScheduled": None, "sharedProposalRecorded": None,
                "agentRunScheduled": None,
                "canRetry": True, "errorCode": "commit_unconfirmed",
                "message": (
                    "The action could not be confirmed. Retry this tool; "
                    "the run guard will prevent a duplicate."
                ),
            }
        if isinstance(error, ValueError):
            return {
                "success": False, "status": "rejected", "outcome": None,
                "strategyScheduled": False, "sharedProposalRecorded": False,
                "agentRunScheduled": False,
                "canRetry": True, "errorCode": "invalid_tool_input", "message": str(error),
            }
        logger.exception("Automation %s tool failed run_id=%s", action, self.agent_run_id, exc_info=error)
        return {
            "success": False, "status": "rejected", "outcome": None,
            "strategyScheduled": False, "sharedProposalRecorded": False,
            "agentRunScheduled": False,
            "canRetry": True, "errorCode": error.code if isinstance(error, AppError) else "tool_failed",
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
        holding_policy: Literal["saved", "intraday", "overnight", "positional", "hold_to_expiry"] = "saved",
        planned_exit_time: str | None = None,
        expiry_policy: Literal["same_day", "next_day", "7_day", "30_day"] | None = None,
    ) -> str:
        """Schedule the strategyRef returned by show_available_strategy.

        holding_policy: saved preserves the template; intraday, overnight and positional
        require planned_exit_time as an aware ISO timestamp. hold_to_expiry exits before
        expiry using the saved safety buffer. Stops and profit targets remain active.
        expiry_policy: optionally choose a listed expiry horizon. Risk, legs and sizing
        remain owned by the saved strategy. Explain the holding choice in reasoning_summary.
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
                    holding_policy=holding_policy,
                    planned_exit_time=planned_exit_time,
                    expiry_policy=expiry_policy,
                )
            )
            shared = self.user_id == SHARED_USER_ID
            return json.dumps({
                **result,
                "success": True, "status": "committed", "newActionCreated": True,
                "strategyScheduled": not shared, "sharedProposalRecorded": shared,
                "agentRunScheduled": False,
                "activationRecheckScheduled": True, "accountSchedulingPending": shared,
                "canRetry": False,
                "message": (
                    "Shared proposal and recheck recorded. Account strategies are scheduled "
                    "only after recheck and allocation."
                    if shared else "Account strategy and activation recheck scheduled. No order has been placed yet."
                ),
            })
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
        holding_policy: Literal["saved", "intraday", "overnight", "positional", "hold_to_expiry"],
        planned_exit_time: str | None,
        expiry_policy: Literal["same_day", "next_day", "7_day", "30_day"] | None,
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

        external = self.application_data.selection_context(self.user_id, saved_id)
        if not external or not external[0] or not external[0][0]["enabled_for_ai"]:
            raise ValueError("Selected strategy is unavailable")
        strategy = external[0][0]
        if strategy["version"] != saved_strategy_version:
            raise ValueError("The saved strategy version changed")
        context = self.runtime_data.request_sync(
            "runtimeAutomation:context", {"userId": self.user_id, "runId": self.agent_run_id}
        )
        snapshot = context["snapshot"]
        if not snapshot or snapshot["id"] != self.market_snapshot_id:
            raise ValueError("Current market snapshot is unavailable")
        market = snapshot["market_json"]
        live_definition, exit_at = materialize_live_definition(
            strategy["definition_json"],
            activation=activation,
            option_context=market.get("executionOptionContext") or market.get("deltaOptionContext") or {},
            holding_policy=holding_policy,
            planned_exit_time=planned_exit_time,
            expiry_policy=expiry_policy,
        )
        if self.user_id == SHARED_USER_ID:
            return json.dumps(
                self.runtime_data.request_sync(
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
                )
            )
        return json.dumps(
            self.runtime_data.request_sync(
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
            )
        )

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
            result = json.loads(
                self._schedule_next_agent_run(next_run_time, reason_for_waiting, signals_to_inspect)
            )
            return json.dumps({
                **result,
                "success": True, "status": "committed", "agentRunScheduled": True,
                "strategyScheduled": False, "sharedProposalRecorded": False,
                "newActionCreated": not bool(
                    result.get("reusedExistingRun") or result.get("rescheduledExistingFollowUp")
                ),
                "canRetry": False,
                "message": (
                    "A future agent review is scheduled or an existing review was reused. "
                    "No trade was scheduled."
                ),
            })
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
        raise ValueError("Delta returned no live BTC option expiries")

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
