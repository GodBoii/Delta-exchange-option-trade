from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
from agno.tools import Toolkit
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.capital import percentage_concurrency_limit
from app.models import StrategyDefinition
from news_agent.config import NewsAgentSettings

IST = ZoneInfo("Asia/Kolkata")


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
            option_context = (snapshot["market_json"] or {}).get("deltaOptionContext") or {}
            live_definition, exit_at = materialize_live_definition(
                strategy["definition_json"],
                activation=activation,
                option_context=option_context,
            )
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
                "update public.automation_agent_runs set outcome = 'strategy_selected' where id = %s and user_id = %s",
                (self.agent_run_id, self.user_id),
            )
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
        """Schedule a future analysis run after validating minimum delay, deduplication, and the daily limit."""
        next_run = _future_datetime(next_run_time, "next_run_time")
        if not reason_for_waiting.strip():
            raise ValueError("reason_for_waiting is required")

        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                select enabled, minimum_follow_up_minutes, maximum_agent_runs_per_day
                from public.automation_settings where user_id = %s
                """,
                (self.user_id,),
            )
            settings = cursor.fetchone() or {"minimum_follow_up_minutes": 5, "maximum_agent_runs_per_day": 12}
            if not settings.get("enabled"):
                raise ValueError("Automation is turned off")
            minimum = datetime.now(UTC) + timedelta(minutes=int(settings["minimum_follow_up_minutes"]))
            if next_run < minimum:
                raise ValueError(
                    f"next_run_time must be at least {settings['minimum_follow_up_minutes']} minutes ahead"
                )

            local_day = next_run.astimezone(IST).date()
            day_start = datetime.combine(local_day, datetime.min.time(), tzinfo=IST).astimezone(UTC)
            day_end = day_start + timedelta(days=1)
            cursor.execute(
                """
                select count(*)::int as count from public.automation_agent_runs
                where user_id = %s and scheduled_for >= %s and scheduled_for < %s
                  and status <> 'cancelled'
                """,
                (self.user_id, day_start, day_end),
            )
            if int(cursor.fetchone()["count"]) >= int(settings["maximum_agent_runs_per_day"]):
                raise ValueError("The daily agent-run limit has been reached")

            digest = hashlib.sha256(f"{next_run.isoformat()}|{reason_for_waiting.strip()}".encode()).hexdigest()[:20]
            run_key = f"follow-up:{digest}"
            cursor.execute(
                """
                insert into public.automation_agent_runs (
                  user_id, run_key, trigger, status, scheduled_for, reason,
                  signals_to_inspect, market_snapshot_id, news_analysis_id
                ) values (%s,%s,'agent_follow_up','scheduled',%s,%s,%s,%s,%s)
                on conflict (user_id, run_key) do update set reason = excluded.reason
                returning id::text
                """,
                (
                    self.user_id,
                    run_key,
                    next_run,
                    reason_for_waiting.strip(),
                    Jsonb(signals_to_inspect),
                    self.market_snapshot_id,
                    self.news_analysis_id,
                ),
            )
            scheduled_run_id = cursor.fetchone()["id"]
            cursor.execute(
                "update public.automation_agent_runs set outcome = 'wait_and_run_again' where id = %s and user_id = %s",
                (self.agent_run_id, self.user_id),
            )
            connection.commit()
        return json.dumps(
            {
                "outcome": "wait_and_run_again",
                "scheduledRunId": scheduled_run_id,
                "nextRunTime": next_run.isoformat(),
            }
        )

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.database_url, row_factory=dict_row)


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


def _psycopg_url(url: str) -> str:
    normalized = url.replace("postgresql+psycopg://", "postgresql://", 1)
    parts = urlsplit(normalized)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, parts.fragment))


def _future_datetime(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO-8601 datetime") from error
    if parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    parsed = parsed.astimezone(UTC)
    if parsed <= datetime.now(UTC):
        raise ValueError(f"{field} must be in the future")
    return parsed


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
