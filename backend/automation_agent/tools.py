from __future__ import annotations

import json
import logging
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import httpx
import psycopg
from agno.tools import Toolkit
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.application_data import ConvexApplicationData, response_value
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
        self.database_url = _psycopg_url(settings.require_database_url())
        self.user_id = user_id if user_id == SHARED_USER_ID else str(UUID(user_id))
        self.agent_run_id = str(UUID(agent_run_id))
        self.market_snapshot_id = str(UUID(market_snapshot_id))
        self.news_analysis_id = news_analysis_id
        self.strategy_references: dict[str, tuple[str, int]] = {}
        self.application_data = (
            ConvexApplicationData(settings.convex_url, settings.convex_trading_secret)
            if settings.convex_library_enabled
            else None
        )
        self.account_data = (
            ConvexApplicationData(settings.convex_url, settings.convex_trading_secret)
            if getattr(settings, "convex_accounts_enabled", False)
            else None
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
        """Return enabled strategies with short run-local references and complete definitions."""
        external = self.application_data.selection_context(self.user_id) if self.application_data else None
        if self.runtime_data is not None:
            if external is None:
                raise ValueError("Runtime migration requires the Convex library")
            rows, capital_settings = external
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
        with self._connect() as connection, connection.cursor() as cursor:
            if external is None:
                cursor.execute(
                    """
                select id::text, name, version, definition_json, created_at, updated_at
                from public.saved_strategies
                where (user_id = %s or user_id is null) and enabled_for_ai = true
                order by name, updated_at desc
                """,
                    (self.user_id,),
                )
                strategies = cursor.fetchall()
                cursor.execute(
                    "select allocation_mode from public.capital_settings where user_id = %s",
                    (self.user_id,),
                )
                capital_settings = cursor.fetchone() or {"allocation_mode": "half_balance"}
            else:
                rows, capital_settings = external
                strategies = [row for row in rows if row["enabled_for_ai"]]
            maximum_slots = percentage_concurrency_limit(str(capital_settings["allocation_mode"]))
            cursor.execute(
                """
                select count(*)::int as count
                from public.strategy_capital_slots
                where user_id = %s and status in ('reserved','active')
                """,
                (self.user_id,),
            )
            active_count = int(cursor.fetchone()["count"])
        visible = sorted(strategies, key=lambda row: (str(row["name"]).casefold(), str(row["id"])))
        self.strategy_references = {
            f"S{index:02d}": (str(row["id"]), int(row["version"]))
            for index, row in enumerate(visible, start=1)
        }
        result = []
        for index, row in enumerate(visible, start=1):
            definition = row["definition_json"]
            reasons: list[str] = []
            if maximum_slots is not None and active_count >= maximum_slots:
                reasons.append("Every account capital allocation is occupied")
            result.append(
                {
                    "strategyRef": f"S{index:02d}",
                    "version": row["version"],
                    "name": row["name"],
                    "definition": definition,
                    "currentAvailability": "unavailable" if reasons else "available_for_live_schedule",
                    "reasonUnavailable": reasons or None,
                }
            )
        return json.dumps(
            {
                "strategies": result,
                "activeSlotCount": active_count,
                "maximumSlots": maximum_slots or "calculated_at_entry",
            },
            default=str,
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

        external = self.application_data.selection_context(self.user_id, saved_id) if self.application_data else None
        if self.runtime_data is not None:
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
        account_connected = None
        if self.account_data is not None:
            with httpx.Client(timeout=15) as client:
                overview = response_value(
                    client.post(
                        f"{self.account_data.url}/api/query",
                        json=self.account_data.body("accounts:overview", {"userId": self.user_id}),
                    )
                )
            account_connected = bool(overview["connection"] and overview["connection"]["status"] == "connected")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select enabled from public.automation_settings where user_id = %s for share",
                (self.user_id,),
            )
            automation = cursor.fetchone()
            if not automation or not automation["enabled"]:
                raise ValueError("Automation is turned off")
            if external is None:
                cursor.execute(
                    "select allocation_mode from public.capital_settings where user_id = %s",
                    (self.user_id,),
                )
                capital_settings = cursor.fetchone() or {"allocation_mode": "half_balance"}
            else:
                _, capital_settings = external
            maximum_slots = percentage_concurrency_limit(str(capital_settings["allocation_mode"]))
            if account_connected is None:
                cursor.execute(
                    "select 1 from public.exchange_connections where user_id = %s and status = 'connected' limit 1",
                    (self.user_id,),
                )
                account_connected = bool(cursor.fetchone())
            if not account_connected:
                raise ValueError("A connected Delta account is required for live scheduling")
            if external is None:
                cursor.execute(
                    """
                select id::text, name, version, definition_json
                from public.saved_strategies
                where id = %s and (user_id = %s or user_id is null) and enabled_for_ai = true
                for share
                """,
                    (saved_id, self.user_id),
                )
                strategy = cursor.fetchone()
            else:
                external_rows, _ = external
                strategy = next((row for row in external_rows if row["enabled_for_ai"]), None)
            if not strategy:
                raise ValueError("The selected strategy is missing, disabled, or belongs to another user")
            if int(strategy["version"]) != saved_strategy_version:
                raise ValueError("The saved strategy version changed; inspect available strategies again")
            cursor.execute(
                """
                select count(*)::int as count
                from public.strategy_capital_slots
                where user_id = %s and status in ('reserved','active')
                """,
                (self.user_id,),
            )
            occupied = int(cursor.fetchone()["count"])
            if maximum_slots is not None and occupied >= maximum_slots:
                raise ValueError("No account capital allocation is currently available")

            cursor.execute(
                "select market_json from public.automation_market_snapshots where id = %s and user_id = %s",
                (self.market_snapshot_id, self.user_id),
            )
            snapshot = cursor.fetchone()
            if not snapshot:
                raise ValueError("The current market snapshot is unavailable")
            market_json = snapshot["market_json"] or {}
            option_context = market_json.get("executionOptionContext") or market_json.get("deltaOptionContext") or {}
            live_definition, exit_at = materialize_live_definition(
                strategy["definition_json"],
                activation=activation,
                option_context=option_context,
                holding_policy=holding_policy,
                planned_exit_time=planned_exit_time,
                expiry_policy=expiry_policy,
            )
            self._claim_terminal_outcome(cursor, "strategy_selected")
            cursor.execute(
                """
                insert into public.strategies (
                  user_id, saved_strategy_id, name, status, definition_json, entry_at, exit_at
                ) values (%s,%s,%s,'scheduled',%s,%s,%s)
                returning id::text
                """,
                (
                    self.user_id,
                    saved_id,
                    strategy["name"],
                    Jsonb(live_definition),
                    activation,
                    exit_at,
                ),
            )
            scheduled_strategy_id = cursor.fetchone()["id"]

            cursor.execute(
                """
                insert into public.strategy_proposals (
                  user_id, agent_run_id, strategy_id, saved_strategy_id, saved_strategy_version, status,
                  activation_time, proposal_expiry, ai_confidence, reasoning_summary,
                  supporting_signals, invalidation_signals, market_snapshot_id, news_analysis_id
                ) values (%s,%s,%s,%s,%s,'scheduled',%s,%s,%s,%s,%s,%s,%s,%s)
                returning id::text
                """,
                (
                    self.user_id,
                    self.agent_run_id,
                    scheduled_strategy_id,
                    saved_id,
                    saved_strategy_version,
                    activation,
                    expiry,
                    ai_confidence,
                    reasoning_summary.strip(),
                    Jsonb(supporting_signals),
                    Jsonb(invalidation_signals),
                    self.market_snapshot_id,
                    self.news_analysis_id,
                ),
            )
            proposal_id = cursor.fetchone()["id"]
            cursor.execute(
                """
                insert into public.automation_agent_runs (
                  user_id, run_key, trigger, status, scheduled_for, reason, strategy_proposal_id
                ) values (%s,%s,'activation_recheck','scheduled',%s,%s,%s)
                returning id::text
                """,
                (
                    self.user_id,
                    f"activation-recheck:{proposal_id}",
                    recheck_at,
                    f"Recheck {strategy['name']} before its scheduled activation",
                    proposal_id,
                ),
            )
            recheck_run_id = cursor.fetchone()["id"]
            connection.commit()

        return json.dumps(
            {
                "outcome": "strategy_selected",
                "proposalId": proposal_id,
                "strategy": strategy["name"],
                "strategyVersion": saved_strategy_version,
                "activationTime": activation.isoformat(),
                "proposalExpiry": expiry.isoformat(),
                "scheduledStrategyId": scheduled_strategy_id,
                "activationRecheckRunId": recheck_run_id,
                "activationRecheckTime": recheck_at.isoformat(),
                "exitTime": exit_at.isoformat(),
                "execution": "live_strategy_scheduler",
            }
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
        if self.runtime_data is not None:
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

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("select pg_advisory_xact_lock(hashtextextended(%s, 43))", (self.user_id,))
            cursor.execute(
                """
                select runs.trigger, runs.outcome, settings.enabled,
                       settings.minimum_follow_up_minutes, settings.maximum_agent_runs_per_day
                from public.automation_agent_runs runs
                join public.automation_settings settings on settings.user_id = runs.user_id
                where runs.id = %s and runs.user_id = %s
                for update
                """,
                (self.agent_run_id, self.user_id),
            )
            current = cursor.fetchone()
            if not current:
                raise ValueError("The current automation run is unavailable")
            if not current["enabled"]:
                raise ValueError("Automation is turned off")
            if current["trigger"] == "agent_follow_up":
                raise ValueError(
                    "A follow-up run cannot schedule another follow-up; select a strategy or record no trade"
                )

            minimum = now + timedelta(minutes=int(current["minimum_follow_up_minutes"]))
            if next_run < minimum:
                raise ValueError(f"next_run_time must be at least {current['minimum_follow_up_minutes']} minutes ahead")

            cursor.execute(
                """
                select id::text, trigger, scheduled_for
                from public.automation_agent_runs
                where user_id = %s and id <> %s and status = 'scheduled' and scheduled_for > %s
                  and trigger <> 'activation_recheck'
                order by scheduled_for
                limit 1
                """,
                (self.user_id, self.agent_run_id, now),
            )
            existing = cursor.fetchone()
            if existing and existing["scheduled_for"] <= next_run:
                self._claim_terminal_outcome(cursor, "wait_and_run_again")
                cursor.execute(
                    """
                    update public.automation_agent_runs
                    set parent_agent_run_id = %s
                    where id = %s and user_id = %s and status = 'scheduled' and parent_agent_run_id is null
                    """,
                    (self.agent_run_id, existing["id"], self.user_id),
                )
                connection.commit()
                return json.dumps(
                    {
                        "outcome": "wait_and_run_again",
                        "scheduledRunId": existing["id"],
                        "nextRunTime": utc_text(existing["scheduled_for"]),
                        "trigger": existing["trigger"],
                        "reusedExistingRun": True,
                    }
                )

            fixed = next_fixed_run(now)
            if next_run >= fixed.scheduled_for:
                raise ValueError(
                    f"A fixed {fixed.trigger.replace('_', ' ')} review already runs at "
                    f"{utc_text(fixed.scheduled_for)}; do not schedule another run at or after it"
                )

            previous_fixed = previous_fixed_run(now)
            cursor.execute(
                """
                select count(*)::int as count
                from public.automation_agent_runs
                where user_id = %s
                  and trigger = 'agent_follow_up'
                  and status <> 'cancelled'
                  and scheduled_for > %s
                  and scheduled_for < %s
                """,
                (self.user_id, previous_fixed.scheduled_for, fixed.scheduled_for),
            )
            interval_follow_up_count = int(cursor.fetchone()["count"])

            cursor.execute(
                """
                select id::text
                from public.automation_agent_runs
                where user_id = %s and status = 'scheduled' and trigger = 'agent_follow_up'
                order by scheduled_for
                limit 1
                for update
                """,
                (self.user_id,),
            )
            pending_follow_up = cursor.fetchone()
            if pending_follow_up:
                run_key = f"follow-up:{next_run.strftime('%Y-%m-%dT%H:%MZ')}"
                self._claim_terminal_outcome(cursor, "wait_and_run_again")
                cursor.execute(
                    """
                    update public.automation_agent_runs
                    set run_key = %s, scheduled_for = %s, reason = %s, signals_to_inspect = %s,
                        market_snapshot_id = %s, news_analysis_id = %s, parent_agent_run_id = %s
                    where id = %s
                    """,
                    (
                        run_key,
                        next_run,
                        reason,
                        Jsonb(signals),
                        self.market_snapshot_id,
                        self.news_analysis_id,
                        self.agent_run_id,
                        pending_follow_up["id"],
                    ),
                )
                connection.commit()
                return json.dumps(
                    {
                        "outcome": "wait_and_run_again",
                        "scheduledRunId": pending_follow_up["id"],
                        "nextRunTime": utc_text(next_run),
                        "rescheduledExistingFollowUp": True,
                    }
                )

            if interval_follow_up_count:
                raise ValueError(
                    "A follow-up has already been used between the previous and next fixed session reviews"
                )

            day_start, day_end = ist_day_bounds(next_run)
            cursor.execute(
                """
                select count(*)::int as count from public.automation_agent_runs
                where user_id = %s and scheduled_for >= %s and scheduled_for < %s
                  and trigger = 'agent_follow_up' and status <> 'cancelled'
                """,
                (self.user_id, day_start, day_end),
            )
            if int(cursor.fetchone()["count"]) >= int(current["maximum_agent_runs_per_day"]):
                raise ValueError("The daily follow-up limit has been reached")

            run_key = f"follow-up:{next_run.strftime('%Y-%m-%dT%H:%MZ')}"
            self._claim_terminal_outcome(cursor, "wait_and_run_again")
            cursor.execute(
                """
                insert into public.automation_agent_runs (
                  user_id, run_key, trigger, status, scheduled_for, reason,
                  signals_to_inspect, market_snapshot_id, news_analysis_id, parent_agent_run_id
                ) values (%s,%s,'agent_follow_up','scheduled',%s,%s,%s,%s,%s,%s)
                on conflict (user_id, run_key) do update
                  set status = 'scheduled',
                      scheduled_for = excluded.scheduled_for,
                      completed_at = null,
                      error = null,
                      reason = excluded.reason,
                      signals_to_inspect = excluded.signals_to_inspect,
                      market_snapshot_id = excluded.market_snapshot_id,
                      news_analysis_id = excluded.news_analysis_id,
                      parent_agent_run_id = excluded.parent_agent_run_id
                  where public.automation_agent_runs.status = 'cancelled'
                returning id::text
                """,
                (
                    self.user_id,
                    run_key,
                    next_run,
                    reason,
                    Jsonb(signals),
                    self.market_snapshot_id,
                    self.news_analysis_id,
                    self.agent_run_id,
                ),
            )
            scheduled = cursor.fetchone()
            if not scheduled:
                raise ValueError("A run already used this exact UTC minute")
            scheduled_run_id = scheduled["id"]
            connection.commit()
        return json.dumps(
            {
                "outcome": "wait_and_run_again",
                "scheduledRunId": scheduled_run_id,
                "nextRunTime": utc_text(next_run),
            }
        )

    def _claim_terminal_outcome(self, cursor: psycopg.Cursor, outcome: str) -> None:
        cursor.execute(
            """
            update public.automation_agent_runs
            set outcome = %s
            where id = %s and user_id = %s and status = 'running' and outcome is null
            returning id
            """,
            (outcome, self.agent_run_id, self.user_id),
        )
        if not cursor.fetchone():
            raise ValueError("This automation run has already chosen its terminal action")

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.database_url, row_factory=dict_row)


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
        self.database_url = _psycopg_url(settings.require_database_url())
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
        if self.runtime_data is not None:
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
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                select proposals.status as proposal_status, proposals.activation_time,
                       strategies.id::text as strategy_id, strategies.name, strategies.status as strategy_status
                from public.strategy_proposals proposals
                join public.strategies strategies on strategies.id = proposals.strategy_id
                where proposals.id = %s and proposals.user_id = %s
                for update of proposals, strategies
                """,
                (self.proposal_id, self.user_id),
            )
            assigned = cursor.fetchone()
            if not assigned:
                raise ValueError("The strategy assigned to this recheck is unavailable")
            if assigned["name"].casefold() != strategy_name.strip().casefold():
                raise ValueError("strategy_name does not match the strategy assigned to this recheck")
            if requested_activation != assigned["activation_time"].astimezone(UTC):
                raise ValueError("activation_time does not match the strategy assigned to this recheck")
            if assigned["proposal_status"] != "scheduled" or assigned["strategy_status"] != "scheduled":
                raise ValueError("The assigned strategy is no longer awaiting activation")
            cursor.execute(
                """
                update public.automation_agent_runs
                set outcome = 'strategy_dropped'
                where id = %s and user_id = %s and status = 'running' and outcome is null
                  and trigger = 'activation_recheck' and strategy_proposal_id = %s
                returning id
                """,
                (self.agent_run_id, self.user_id, self.proposal_id),
            )
            if not cursor.fetchone():
                raise ValueError("This activation recheck has already chosen its outcome")
            message = f"Dropped by activation recheck: {reason}"
            cursor.execute(
                "update public.strategies set status = 'cancelled', last_error = %s where id = %s",
                (message, assigned["strategy_id"]),
            )
            cursor.execute(
                "update public.strategy_proposals set status = 'cancelled', rejection_reason = %s where id = %s",
                (message, self.proposal_id),
            )
            connection.commit()
        return json.dumps(
            {
                "outcome": "strategy_dropped",
                "strategy": assigned["name"],
                "activationTime": requested_activation.isoformat(),
                "reason": reason,
            }
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
    if data is not None:
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
    with psycopg.connect(_psycopg_url(settings.require_database_url()), row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                insert into public.automation_market_snapshots (user_id, market_json, account_json)
                values (%s,%s,%s) returning id::text
                """,
                (
                    str(UUID(user_id)),
                    Jsonb(market_packet, dumps=snapshot_json),
                    Jsonb(account_context, dumps=snapshot_json),
                ),
            )
            snapshot_id = cursor.fetchone()["id"]
            cursor.execute(
                """
                update public.automation_agent_runs
                set market_snapshot_id = %s
                where id = %s and user_id = %s
                """,
                (snapshot_id, str(UUID(agent_run_id)), str(UUID(user_id))),
            )
        connection.commit()
    return snapshot_id


def read_parent_run_context(settings: NewsAgentSettings, *, user_id: str, agent_run_id: str) -> dict[str, Any] | None:
    """Read only the scheduling parent's report, never unrelated recent decisions."""
    data = runtime_data(settings)
    if data is not None:
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
    with psycopg.connect(_psycopg_url(settings.require_database_url()), row_factory=dict_row) as connection:
        row = connection.execute(
            """
            select parent.id::text as "runId", parent.scheduled_for as "scheduledFor",
                   parent.started_at as "startedAt", parent.completed_at as "completedAt",
                   parent.trigger, parent.outcome, parent.report_markdown as "finalResponse"
            from public.automation_agent_runs child
            join public.automation_agent_runs parent on parent.id = child.parent_agent_run_id
              and parent.user_id = child.user_id
            where child.id = %s and child.user_id = %s
            """,
            (str(UUID(agent_run_id)), str(UUID(user_id))),
        ).fetchone()
    return dict(row) if row else None


def read_automation_state(settings: NewsAgentSettings, *, user_id: str, agent_run_id: str) -> dict[str, Any] | None:
    if getattr(settings, "convex_runtime_enabled", False):
        data = ConvexApplicationData(settings.convex_url, settings.convex_trading_secret)
        row = data.request_sync("runtimeAutomation:context", {"userId": user_id, "runId": agent_run_id})["run"]
        return {"outcome": row.get("outcome"), "market_snapshot_id": row.get("market_snapshot_id")}
    with psycopg.connect(_psycopg_url(settings.require_database_url()), row_factory=dict_row) as connection:
        row = connection.execute(
            "select outcome, market_snapshot_id::text from public.automation_agent_runs where id = %s and user_id = %s",
            (str(UUID(agent_run_id)), str(UUID(user_id))),
        ).fetchone()
    return dict(row) if row else None


def confirm_activation_recheck(
    settings: NewsAgentSettings,
    *,
    user_id: str,
    agent_run_id: str,
    proposal_id: str,
) -> str:
    """Record the no-tool recheck outcome without reopening or changing the strategy."""
    data = runtime_data(settings)
    if data is not None:
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
    with psycopg.connect(_psycopg_url(settings.require_database_url()), row_factory=dict_row) as connection:
        row = connection.execute(
            """
            update public.automation_agent_runs runs
            set outcome = case
              when proposals.status = 'scheduled' and strategies.status = 'scheduled'
                then 'strategy_reconfirmed'
              else 'strategy_dropped'
            end
            from public.strategy_proposals proposals
            left join public.strategies strategies on strategies.id = proposals.strategy_id
            where runs.id = %s and runs.user_id = %s and runs.status = 'running' and runs.outcome is null
              and runs.strategy_proposal_id = proposals.id and proposals.id = %s
            returning runs.outcome
            """,
            (str(UUID(agent_run_id)), str(UUID(user_id)), str(UUID(proposal_id))),
        ).fetchone()
        if not row:
            row = connection.execute(
                "select outcome from public.automation_agent_runs where id = %s and user_id = %s",
                (str(UUID(agent_run_id)), str(UUID(user_id))),
            ).fetchone()
        connection.commit()
    if not row or not row["outcome"]:
        raise RuntimeError("The activation recheck outcome could not be recorded")
    return str(row["outcome"])


def runtime_data(settings: NewsAgentSettings) -> ConvexApplicationData | None:
    return (
        ResearchData(settings.convex_url, settings.convex_trading_secret, settings.require_database_url())
        if getattr(settings, "convex_runtime_enabled", False)
        else None
    )


def _psycopg_url(url: str) -> str:
    normalized = url.replace("postgresql+psycopg://", "postgresql://", 1)
    parts = urlsplit(normalized)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, parts.fragment))


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
