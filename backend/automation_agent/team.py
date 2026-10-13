from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from agno.agent import Agent
from agno.db.in_memory import InMemoryDb
from agno.media import Image
from agno.models.openrouter import OpenRouter
from agno.run.agent import RunOutput
from agno.tools.calculator import CalculatorTools

from app.automation_schedule import fixed_runs_between, ist_text, utc_text
from news_agent.config import NewsAgentSettings
from news_agent.database import create_session_db
from news_agent.pipeline import run_news_pipeline

from .assets import PROFILES, AssetProfile
from .charts import render_candlestick_chart
from .evidence import assigned_option_overview, dumps
from .market import MarketIntelligenceTools
from .recheck import RecheckOptionTools
from .report_data import save_news_evidence
from .storage import ChartArtifact, ChartStorage
from .tools import AutomationStrategyTools, DropStrategyTools, read_parent_run_context, save_market_snapshot

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AutomationTeamResult:
    run_id: str
    session_id: str
    model_id: str
    report: str
    market_snapshot_id: str
    member_responses: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]


def run_automation_team(
    *,
    settings: NewsAgentSettings,
    user_id: str,
    agent_run_id: str,
    session_id: str,
    account_context: dict[str, Any],
    trigger: str,
    trigger_reason: str | None = None,
    signals_to_inspect: list[str] | None = None,
    asset: AssetProfile = PROFILES["BTC"],
) -> AutomationTeamResult:
    run_started_at = datetime.now(UTC)
    permanent_runs = [
        {"trigger": run.trigger, "scheduledForUtc": utc_text(run.scheduled_for),
         "scheduledForIst": ist_text(run.scheduled_for)}
        for run in fixed_runs_between(
            run_started_at + timedelta(microseconds=1), run_started_at + timedelta(days=2)
        )[:3]
    ]
    code, pair, spot = asset.code, asset.delta_index, asset.spot_symbol
    previous_run = read_parent_run_context(settings, user_id=user_id, agent_run_id=agent_run_id)
    if previous_run:
        account_context = {**account_context, "previousRun": previous_run}
    market_tools = MarketIntelligenceTools(session_trigger=trigger, asset=asset)
    # News and market I/O are independent. Only one news synthesis is performed per decision.
    with ThreadPoolExecutor(max_workers=2) as executor:
        news_future = executor.submit(
            run_news_pipeline,
            asset.news_prompt,
            settings=settings,
            session_id=f"news:{agent_run_id}",
            user_id=user_id,
            db=InMemoryDb(),
            debug_mode=True,
            asset=code,
            focus_query=asset.news_focus_query,
            evidence_sink=lambda member: save_news_evidence(settings.psycopg_url(), agent_run_id, member),
        )
        option_future = executor.submit(market_tools.collect_delta_option_context)
        market_packet = market_tools.collect_market_packet()
        option_context = option_future.result()
        news_result = news_future.result()
    chart_artifacts = _chart_artifacts(market_packet, code)
    chart_context = {chart.id: chart.context for chart in chart_artifacts}
    stored_charts = ChartStorage(settings).save_run_charts(
        user_id=user_id,
        agent_run_id=agent_run_id,
        charts=chart_artifacts,
    )
    combined_market_packet = {
        **market_packet,
        "executionOptionContext": option_context,
        "chartImages": [chart.stored_metadata() for chart in stored_charts],
        "chartContext": chart_context,
    }
    market_snapshot_id = save_market_snapshot(
        settings,
        user_id=user_id,
        agent_run_id=agent_run_id,
        market_packet=combined_market_packet,
        account_context=account_context,
    )
    strategy_tools = AutomationStrategyTools(
        settings,
        user_id=user_id,
        agent_run_id=agent_run_id,
        market_snapshot_id=market_snapshot_id,
        asset=code,
        trigger=trigger,
        review_at=datetime.fromtimestamp(market_packet["capturedAt"] / 1000, UTC),
    )

    team_db = create_session_db(settings, session_table=settings.automation_session_table)
    try:
        model = OpenRouter(
            id=settings.automation_model_id,
            api_key=settings.require_api_key(),
            supports_native_structured_outputs=False,
            reasoning_effort="max",
            timeout=180,
            max_retries=0,
            max_tokens=None,
            max_completion_tokens=None,
        )
        team = Agent(
            id=asset.agent_id,
            name=f"{code} Automation agent",
            role=(
                f"{code} options analysis agent that identifies sideways, bullish, bearish, and volatility trends, "
                "then schedules the saved strategy most likely to profit."
            ),
            model=model,
            tools=[market_tools, strategy_tools],
            description=(
                f"Analyze {pair}, compare the supplied option strategy catalog, and select one strategy and trade time."
            ),
            instructions=[
                f"Assess {pair} direction, sideways conditions, breakouts and volatility using supplied market "
                "data, charts and news. Judge news freshness against the supplied current time. Do not repeat "
                "news research or infer current option prices from spot; activation recheck evaluates actual options.",
                "Call show_available_strategy for saved strategies and strategyRef values. Choose using their "
                "descriptions and rules. Preserve legs, strikes, sizing, stops, targets and order types.",
                "Select a strategy and entry time. The backend calculates the default exit. Choose a duration "
                "only when the catalogue offers it. The system runs activation recheck seven minutes before entry.",
                "Compare current direction, volatility and volume with recent/session averages. Use numerical "
                "indicators alongside charts. Missing data is unknown, not zero.",
                "If a previous parent response is supplied, compare its thesis and requested signals with fresh "
                "evidence; treat it as historical context.",
                f"Inspect the {spot} 1-minute, 15-minute and daily price charts. Get volume, realized volatility "
                "and order-book evidence from the market tool. Use numerical EMA, ATR, VWAP, CVD and liquidity. "
                "Absent catalyst coverage does not prove "
                "no event can occur.",
                "Choose one outcome: schedule a strategy, schedule a follow-up before the next permanent run, "
                "or no trade. Avoid selection when evidence is stale, contradictory, incomplete or fails saved rules.",
                "Confirm scheduling only from a successful tool result. After confirmation, return the report. "
                "The backend handles budget and sizing; do not request or estimate account balance.",
                "Use IST in the report and timezone-aware timestamps in tools. Return concise Markdown under "
                "## Market regime, ## News analysis, ## Chart and data evidence, ## Decision and ## Invalidation. "
                "Do not expose credentials or internal secrets.",
            ],
            expected_output=(
                "A completed Markdown decision report backed by a terminal outcome and explicit "
                "invalidation conditions."
            ),
            additional_context=(
                f"Run started at: {run_started_at.isoformat()} (UTC). "
                f"Next three permanent agent runs: {dumps(permanent_runs)}. "
                "Assess news publication and event age against this time. Weight freshness, verification and "
                "continuing relevance; older news may still matter. Unknown dates stay unknown. "
                f"Current news evidence: {news_result.markdown}. "
                f"Current trigger: {trigger}. Trigger reason: {trigger_reason or 'scheduled market analysis'}. "
                f"Signals requested by the prior run: {json.dumps(signals_to_inspect or [], ensure_ascii=False)}. "
                "Chart reading instructions and exact values are keyed by the attached image IDs. "
                f"Use these with the plots: {json.dumps(chart_context, ensure_ascii=False)}. "
                "Current active strategies and upcoming agent runs follow: "
                f"{json.dumps(account_context, ensure_ascii=False, default=str)}"
            ),
            db=team_db,
            add_datetime_to_context=True,
            timezone_identifier="Asia/Kolkata",
            store_events=True,
            # Charts live in ai.chart_images; sessions keep text, not base64 image copies.
            store_media=False,
            tool_call_limit=48,
            debug_mode=True,
            telemetry=False,
        )
        images = [
            Image(
                content=chart.content,
                format="png",
                mime_type="image/png",
                id=chart.id,
                alt_text=chart.alt_text,
                detail="high",
            )
            for chart in stored_charts
        ]
        stored_session_id = f"automation:{user_id}:{session_id}"
        response = asyncio.run(
            team.arun(
                f"Analyze the current {code} market and choose the appropriate live action.",
                session_id=stored_session_id,
                user_id=user_id,
                images=images,
                metadata={
                    "triggerReason": trigger_reason or "scheduled market analysis",
                    "trigger": trigger,
                    "signalsToInspect": signals_to_inspect or [],
                    "marketSnapshotId": market_snapshot_id,
                },
            )
        )
        if not isinstance(response, RunOutput):
            raise RuntimeError("Automation team returned an unexpected streaming response")
        if str(getattr(response.status, "value", response.status)).upper() == "ERROR":
            raise RuntimeError("Market analysis model failed")
        report = (
            response.content.strip() if isinstance(response.content, str) else json.dumps(response.content, default=str)
        )
        if not report:
            raise RuntimeError("Automation team returned an empty report")
        if report.casefold() in {"provider returned error", "request timed out.", "request timed out"}:
            raise RuntimeError("Automation model provider did not return a decision report")
        logger.info(
            "automation.model run_id=%s input_tokens=%s output_tokens=%s reasoning_tokens=%s",
            agent_run_id,
            getattr(response.metrics, "input_tokens", None),
            getattr(response.metrics, "output_tokens", None),
            getattr(response.metrics, "reasoning_tokens", None),
        )
        return AutomationTeamResult(
            run_id=str(response.run_id),
            session_id=stored_session_id,
            model_id=str(response.model or settings.automation_model_id),
            report=report,
            market_snapshot_id=market_snapshot_id,
            member_responses=[
                {
                    **_response_summary(news_result.report_response),
                    "researchTools": news_result.research_tools,
                    "researchTrace": news_result.research_trace,
                    "reportQuality": getattr(news_result, "quality", {}),
                }
            ],
            tool_calls=[_tool_summary(item) for item in response.tools or []],
        )
    finally:
        team_db.close()


def run_activation_recheck(
    *,
    settings: NewsAgentSettings,
    user_id: str,
    agent_run_id: str,
    session_id: str,
    recheck_context: dict[str, Any],
    asset: AssetProfile = PROFILES["BTC"],
) -> AutomationTeamResult:
    code = asset.code
    market_tools = MarketIntelligenceTools(
        asset=asset,
        assigned_expiries=[
            leg["expiry"] for leg in recheck_context["selectedStrategy"].get("definition", {}).get("legs", [])
        ],
    )
    market_packet = market_tools.collect_market_packet()
    chart_artifacts = _recheck_chart_artifacts(market_packet, code)
    chart_context = {chart.id: chart.context for chart in chart_artifacts}
    option_tools = RecheckOptionTools(asset=asset, selected_strategy=recheck_context["selectedStrategy"])
    market_packet["selectedOptionEvidence"] = option_tools.collect_evidence()
    market_packet["assignedOptionOverview"] = assigned_option_overview(market_packet, market_tools.assigned_expiries)
    stored_charts = ChartStorage(settings).save_run_charts(
        user_id=user_id,
        agent_run_id=agent_run_id,
        charts=chart_artifacts,
    )
    market_snapshot_id = save_market_snapshot(
        settings,
        user_id=user_id,
        agent_run_id=agent_run_id,
        market_packet={
            **market_packet,
            "chartImages": [chart.stored_metadata() for chart in stored_charts],
            "chartContext": chart_context,
        },
        account_context=recheck_context,
    )
    strategy = recheck_context["selectedStrategy"]
    drop_tools = DropStrategyTools(
        settings,
        user_id=user_id,
        agent_run_id=agent_run_id,
        proposal_id=str(recheck_context["proposalId"]),
    )
    model = OpenRouter(
        id=settings.automation_model_id,
        api_key=settings.require_api_key(),
        supports_native_structured_outputs=False,
        reasoning_effort="low",
        timeout=240,
        max_retries=0,
        max_tokens=None,
        max_completion_tokens=None,
    )
    fresh_market = market_tools.market_packet_json()
    agent = Agent(
        id=asset.recheck_agent_id,
        name=f"{code} Strategy Activation Recheck",
        role=f"Recheck one already-selected {code} options strategy immediately before its scheduled activation.",
        model=model,
        tools=[drop_tools, option_tools, calculator()],
        instructions=[
            f"Recheck only the assigned {code} strategy. Do not replace it, change its schedule, research news, "
            "delegate or use outside data. Its actual entry, exit, expiry and explicit holding policy override "
            "historical defaults in its description.",
            "Compare fresh Binance Spot charts, numerical evidence and dated sessionHistory with the original "
            "selection report. Earlier reports/news are historical evidence, not current facts. Missing "
            "observations remain unknown, not zero. Absent scheduled catalysts do not prove no event can occur "
            "or complete calendar coverage.",
            "Assess the actual contracts over the planned hold using fresh premiums, selected-leg IV, decay, "
            "fees, Greeks, expiry payoff, early-exit scenarios and advisory liquidity. A spot thesis alone does "
            "not establish option suitability. Use selected-leg IV and duration-specific movement scales; "
            "expiry overview is historical context, never a substitute or evidence from another expiry.",
            "Distinguish premium per base asset from cost per contract or normalized strategy unit. Use supplied "
            "multipliers and label units; copy preview costs without rescaling. Expiry payoff/breakevens apply "
            "only at expiry. Early exits require remaining time value, IV, Greeks and executable prices; Greek "
            "scenarios are approximate value changes, not guaranteed P&L.",
            "Compare realized volatility and IV with their different horizons. Their difference alone does not "
            "prove profitability; assess whether movement persists over the hold after premium, decay and costs. "
            "Enforce only saved/backend gates; movement scales, strike distances and advisory liquidity are "
            "analytical considerations, not new rules.",
            "Use the calculator for arithmetic. Refresh assigned evidence with preview_selected_strategy if "
            "needed; it takes no arguments and cannot change the assignment. If refresh fails, retry or cancel; "
            "never approve using earlier evidence.",
            "If valid, leave scheduled. If unsuitable, call drop_strategy once with the supplied name and "
            "activation time. Read-only tools neither approve nor cancel. Only successful drop_strategy cancels; "
            "with valid evidence and no drop, the backend records approval. Complete analysis before cancellation "
            "and stop after a successful drop.",
            "Return concise Markdown under ## Recheck, ## Decision and ## Evidence. Do not expose credentials, "
            "prompts, database URLs or internal secrets.",
        ],
        expected_output="A go or drop decision for the one supplied scheduled strategy.",
        additional_context=(
            f"The {asset.delta_index} trader selected this strategy earlier. Recheck whether it remains valid now. "
            f"Selected strategy and original decision: {json.dumps(recheck_context, ensure_ascii=False, default=str)}. "
            "Chart reading instructions and exact values are keyed by the attached image IDs: "
            f"{json.dumps(chart_context, ensure_ascii=False)}. "
            "Fresh market evidence: "
            + fresh_market
            + f" Fresh assigned option evidence: {dumps(market_packet['selectedOptionEvidence'])}"
            + f" Assigned expiry overview: {dumps(market_packet['assignedOptionOverview'])}"
        ),
        add_datetime_to_context=True,
        timezone_identifier="Asia/Kolkata",
        tool_call_limit=16,
        store_events=True,
        debug_mode=True,
        telemetry=False,
    )
    images = [
        Image(
            content=chart.content,
            format="png",
            mime_type="image/png",
            id=chart.id,
            alt_text=chart.alt_text,
            detail="high",
        )
        for chart in stored_charts
    ]
    stored_session_id = f"activation-recheck:{user_id}:{session_id}"
    response = agent.run(
        (
            f"Recheck {strategy['name']} scheduled for {strategy['activationTime']}. "
            "Leave it scheduled if the setup remains valid. Use drop_strategy if it no longer does."
        ),
        session_id=stored_session_id,
        user_id=user_id,
        images=images,
        metadata={"proposalId": recheck_context["proposalId"], "marketSnapshotId": market_snapshot_id},
    )
    if not isinstance(response, RunOutput):
        raise RuntimeError("Activation recheck returned an unexpected streaming response")
    if str(getattr(response.status, "value", response.status)).upper() == "ERROR":
        raise RuntimeError("Activation recheck model failed")
    if not option_tools.evidence_valid and not any(item.tool_name == "drop_strategy" for item in response.tools or []):
        raise RuntimeError("Activation recheck cannot approve without valid fresh option evidence")
    report = response.content.strip() if isinstance(response.content, str) else ""
    if not report:
        raise RuntimeError("Activation recheck returned an empty report")
    if report.casefold() in {"provider returned error", "request timed out.", "request timed out"}:
        raise RuntimeError("Activation recheck model provider returned an error")
    return AutomationTeamResult(
        run_id=str(response.run_id),
        session_id=stored_session_id,
        model_id=str(response.model or settings.automation_model_id),
        report=report,
        market_snapshot_id=market_snapshot_id,
        member_responses=[],
        tool_calls=[_tool_summary(item) for item in response.tools or []],
    )


def calculator() -> CalculatorTools:
    return CalculatorTools(include_tools=["add", "subtract", "multiply", "divide", "exponentiate", "square_root"])


def _chart_artifacts(
    market_packet: dict[str, Any],
    base: str = "BTC",
) -> list[ChartArtifact]:
    charts: list[ChartArtifact] = []

    def add(renderer: Callable[..., bytes], args: tuple, image_id: str, label: str, alt_text: str) -> None:
        context: dict[str, Any] = {}
        chart = renderer(*args, as_of_ms=market_packet.get("capturedAt"), context=context, base=base)
        if chart:
            charts.append(
                ChartArtifact(
                    content=chart,
                    id=image_id,
                    label=label,
                    alt_text=alt_text,
                    context=context,
                )
            )

    prefix, spot = base.lower(), f"{base}USDT"
    for label in ("1 minute", "15 minute", "1 day"):
        payload = (market_packet.get("timeframes") or {}).get(label) or {}
        add(
            render_candlestick_chart,
            (label, payload.get("candles") or []),
            f"{prefix}-{str(label).replace(' ', '-')}",
            f"{spot} {label} price",
            f"{spot} {label} candlestick chart from Binance Spot",
        )
    return charts


def _recheck_chart_artifacts(market_packet: dict[str, Any], base: str = "BTC") -> list[ChartArtifact]:
    charts: list[ChartArtifact] = []
    prefix, spot = base.lower(), f"{base}USDT"
    for label in ("1 minute", "15 minute"):
        payload = (market_packet.get("timeframes") or {}).get(label) or {}
        context: dict[str, Any] = {}
        chart = render_candlestick_chart(
            label,
            payload.get("candles") or [],
            as_of_ms=market_packet.get("capturedAt"),
            context=context,
            base=base,
        )
        if chart:
            charts.append(
                ChartArtifact(
                    content=chart,
                    id=f"{prefix}-{label.replace(' ', '-')}",
                    label=f"{spot} {label} price",
                    alt_text=f"Fresh {spot} {label} candlestick chart from Binance Spot",
                    context=context,
                )
            )
    return charts


def _response_summary(response: Any) -> dict[str, Any]:
    research_tools = []
    for execution in getattr(response, "tools", None) or []:
        if isinstance(execution, dict):
            name = execution.get("tool_name") or execution.get("name")
        else:
            name = getattr(execution, "tool_name", None) or getattr(execution, "name", None)
        if name and str(name) not in research_tools:
            research_tools.append(str(name))
    return {
        "runId": getattr(response, "run_id", None),
        "agentId": getattr(response, "agent_id", None),
        "agentName": getattr(response, "agent_name", None),
        "model": getattr(response, "model", None),
        "content": getattr(response, "content", None),
        "createdAt": getattr(response, "created_at", None),
        "status": str(getattr(response, "status", "")),
        "researchTools": research_tools,
    }


def _tool_summary(execution: Any) -> dict[str, Any]:
    if isinstance(execution, dict):
        return {
            "name": execution.get("tool_name") or execution.get("name"),
            "args": execution.get("tool_args") or execution.get("arguments"),
            "result": execution.get("result") or execution.get("tool_result"),
        }
    return {
        "name": getattr(execution, "tool_name", None) or getattr(execution, "name", None),
        "args": getattr(execution, "tool_args", None) or getattr(execution, "arguments", None),
        "result": getattr(execution, "result", None),
        "durationSeconds": getattr(getattr(execution, "metrics", None), "duration", None),
    }
