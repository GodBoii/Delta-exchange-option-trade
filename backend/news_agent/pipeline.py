from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from agno.db.base import BaseDb
from agno.run.agent import RunOutput
from agno.run.base import RunStatus

from .agent import create_news_agent
from .budget import ResearchBudget
from .config import NewsAgentSettings
from .database import create_session_db

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
                    for key in ("title", "url", "date", "source", "body")
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
                    for key in ("source", "title", "url", "published_at", "excerpt")
                }
                for item in (result.get("sources") or [])[:40]
            ],
            "errors": result.get("errors") or [],
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
                for item in (result.get("announcements") or [])[:20]
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
                    "title": article.get("title"),
                    "published_at": article.get("published_at"),
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
            "title": article.get("title"),
            "published_at": article.get("published_at"),
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
) -> NewsPipelineResult:
    """Let the analyst choose source collection, web searches, and article reads."""
    settings = settings or NewsAgentSettings.load()
    effective_session_id = session_id or settings.default_session_id
    effective_user_id = user_id or settings.default_user_id
    owns_db = db is None
    session_db = db or create_session_db(settings)
    started_at = time.perf_counter()
    try:
        analyst = create_news_agent(
            settings=settings,
            db=session_db,
            debug_mode=debug_mode,
            asset=asset,
            research_budget=ResearchBudget(),
        )
        research_prompt = (
            f"{prompt}\n\nFocus query: {focus_query}. Gather current material from public sources and "
            "search the web yourself. Investigate related global developments, scheduled meetings, "
            "crypto announcements, and public discussion. Open useful source pages before citing them. "
            "Continue with available evidence when a source fails; explain material gaps. Return the completed "
            "Markdown report using the required headings."
        )
        report_response = analyst.run(
            research_prompt,
            session_id=effective_session_id,
            user_id=effective_user_id,
        )
        if (
            report_response.status in {RunStatus.error, RunStatus.cancelled}
            or not isinstance(report_response.content, str)
            or not report_response.content.strip()
            or report_response.content.strip().casefold()
            in {"provider returned error", "the operation was aborted", "request timed out", "request timed out."}
        ):
            raise RuntimeError("News synthesis returned no report")
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
        )
    except Exception:
        logger.exception("News pipeline failed session_id=%s user_id=%s", effective_session_id, effective_user_id)
        raise
    finally:
        if owns_db:
            session_db.close()
