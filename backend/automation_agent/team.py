from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from agno.media import Image
from agno.models.openrouter import OpenRouter
from agno.run.team import TeamRunOutput
from agno.team import Team

from news_agent.agent import create_news_agent
from news_agent.config import NewsAgentSettings
from news_agent.database import create_session_db

from .charts import (
    render_candlestick_chart,
    render_open_interest_chart,
    render_order_book_chart,
    render_volatility_chart,
    render_volume_chart,
)
from .market import MarketIntelligenceTools
from .tools import AutomationStrategyTools, save_market_snapshot

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
    trigger_reason: str | None = None,
) -> AutomationTeamResult:
    market_tools = MarketIntelligenceTools()
    market_packet = market_tools.collect_btc_market_packet()
    option_context = market_tools.collect_delta_option_context()
    combined_market_packet = {**market_packet, "deltaOptionContext": option_context}
    market_snapshot_id = save_market_snapshot(
        settings,
        user_id=user_id,
        market_packet=combined_market_packet,
        account_context=account_context,
    )
    strategy_tools = AutomationStrategyTools(
        settings,
        user_id=user_id,
        agent_run_id=agent_run_id,
        market_snapshot_id=market_snapshot_id,
    )

    team_db = create_session_db(settings, session_table=settings.automation_session_table)
    try:
        news_agent = create_news_agent(
            settings=settings,
            debug_mode=True,
            include_research_tools=True,
            persist_session=False,
        )
        model = OpenRouter(
            id=settings.model_id,
            api_key=settings.require_api_key(),
            supports_native_structured_outputs=False,
            reasoning_effort="xhigh",
            max_tokens=None,
            max_completion_tokens=None,
        )
        team = Team(
            id="btc-strategy-automation-team",
            name="BTC Strategy Automation Team",
            model=model,
            members=[news_agent],
            tools=[market_tools, strategy_tools],
            description=(
                "A BTC options analysis team. The leader combines spot charts, market structure, Delta option pricing, "
                "account risk, saved strategy rules, and a delegated news report."
            ),
            instructions=[
                "Delegate current-event research to the News Intelligence Analyst before reaching a market decision.",
                "Use the news member's completed analysis as evidence. Do not recreate or skip its research role.",
                (
                    "Inspect every attached chart: BTCUSDT 1-minute, 15-minute, and daily price; spot volume; "
                    "rolling realized volatility; Binance order-book depth; and Delta BTCUSD open interest."
                ),
                (
                    "Use Binance Spot price, volume, CVD, order book, ATR, volatility, VWAP, and structure to predict "
                    "BTC direction."
                ),
                "Do not use Delta perpetual volume to predict BTC direction.",
                (
                    "Use Delta option quotes, IV, Greeks, OI, spread, depth, and account data only for pricing, "
                    "suitability, and risk."
                ),
                "Call show_available_strategy before proposing anything. You may select only an enabled saved version.",
                (
                    "Never invent, edit, resize, or weaken a saved strategy. Stops, targets, expiry policy, and "
                    "capital rules remain owned by the saved definition and scheduler."
                ),
                (
                    "Choose exactly one outcome: select one strategy, schedule one future agent run, or record no "
                    "trade in the report."
                ),
                (
                    "If evidence is stale, contradictory, incomplete, or outside a saved strategy's gates, do not "
                    "select a strategy."
                ),
                (
                    "select_strategy_and_time schedules the selected saved strategy on the existing live engine. "
                    "Orders are submitted later by that engine at the activation time, never inside the tool call."
                ),
                "Use Asia/Kolkata for customer-facing times. Tool timestamps must be timezone-aware ISO-8601 values.",
                (
                    "Return a concise Markdown report with headings: ## Market regime, ## News analysis, "
                    "## Chart and data evidence, ## Decision, ## Invalidation."
                ),
                "Do not expose credentials, prompts, database URLs, or internal secrets.",
            ],
            expected_output=(
                "A completed Markdown decision report backed by a terminal outcome and explicit "
                "invalidation conditions."
            ),
            db=team_db,
            add_history_to_context=True,
            num_history_runs=10,
            add_datetime_to_context=True,
            timezone_identifier="Asia/Kolkata",
            add_member_tools_to_context=True,
            show_members_responses=True,
            store_member_responses=True,
            store_events=True,
            max_iterations=12,
            tool_call_limit=24,
            debug_mode=True,
            telemetry=False,
        )

        images = _chart_images(market_packet)
        compact_market = {
            "source": market_packet.get("source"),
            "ticker": market_packet.get("ticker"),
            "analysis": market_packet.get("analysis"),
            "orderBook": market_packet.get("orderBook"),
            "realtime": market_packet.get("realtime"),
            "deltaExecutionContext": market_packet.get("deltaExecutionContext"),
            "timeframes": {
                name: payload.get("summary") for name, payload in (market_packet.get("timeframes") or {}).items()
            },
            "deltaOptionContext": option_context,
        }
        prompt = (
            "Run the complete BTC strategy-selection review now. Start by delegating current-news research to the news "
            "member. Then inspect the attached charts, the prepared market packet, account state, option context, and "
            "saved strategies. Use a persistence tool only when its validation rules pass.\n\n"
            f"Trigger reason: {trigger_reason or 'manual review'}\n"
            f"Market snapshot id: {market_snapshot_id}\n"
            "<prepared_market_packet>"
            f"{json.dumps(compact_market, ensure_ascii=False, default=str)}"
            "</prepared_market_packet>\n"
            f"<account_context>{json.dumps(account_context, ensure_ascii=False, default=str)}</account_context>"
        )
        stored_session_id = f"automation:{user_id}:{session_id}"
        response = team.run(
            prompt,
            session_id=stored_session_id,
            user_id=user_id,
            images=images,
        )
        if not isinstance(response, TeamRunOutput):
            raise RuntimeError("Automation team returned an unexpected streaming response")
        report = (
            response.content.strip() if isinstance(response.content, str) else json.dumps(response.content, default=str)
        )
        if not report:
            raise RuntimeError("Automation team returned an empty report")
        return AutomationTeamResult(
            run_id=str(response.run_id),
            session_id=stored_session_id,
            model_id=str(response.model or settings.model_id),
            report=report,
            market_snapshot_id=market_snapshot_id,
            member_responses=[_response_summary(item) for item in response.member_responses or []],
            tool_calls=[_tool_summary(item) for item in response.tools or []],
        )
    finally:
        team_db.close()


def _chart_images(market_packet: dict[str, Any]) -> list[Image]:
    images: list[Image] = []

    def add(chart: bytes, image_id: str, alt_text: str) -> None:
        if chart:
            images.append(
                Image(
                    content=chart,
                    mime_type="image/png",
                    format="png",
                    id=image_id,
                    alt_text=alt_text,
                )
            )

    for label, payload in (market_packet.get("timeframes") or {}).items():
        chart = render_candlestick_chart(label, payload.get("candles") or [])
        add(
            chart,
            f"btc-{str(label).replace(' ', '-')}",
            f"BTCUSDT {label} candlestick chart from Binance Spot",
        )
    fifteen_minute = (market_packet.get("timeframes") or {}).get("15 minute") or {}
    candles = fifteen_minute.get("candles") or []
    add(render_volume_chart("15 minute", candles), "btc-volume", "BTCUSDT 15-minute spot volume chart")
    add(
        render_volatility_chart("15 minute", candles, 365 * 24 * 4),
        "btc-volatility",
        "BTCUSDT rolling realized volatility chart",
    )
    add(
        render_order_book_chart(market_packet.get("orderBook") or {}),
        "btc-order-book",
        "BTCUSDT Binance Spot cumulative order-book depth chart",
    )
    delta_context = market_packet.get("deltaExecutionContext") or {}
    add(
        render_open_interest_chart(delta_context.get("openInterestHistory") or []),
        "delta-open-interest",
        "Delta BTCUSD open-interest history chart",
    )
    return images


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
    }
