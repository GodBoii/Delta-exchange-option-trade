"""Read-only analysis commitments. Funded reservations remain owned by entry execution."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .assets import automation_asset_enabled, run_asset, strategy_asset
from .capital import fits_capital_limits, policy_budget, policy_from_row
from .errors import AppError
from .exit_schedule import session_expiry

Admission = Literal["ready", "full", "pending"]


def instant(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise AppError(503, "Capacity schedule lacks a timezone", "capital_capacity_unavailable")
    return parsed.astimezone(UTC)


def proposal_key(row: dict[str, Any]) -> tuple[bool, datetime, str]:
    return run_asset(row) != "BTC", instant(row["activation_time"]), str(row["id"])


async def read_snapshot(pool: AsyncConnectionPool, args: dict[str, Any]) -> dict[str, Any]:
    """Use live runtime state, never owner reporting or private model context."""
    owner = str(args["p_user_id"])
    request = args["p_run"]
    asset = run_asset(request)
    now = datetime.now(UTC)
    earliest = now - timedelta(seconds=int(args["p_entry_lateness_seconds"]))
    async with pool.connection() as connection, connection.cursor(row_factory=dict_row) as cursor:
        # One consistent database snapshot, including the handoff from job to proposal to slot.
        await cursor.execute("set transaction isolation level repeatable read, read only")
        await cursor.execute("select connection,capital,automation from trade.users where user_id=%s", (owner,))
        account = await cursor.fetchone()
        if not account or not account["connection"] or account["connection"].get("status") != "connected":
            return {"eligible": False}
        wallet = account["connection"].get("delta_user_id")
        if not wallet:
            raise AppError(503, "Exchange account identity is missing", "capital_capacity_unavailable")
        if not automation_asset_enabled(account["automation"], asset):
            return {"eligible": False}
        await cursor.execute("select analysis from trade.system_settings where key='main'")
        config = await cursor.fetchone()
        global_settings = config["analysis"] if config and args.get("p_shared", True) else {"enabled": True}
        if not automation_asset_enabled(global_settings, asset):
            return {"eligible": False}
        await cursor.execute(
            "select user_id,capital,automation,connection from trade.users where connection->>'delta_user_id'=%s",
            (str(wallet),),
        )
        aliases = {row["user_id"]: row for row in await cursor.fetchall()}
        await cursor.execute(
            """select data from trade.strategy_capital_slots
               where owner_id=any(%s) and status in ('reserved','active')""", (list(aliases),),
        )
        slots = [row["data"] for row in await cursor.fetchall()]
        await cursor.execute(
            """select data from trade.strategies where owner_id=any(%s)
               and ((status='scheduled' and entry_at >= %s and exit_at > %s)
                    or id=any(%s))""",
            (list(aliases), earliest, now, [row.get("strategy_id") for row in slots]),
        )
        strategies = [row["data"] for row in await cursor.fetchall()]
        await cursor.execute(
            """select p.data,r.status as recheck_status,r.data->>'outcome' as recheck_outcome
               from trade.strategy_proposals p
               left join trade.analysis_jobs r on r.id=p.data->>'shared_recheck_run_id'
               where p.owner_id='global' and p.status='scheduled' and p.activation_time >= %s""",
            (earliest,),
        )
        shared = await cursor.fetchall()
        await cursor.execute(
            """select data from trade.analysis_jobs where (owner_id=any(%s) or owner_id='global')
               and (status='running' or (status='scheduled' and scheduled_for between %s and %s))""",
            (list(aliases), now - timedelta(minutes=10), now),
        )
        jobs = [row["data"] for row in await cursor.fetchall()]
        # Account proposals also bind manual/private rechecks and reject failed approvals.
        await cursor.execute(
            """select p.data,r.status as recheck_status,r.data->>'outcome' as recheck_outcome
               from trade.strategy_proposals p left join trade.analysis_jobs r
                 on r.relation_id=coalesce(p.data->>'shared_decision_id',p.id)
                 and r.data->>'trigger'='activation_recheck'
               where p.owner_id=any(%s) and p.status='scheduled'""", (list(aliases),),
        )
        private = await cursor.fetchall()

    current = next((row for row in jobs if row["id"] == request.get("id")), None)
    assigned_id = request.get("strategy_proposal_id")
    proposals = {row["data"]["id"]: row["data"] for row in [*shared, *private]}
    assigned = proposals.get(assigned_id)
    if assigned_id and assigned is None:
        return {"eligible": False}
    if assigned:
        start = max(now, instant(assigned["activation_time"]))
        strategy = next((row for row in strategies if row["id"] == assigned.get("strategy_id")), None)
        end = instant(assigned.get("exit_at") or (strategy or {}).get("exit_at"))
    else:
        reference = request.get("scheduled_for") or (current or {}).get("scheduled_for") or now.isoformat()
        start, end = now, session_expiry(instant(reference))
    if end <= start:
        return {"eligible": False}
    reserved_ids = {row.get("strategy_id") for row in slots}
    invalid = {
        row["data"]["id"] for row in [*shared, *private]
        if row["recheck_status"] in {"failed", "cancelled"} or row["recheck_outcome"] == "strategy_dropped"
    }
    invalid_strategies = {row["data"].get("strategy_id") for row in private if row["data"]["id"] in invalid}
    invalid_decisions = {row["data"]["shared_decision_id"] for row in private
                         if row["data"]["id"] in invalid and row["data"].get("shared_decision_id")}
    private_by_strategy = {row["data"].get("strategy_id"): row for row in private}
    funded_decisions = {(row["user_id"], row.get("shared_decision_id")) for row in strategies
                        if row["id"] in reserved_ids}
    commitments: dict[tuple[str, str], dict[str, Any]] = {}

    def add(user_id: str, key: str, begin: datetime, finish: datetime, code: str) -> None:
        if user_id not in aliases or finish <= start or begin >= end:
            return
        commitments[(user_id, key)] = {
            "owner": user_id,
            "key": key,
            "start": max(start, begin).isoformat(),
            "end": min(end, finish).isoformat(),
            "capital": aliases[user_id]["capital"],
            "asset": code,
        }

    def participates(user_id: str, code: str) -> bool:
        item = aliases[user_id]
        return (item["connection"].get("status") == "connected"
                and automation_asset_enabled(item["automation"], code)
                and automation_asset_enabled(global_settings, code))

    for row in strategies:
        if row["status"] != "scheduled":
            continue
        key = str(row.get("shared_decision_id") or row["id"])
        if (row["id"] in reserved_ids or row["id"] in invalid_strategies
                or row.get("shared_decision_id") in invalid_decisions):
            continue
        linked = private_by_strategy.get(row["id"])
        if linked and not participates(row["user_id"], strategy_asset(row)):
            continue
        if assigned and linked:
            contender = proposals.get(row.get("shared_decision_id")) or linked["data"]
            if (
                linked["data"]["id"] == assigned_id
                or key == str(assigned.get("shared_decision_id") or assigned["id"])
                or proposal_key(contender) > proposal_key(assigned)
            ):
                continue
        add(row["user_id"], key, instant(row.get("entry_at") or now.isoformat()),
            instant(row.get("exit_at") or end.isoformat()), strategy_asset(row))
    for item in shared:
        row = item["data"]
        if row["id"] in invalid or row.get("allocation_completed_at"):
            continue
        if assigned and (row["id"] == assigned["id"] or proposal_key(row) > proposal_key(assigned)):
            continue
        for user_id in aliases:
            if participates(user_id, run_asset(row)):
                # An allocated or already funded copy replaces its shared commitment.
                if (user_id, row["id"]) in funded_decisions:
                    continue
                add(user_id, row["id"], instant(row["activation_time"]),
                    instant(row.get("exit_at") or end.isoformat()), run_asset(row))
    if not assigned:
        for row in jobs:
            if row["id"] == request.get("id") or row.get("outcome") or row.get("trigger") == "activation_recheck":
                continue
            if row["status"] == "scheduled" and not (asset == "ETH" and run_asset(row) == "BTC"):
                continue
            if (current and current["status"] == "running" and row["status"] == "running"
                    and (instant(row["started_at"]), row["id"])
                    > (instant(current["started_at"]), current["id"])):
                continue
            finish = session_expiry(instant(row.get("scheduled_for") or row["started_at"]))
            for user_id in aliases if row["user_id"] == "global" else [row["user_id"]]:
                if participates(user_id, run_asset(row)):
                    add(user_id, f"run:{row['id']}", now, finish, run_asset(row))
    return {
        "eligible": True, "owner": owner, "capital": account["capital"], "start": start.isoformat(),
        "occupied": sum(row["user_id"] == owner for row in slots),
        "reserved": str(sum((Decimal(str(row.get("reserved_budget") or "0")) for row in slots), Decimal(0))),
        "commitments": list(commitments.values()),
        "asset": asset,
        "reservedByAsset": {
            code: str(
                sum(
                    (
                        Decimal(str(row.get("reserved_budget") or "0"))
                        for row in slots
                        if (
                            row.get("asset")
                            or strategy_asset(
                                next(
                                    (item for item in strategies if item["id"] == row.get("strategy_id")),
                                    {},
                                )
                            )
                        )
                        == code
                    ),
                    Decimal(0),
                )
            )
            for code in ("BTC", "ETH")
        },
    }


def evaluate(snapshot: dict[str, Any], available: Decimal, total: Decimal) -> Admission:
    if not available.is_finite() or not total.is_finite():
        raise AppError(503, "Wallet capacity is invalid", "capital_capacity_unavailable")
    if not snapshot["eligible"] or available <= 0 or total <= 0:
        return "full"
    policy = policy_from_row(snapshot["capital"])
    maximum = policy.concurrency_limit(total)
    asset = snapshot.get("asset", "BTC")
    budget = policy_budget(available, total, policy, asset)
    if budget <= 0:
        return "full"
    reserved = Decimal(snapshot["reserved"])
    if not reserved.is_finite() or reserved < 0 or not budget.is_finite():
        raise AppError(503, "Committed capacity is invalid", "capital_capacity_unavailable")
    by_asset = {key: Decimal(value) for key, value in snapshot.get("reservedByAsset", {}).items()}
    if any(not value.is_finite() or value < 0 for value in by_asset.values()):
        raise AppError(503, "Committed asset capacity is invalid", "capital_capacity_unavailable")
    if snapshot["occupied"] >= maximum or not fits_capital_limits(policy, total, asset, budget, reserved, by_asset):
        return "full"
    # Half-open intervals permit back-to-back planned trades without summing disjoint holds.
    events: dict[datetime, list[tuple[int, Decimal, str]]] = {}
    for row in snapshot["commitments"]:
        other = policy_from_row(row["capital"])
        code = row.get("asset", "BTC")
        amount = policy_budget(available, total, other, code)
        if amount == 0 and not other.allow_smaller_entries:
            return "pending"
        if not amount.is_finite() or amount <= 0:
            raise AppError(503, "Committed capacity is invalid", "capital_capacity_unavailable")
        count = int(row["owner"] == snapshot["owner"])
        events.setdefault(instant(row["start"]), []).append((count, amount, code))
        events.setdefault(instant(row["end"]), []).append((-count, -amount, code))
    count, committed = snapshot["occupied"], reserved
    for _, changes in sorted(events.items()):
        count += sum(change[0] for change in changes)
        committed += sum((change[1] for change in changes), Decimal(0))
        for _, amount, code in changes:
            by_asset[code] = by_asset.get(code, Decimal(0)) + amount
        if count >= maximum or not fits_capital_limits(policy, total, asset, budget, committed, by_asset):
            return "pending"
    return "ready"
