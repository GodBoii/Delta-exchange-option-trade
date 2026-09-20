from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from agno.db.base import BaseDb
from agno.run.agent import RunOutput

from .agent import WebSearchTools, create_news_agent
from .budget import ResearchBudget
from .config import NewsAgentSettings
from .database import create_session_db
from .tools import NewsResearchTools

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class NewsPipelineResult:
    report_response: RunOutput
    model_id: str
    session_id: str
    user_id: str
    bootstrap_tools: tuple[str, ...] = ()

    @property
    def markdown(self) -> str | None:
        content = self.report_response.content
        return content.strip() if isinstance(content, str) and content.strip() else None

    @property
    def research_tools(self) -> list[str]:
        return list(dict.fromkeys([*self.bootstrap_tools, *_tool_names(self.report_response)]))


def _tool_names(run: RunOutput) -> list[str]:
    names: list[str] = []
    for execution in run.tools or []:
        if isinstance(execution, dict):
            name = execution.get("tool_name") or execution.get("name")
            function = execution.get("function")
            if not name and isinstance(function, dict):
                name = function.get("name")
        else:
            name = getattr(execution, "tool_name", None) or getattr(execution, "name", None)
        if name:
            names.append(str(name))
    return names


async def collect_live_news_context(prompt: str, settings: NewsAgentSettings) -> tuple[str, tuple[str, ...]]:
    """Collect bounded, diverse evidence once, before one model synthesis."""
    budget = ResearchBudget()
    search = WebSearchTools(budget)
    articles = NewsResearchTools(settings, budget)
    queries = [
        f"{prompt[:180]} latest news",
        "Bitcoin BTC ETF regulation latest news",
        "Federal Reserve inflation rates dollar latest news",
    ]
    results = await asyncio.gather(*(search.search_news(query) for query in queries))
    rows = []
    for result in results:
        decoded = json.loads(result)
        if isinstance(decoded, list):
            rows.append(decoded)
    tools = ["search_news"]
    if not any(rows):
        fallback = json.loads(await search.web_search(queries[0]))
        rows = [fallback] if isinstance(fallback, list) else []
        tools.append("web_search")
    # Round robin across subjects so one large result set cannot consume every article slot.
    candidates = []
    seen: set[str] = set()
    domains: dict[str, int] = {}
    for index in range(10):
        for group in rows:
            if index >= len(group):
                continue
            row = group[index]
            url = row["url"]
            domain = urlsplit(url).hostname or ""
            if url in seen or domains.get(domain, 0) >= 2:
                continue
            seen.add(url)
            domains[domain] = domains.get(domain, 0) + 1
            candidates.append(row)
    candidates = candidates[:10]
    dossier = json.loads(await articles.build_news_dossier([row["url"] for row in candidates]))
    tools.append("build_news_dossier")
    context = json.dumps(
        {
            "search_results": candidates,
            "article_dossier": dossier,
            "coverage": "Unavailable pages and copied reporting are not independent verification.",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    logger.info(
        "news.evidence articles=%d context_chars=%d calls=%s",
        dossier.get("successful", 0),
        len(context),
        dict(budget.calls),
    )
    return context, tuple(tools)


def _collect_live_news_context(prompt: str, settings: NewsAgentSettings) -> tuple[str, tuple[str, ...]]:
    return asyncio.run(collect_live_news_context(prompt, settings))


def run_news_pipeline(
    prompt: str,
    *,
    settings: NewsAgentSettings | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    db: BaseDb | None = None,
    debug_mode: bool = True,
) -> NewsPipelineResult:
    """Collect current evidence, then let the model synthesize a natural Markdown analysis."""
    settings = settings or NewsAgentSettings.load()
    effective_session_id = session_id or settings.default_session_id
    effective_user_id = user_id or settings.default_user_id

    owns_db = db is None
    session_db = db or create_session_db(settings)
    started_at = time.perf_counter()
    logger.info(
        "News pipeline started model=%s session_id=%s user_id=%s prompt_chars=%d debug_mode=%s",
        settings.model_id,
        effective_session_id,
        effective_user_id,
        len(prompt),
        debug_mode,
    )
    logger.debug("News pipeline prompt=%r", prompt)
    try:
        live_news_context, bootstrap_tools = _collect_live_news_context(prompt, settings)
        research_prompt = (
            f"{prompt}\n\n"
            "Live news-search evidence has already been collected below. Analyze it now; do not return an "
            "intermediate promise to search. Use the supplied URLs as source candidates. If evidence is "
            "insufficient, state that explicitly. Respond naturally in Markdown and return the completed "
            "customer-facing report using the required headings from your instructions.\n\n"
            f"<live_news_search_evidence>\n{live_news_context}\n</live_news_search_evidence>"
        )
        logger.debug("News pipeline research context chars=%d", len(live_news_context))
        analyst = create_news_agent(
            settings=settings,
            db=session_db,
            debug_mode=debug_mode,
            include_research_tools=False,
        )
        report_response = analyst.run(research_prompt, session_id=effective_session_id, user_id=effective_user_id)

        if (
            not isinstance(report_response.content, str)
            or not report_response.content.strip()
            or report_response.content.strip().casefold() == "provider returned error"
        ):
            raise RuntimeError("News synthesis returned no report within its output budget")

        metrics = report_response.metrics
        logger.info(
            "news.model input_tokens=%s output_tokens=%s reasoning_tokens=%s",
            getattr(metrics, "input_tokens", None),
            getattr(metrics, "output_tokens", None),
            getattr(metrics, "reasoning_tokens", None),
        )

        logger.info(
            "News pipeline completed run_id=%s elapsed_ms=%d markdown=%s tools=%s",
            report_response.run_id,
            round((time.perf_counter() - started_at) * 1_000),
            bool(isinstance(report_response.content, str) and report_response.content.strip()),
            _tool_names(report_response),
        )

        return NewsPipelineResult(
            report_response=report_response,
            model_id=analyst.model.id,
            session_id=effective_session_id,
            user_id=effective_user_id,
            bootstrap_tools=bootstrap_tools,
        )
    except Exception:
        logger.exception(
            "News pipeline failed elapsed_ms=%d session_id=%s user_id=%s",
            round((time.perf_counter() - started_at) * 1_000),
            effective_session_id,
            effective_user_id,
        )
        raise
    finally:
        if owns_db:
            session_db.close()
