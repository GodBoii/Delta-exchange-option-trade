from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import psycopg
from agno.tools import Toolkit
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

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
from app.models import StrategyDefinition
from news_agent.config import NewsAgentSettings

RECHECK_LEAD_TIME = timedelta(minutes=5)


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
        self.database_url = _psycopg_url(settings.require_database_url())
        self.user_id = str(UUID(user_id))
        self.agent_run_id = str(UUID(agent_run_id))
        self.market_snapshot_id = str(UUID(market_snapshot_id))
        self.news_analysis_id = news_analysis_id
        super().__init__(
            name="automation_strategy_tools",
            tools=[
                self.show_available_strategy,
                self.select_strategy_and_time,
                self.scheduled_next_agent_run,
            ],
            instructions=(
                "Inspect available strategies before selecting one. select_strategy_and_time creates a live scheduled "
                "strategy that the existing scheduler executes at the requested time. It never submits an order inside "
                "the tool call."
            ),
            add_instructions=True,
            **kwargs,
        )

    def show_available_strategy(self) -> str:
        """Return every enabled saved strategy, its immutable version, complete definition, and current availability."""
        with self._connect() as connection, connection.cursor() as cursor:
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
        result = []
        for row in strategies:
            definition = row["definition_json"]
            reasons: list[str] = []
            if maximum_slots is not None and active_count >= maximum_slots:
                reasons.append("Every account capital allocation is occupied")
            result.append(
                {
                    "id": row["id"],
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

    def select_strategy_and_time(
        self,
        saved_strategy_id: str,
        saved_strategy_version: int,
        activation_time: str,
        proposal_expiry: str,
        ai_confidence: float,
        reasoning_summary: str,
        supporting_signals: list[str],
        invalidation_signals: list[str],
    ) -> str:
        """Schedule one enabled saved strategy for live execution at a future time."""
        saved_id = str(UUID(saved_strategy_id))
        activation = _future_datetime(activation_time, "activation_time")
        expiry = _future_datetime(proposal_expiry, "proposal_expiry")
        recheck_at = activation - RECHECK_LEAD_TIME
        if recheck_at <= datetime.now(UTC):
            raise ValueError("activation_time must leave at least five minutes for the activation recheck")
        fixed_session = fixed_session_during_minute(activation)
        if fixed_session:
            raise ValueError(
                f"activation_time cannot be during the fixed {fixed_session.trigger.replace('_', ' ')} review at "
                f"{utc_text(fixed_session.scheduled_for)}"
            )
        if expiry <= activation:
            raise ValueError("proposal_expiry must be after activation_time")
        if not 0 <= ai_confidence <= 1:
            raise ValueError("ai_confidence must be between 0 and 1")
        if not reasoning_summary.strip():
            raise ValueError("reasoning_summary is required")

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "select enabled from public.automation_settings where user_id = %s for share",
                (self.user_id,),
            )
            automation = cursor.fetchone()
            if not automation or not automation["enabled"]:
                raise ValueError("Automation is turned off")
            cursor.execute(
                "select allocation_mode from public.capital_settings where user_id = %s",
                (self.user_id,),
            )
            capital_settings = cursor.fetchone() or {"allocation_mode": "half_balance"}
            maximum_slots = percentage_concurrency_limit(str(capital_settings["allocation_mode"]))
            cursor.execute(
                "select 1 from public.exchange_connections where user_id = %s and status = 'connected' limit 1",
                (self.user_id,),
            )
            if not cursor.fetchone():
                raise ValueError("A connected Delta account is required for live scheduling")
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
        """Schedule one follow-up before the next fixed review, or reuse an earlier pending review."""
        now = datetime.now(UTC)
        next_run = normalize_run_time(next_run_time, "next_run_time", now=now)
        reason = reason_for_waiting.strip()
        signals = [signal.strip() for signal in signals_to_inspect if signal.strip()][:10]
        if not reason:
            raise ValueError("reason_for_waiting is required")

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
                raise ValueError(
                    f"next_run_time must be at least {current['minimum_follow_up_minutes']} minutes ahead"
                )

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
        self.user_id = str(UUID(user_id))
        self.agent_run_id = str(UUID(agent_run_id))
        self.proposal_id = str(UUID(proposal_id))
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


def save_market_snapshot(
    settings: NewsAgentSettings,
    *,
    user_id: str,
    agent_run_id: str,
    market_packet: dict[str, Any],
    account_context: dict[str, Any],
) -> str:
    with psycopg.connect(_psycopg_url(settings.require_database_url()), row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                insert into public.automation_market_snapshots (user_id, market_json, account_json)
                values (%s,%s,%s) returning id::text
                """,
                (str(UUID(user_id)), Jsonb(market_packet), Jsonb(account_context)),
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
) -> tuple[dict[str, Any], datetime]:
    """Resolve the saved expiry policy and schedule without changing strategy-owned risk or legs."""
    live = deepcopy(definition)
    live.pop("selectionCriteria", None)
    expiry = resolve_option_expiry(
        policy=str(live.get("expiryPolicy") or "same_day"),
        activation=activation,
        options=option_context.get("options") or [],
    )
    expiry_date = expiry.astimezone(IST).date().isoformat()
    for leg in live.get("legs") or []:
        leg["expiry"] = expiry_date

    entry = live.get("entry") if isinstance(live.get("entry"), dict) else {}
    try:
        old_entry = datetime.fromisoformat(str(entry.get("entryAt")).replace("Z", "+00:00"))
        old_exit = datetime.fromisoformat(str(entry.get("exitAt")).replace("Z", "+00:00"))
        duration = max(timedelta(minutes=1), old_exit - old_entry)
    except (TypeError, ValueError):
        duration = timedelta(hours=7)

    expiry_exit = expiry - timedelta(minutes=int(live.get("exitMinutesBeforeExpiry") or 5))
    if str(live.get("holdingMode")) == "hold_to_expiry":
        exit_at = expiry_exit
    else:
        exit_at = min(activation + duration, expiry_exit)
    if exit_at <= activation:
        raise ValueError("The resolved option expiry does not leave enough time to run this strategy")

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
