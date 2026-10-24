from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from agno.db.base import BaseDb
from agno.run.agent import RunOutput
from agno.run.base import RunStatus

from .agent import _create_research_tools, create_news_agent
from .budget import ResearchBudget
from .collector import initial_collection
from .config import NewsAgentSettings
from .coverage import collection_coverage, coverage_notice
from .database import create_session_db
from .events import cluster_events
from .failures import NewsResearchError, provider_failure
from .intelligence import Brief, analyze_evidence, evidence_documents, render_events
from .memory import memory_context, recent_events, save_events
from .presentation import format_time
from .report_quality import clean_report
from .research import prepare_research

logger = logging.getLogger(__name__)
BTC_FOCUS_QUERY = "Bitcoin BTC ETF regulation latest news"


def _tool_field(execution: Any, *names: str) -> Any:
    for name in names:
        value = execution.get(name) if isinstance(execution, dict) else getattr(execution, name, None)
        if value is not None:
            return value
    return None


def _tool_names(run: RunOutput) -> list[str]:
    return list(
        dict.fromkeys(
            str(name) for execution in run.tools or [] if (name := _tool_field(execution, "tool_name", "name"))
        )
    )


def _decode_result(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value[:240]


def _trace_result(name: str, value: Any) -> dict[str, Any]:
    result = _decode_result(value)
    if isinstance(result, list):
        return {
            "count": len(result),
            "items": [
                {
                    key: (row.get(key)[:200] if key == "body" and isinstance(row.get(key), str) else row.get(key))
                    for key in ("title", "url", "date", "source", "publisher", "access", "body")
                    if row.get(key) is not None
                }
                for row in result[:10]
                if isinstance(row, dict)
            ],
        }
    if not isinstance(result, dict):
        return {"text": str(result)[:240]}
    if name == "curate_public_sources":
        return {
            "source_count": result.get("source_count"),
            "count": len(result.get("sources") or []),
            "items": [
                {
                    key: (item.get(key)[:200] if key == "excerpt" and isinstance(item.get(key), str) else item.get(key))
                    for key in ("source", "title", "url", "published_at", "excerpt", "older_than_window", "kind")
                }
                for item in (result.get("sources") or [])[:160]
            ],
            "errors": result.get("errors") or [],
            "source_health": result.get("source_health") or [],
        }
    if name == "search_public_discussion":
        return {
            "count": len(result.get("posts") or []),
            "items": [
                {key: item.get(key) for key in ("platform", "title", "url", "published_at", "access")}
                for item in (result.get("posts") or [])[:25]
            ],
            "errors": result.get("errors") or [],
        }
    if name == "search_exchange_announcements":
        return {
            "count": len(result.get("announcements") or []),
            "items": [
                {key: item.get(key) for key in ("exchange", "title", "url", "search_date", "access")}
                for item in (result.get("announcements") or [])[:30]
            ],
            "errors": result.get("errors") or [],
        }
    if name == "build_news_dossier":
        items = []
        for item in (result.get("items") or [])[:10]:
            article = item.get("article") or {}
            items.append(
                {
                    "ok": item.get("ok"),
                    "url": article.get("final_url") or item.get("url"),
                    "requested_url": article.get("requested_url"),
                    "canonical_url": article.get("canonical_url"),
                    "title": article.get("title"),
                    "published_at": article.get("published_at"),
                    "feed_published_at": article.get("feed_published_at"),
                    "publication_date_source": article.get("publication_date_source"),
                    "text_chars": len(article.get("text") or ""),
                    "excerpt": (article.get("text") or "")[:200],
                    "error": item.get("error"),
                }
            )
        return {
            "requested": result.get("requested"),
            "successful": result.get("successful"),
            "items": items,
            "error": result.get("error"),
        }
    if name == "read_news_article":
        article = result.get("article") or {}
        return {
            "ok": result.get("ok"),
            "url": article.get("final_url") or result.get("url"),
            "requested_url": article.get("requested_url"),
            "canonical_url": article.get("canonical_url"),
            "title": article.get("title"),
            "published_at": article.get("published_at"),
            "feed_published_at": article.get("feed_published_at"),
            "publication_date_source": article.get("publication_date_source"),
            "text_chars": len(article.get("text") or ""),
            "excerpt": (article.get("text") or "")[:200],
            "error": result.get("error"),
        }
    return {"error": result.get("error"), "count": len(result.get("results") or [])}


def research_trace(run: RunOutput) -> list[dict[str, Any]]:
    """Persist bounded research outcomes without copying full article bodies."""
    trace = []
    for execution in run.tools or []:
        name = _tool_field(execution, "tool_name", "name")
        if not name:
            continue
        args = _tool_field(execution, "tool_args", "arguments")
        metrics = _tool_field(execution, "metrics")
        trace.append(
            {
                "name": str(name),
                "origin": "pipeline"
                if str(_tool_field(execution, "tool_call_id") or "").startswith("prepared:")
                else "model",
                "args": args if isinstance(args, dict) else str(args)[:500],
                "result": _trace_result(str(name), _tool_field(execution, "result", "tool_result")),
                "duration_seconds": getattr(metrics, "duration", None),
            }
        )
    return trace


@dataclass(frozen=True, slots=True)
class NewsPipelineResult:
    report_response: RunOutput
    model_id: str
    session_id: str
    user_id: str
    quality: dict[str, Any] = field(default_factory=dict)

    @property
    def markdown(self) -> str | None:
        content = self.report_response.content
        return content.strip() if isinstance(content, str) and content.strip() else None

    @property
    def research_tools(self) -> list[str]:
        return _tool_names(self.report_response)

    @property
    def research_trace(self) -> list[dict[str, Any]]:
        return research_trace(self.report_response)


def strategy_evidence_ready(quality: dict[str, Any]) -> bool:
    intelligence = quality.get("intelligence")
    accepted = intelligence.get("accepted_claims") if isinstance(intelligence, dict) else None
    originals = quality.get("original_reads")
    return (
        quality.get("research_status") == "verified_findings"
        and type(originals) is int
        and originals > 0
        and type(accepted) is int
        and accepted > 0
    )


def run_news_pipeline(
    prompt: str,
    *,
    settings: NewsAgentSettings | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    db: BaseDb | None = None,
    debug_mode: bool = True,
    asset: str = "BTC",
    focus_query: str = BTC_FOCUS_QUERY,
    evidence_sink: Callable[[dict[str, Any]], None] | None = None,
) -> NewsPipelineResult:
    """Let the analyst choose source collection, web searches, and article reads."""
    run_started_at = datetime.now(UTC)
    settings = settings or NewsAgentSettings.load()
    effective_session_id = session_id or settings.default_session_id
    effective_user_id = user_id or settings.default_user_id
    owns_db = db is None
    session_db = db or create_session_db(settings)
    started_at = time.perf_counter()
    report_response = None
    evidence_written = False
    prepared = []
    try:
        history = []
        if settings.structured_news and settings.database_url:
            history = recent_events(settings.psycopg_url(), asset, run_started_at)
        budget = ResearchBudget(limits={"web_search": 20, "read_news_article": 16})
        toolkits = _create_research_tools(settings, budget) if settings.structured_news else None
        analyst = create_news_agent(
            settings=settings,
            db=session_db,
            debug_mode=debug_mode,
            asset=asset,
            research_budget=budget,
            research_tools=toolkits,
        )
        research_prompt = (
            f"Run started at: {format_time(run_started_at)}. Times use IST, UTC+05:30.\n\n"
            f"{prompt}\n\nFocus query: {focus_query}. Gather current material from public sources and "
            "search the web yourself. Investigate related global developments, scheduled meetings, "
            "crypto announcements, and public discussion. Open useful source pages before citing them. "
            "Continue with available evidence when a source fails; explain material gaps. Return the completed "
            "Markdown report with linked evidence and clear market implications."
            + memory_context(history)
            + ("" if settings.structured_news else initial_collection(asset, run_started_at))
        )

        async def research():
            context = ""
            if toolkits is not None:
                try:
                    async with asyncio.timeout(90):
                        context = await prepare_research(toolkits, asset, prepared)
                except (TimeoutError, ValueError) as error:
                    logger.warning("Initial original collection incomplete asset=%s reason=%s", asset, error)
                    context = "\nInitial collection was incomplete; use your research tools to fill the gaps."
            return await analyst.arun(
                research_prompt + context, session_id=effective_session_id, user_id=effective_user_id
            )

        report_response = asyncio.run(research())
        if isinstance(report_response, RunOutput):
            report_response.tools = [*prepared, *(report_response.tools or [])]
        if (
            not isinstance(report_response, RunOutput)
            or report_response.status != RunStatus.completed
            or not isinstance(report_response.content, (str, Brief))
            or (isinstance(report_response.content, str) and not report_response.content.strip())
            or (
                isinstance(report_response.content, str)
                and report_response.content.strip().casefold()
                in {"provider returned error", "the operation was aborted", "request timed out", "request timed out."}
            )
        ):
            failure = getattr(analyst.model, "last_failure", None) or {
                "code": "news_synthesis_failed",
                "stage": "news",
                "message": "News synthesis returned no report",
            }
            raise NewsResearchError(
                failure, research_trace(report_response) if isinstance(report_response, RunOutput) else []
            )
        proposed = report_response.content if isinstance(report_response.content, Brief) else None
        if proposed is not None and not proposed.events:
            proposed = None
        cleaned = clean_report(
            "## Source evidence" if isinstance(report_response.content, Brief) else report_response.content,
            report_response.tools or [],
        )
        coverage = collection_coverage(report_response.tools or [])
        report_response.content = cleaned.markdown + coverage_notice(coverage)
        quality = {
            "omitted_blocks": cleaned.omitted_blocks,
            "unsupported_urls": list(cleaned.unsupported_urls),
            "verified_urls": list(cleaned.verified_urls),
            "coverage": coverage,
        }
        if settings.structured_news:
            requested = (
                {claim.source_id for event in proposed.events for claim in event.claims}
                if proposed is not None
                else None
            )
            documents = evidence_documents(report_response.tools or [], requested)
            if not documents:
                raise NewsResearchError(
                    {
                        "code": "news_research_incomplete",
                        "stage": "news",
                        "message": "No usable original or calendar news evidence was retrieved. Selection is blocked.",
                    },
                    research_trace(report_response),
                )

            async def verify():
                try:
                    async with asyncio.timeout(180):
                        if proposed is not None:
                            return await analyze_evidence(settings, "", documents, run_started_at, brief=proposed)
                        return await analyze_evidence(settings, cleaned.markdown, documents, run_started_at)
                except TimeoutError as error:
                    raise NewsResearchError(
                        {
                            "code": "news_verification_timeout",
                            "stage": "news",
                            "message": "News verification exceeded its 180-second deadline. Selection is blocked.",
                        },
                        research_trace(report_response),
                    ) from error

            intelligence = asyncio.run(verify())
            intelligence["events"] = cluster_events(intelligence["events"], history)
            quality["intelligence"] = {key: value for key, value in intelligence.items() if key != "markdown"}
            quality["retrieved_documents"] = len(documents)
            quality["original_reads"] = sum(doc["kind"] == "article" for doc in documents.values())
            actionable = any(
                claim.get("source_kind") != "discussion"
                for event in intelligence["events"]
                for claim in event["claims"]
            )
            quality["research_status"] = (
                "verified_findings"
                if actionable and quality["original_reads"]
                else "limited_findings"
                if actionable
                else "researched_no_findings"
            )
            report_response.content = render_events(intelligence["events"], intelligence["gaps"])
            report_response.content += coverage_notice(coverage)
            if settings.database_url:
                save_events(settings.psycopg_url(), intelligence["events"])
        if evidence_sink:
            evidence_written = True
            evidence_sink(
                {
                    "agentId": report_response.agent_id or "news-intelligence-analyst",
                    "agentName": "News Intelligence Analyst",
                    "runId": report_response.run_id,
                    "model": analyst.model.id,
                    "status": str(report_response.status),
                    "content": report_response.content,
                    "createdAt": report_response.created_at,
                    "researchTools": _tool_names(report_response),
                    "researchTrace": research_trace(report_response),
                    "reportQuality": quality,
                }
            )
        logger.info(
            "News pipeline completed run_id=%s elapsed_ms=%d tools=%s",
            report_response.run_id,
            round((time.perf_counter() - started_at) * 1000),
            _tool_names(report_response),
        )
        return NewsPipelineResult(
            report_response=report_response,
            model_id=analyst.model.id,
            session_id=effective_session_id,
            user_id=effective_user_id,
            quality=quality,
        )
    except Exception as error:
        if isinstance(error, NewsResearchError) and not error.trace:
            error.trace = (
                research_trace(report_response)
                if isinstance(report_response, RunOutput)
                else research_trace(RunOutput(tools=prepared))
            )
        if evidence_sink and not evidence_written:
            failure = (
                error.failure
                if isinstance(error, NewsResearchError)
                else provider_failure(error, settings.openrouter_api_key)
            )
            evidence_sink(
                {
                    "agentId": "news-intelligence-analyst",
                    "agentName": "News Intelligence Analyst",
                    "status": "error",
                    "content": None,
                    "failure": failure,
                    "researchTrace": research_trace(report_response)
                    if isinstance(report_response, RunOutput)
                    else research_trace(RunOutput(tools=prepared)),
                }
            )
        logger.exception("News pipeline failed session_id=%s user_id=%s", effective_session_id, effective_user_id)
        raise
    finally:
        if owns_db:
            session_db.close()
