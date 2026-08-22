from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from agno.db.base import BaseDb
from agno.run.agent import RunOutput

from .agent import WebSearchTools, create_news_agent
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


def _result_urls(serialized_results: str) -> list[str]:
    try:
        results = json.loads(serialized_results)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(results, list):
        return []
    urls: list[str] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        url = item.get("url") or item.get("href")
        if isinstance(url, str) and url.startswith(("http://", "https://")) and url not in urls:
            urls.append(url)
    return urls


def _collect_live_news_context(prompt: str, settings: NewsAgentSettings) -> tuple[str, tuple[str, ...]]:
    """Deterministically search before synthesis so the model cannot skip current evidence collection."""
    search_tools = WebSearchTools(timeout=None, fixed_max_results=None)
    started_at = time.perf_counter()
    try:
        search_results = search_tools.search_news("Bitcoin BTC latest news", max_results=None)
        search_tool_name = "search_news"
        logger.info(
            "News evidence bootstrap completed tool=search_news elapsed_ms=%d result_chars=%d",
            round((time.perf_counter() - started_at) * 1_000),
            len(search_results),
        )
    except Exception:
        logger.exception("News evidence bootstrap search_news failed; trying web_search")
        search_results = search_tools.web_search(f"Bitcoin BTC latest news {prompt}", max_results=None)
        search_tool_name = "web_search"
        logger.info(
            "News evidence bootstrap completed tool=web_search elapsed_ms=%d result_chars=%d",
            round((time.perf_counter() - started_at) * 1_000),
            len(search_results),
        )

    urls = _result_urls(search_results)
    logger.info("News evidence bootstrap opening articles count=%d", len(urls))
    dossier = NewsResearchTools(settings).build_news_dossier(urls) if urls else json.dumps({"items": []})
    context = json.dumps(
        {
            "search_results": json.loads(search_results),
            "article_dossier": json.loads(dossier),
        },
        ensure_ascii=False,
    )
    logger.info(
        "News evidence bootstrap dossier completed urls=%d context_chars=%d elapsed_ms=%d",
        len(urls),
        len(context),
        round((time.perf_counter() - started_at) * 1_000),
    )
    tools = (search_tool_name, "build_news_dossier") if urls else (search_tool_name,)
    return context, tools


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

        if not isinstance(report_response.content, str) or not report_response.content.strip():
            logger.warning(
                "News synthesis returned no Markdown; retrying once run_id=%s content=%r",
                report_response.run_id,
                report_response.content,
            )
            repair_prompt = (
                f"{research_prompt}\n\n"
                "Your previous response was empty. Return the completed evidence-based analysis now as normal "
                "Markdown, with clickable source links and explicit uncertainty. Do not return JSON."
            )
            report_response = analyst.run(
                repair_prompt,
                session_id=effective_session_id,
                user_id=effective_user_id,
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
