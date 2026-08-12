from __future__ import annotations

from dataclasses import dataclass

from agno.db.base import BaseDb
from agno.run.agent import RunOutput

from .agent import create_news_agent
from .config import NewsAgentSettings
from .database import create_session_db
from .models import NewsAnalysisReport


@dataclass(frozen=True, slots=True)
class NewsPipelineResult:
    report_response: RunOutput
    model_id: str
    session_id: str
    user_id: str

    @property
    def report(self) -> NewsAnalysisReport | None:
        content = self.report_response.content
        return content if isinstance(content, NewsAnalysisReport) else None

    @property
    def research_tools(self) -> list[str]:
        return _tool_names(self.report_response)


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


def run_news_pipeline(
    prompt: str,
    *,
    settings: NewsAgentSettings | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    db: BaseDb | None = None,
    debug_mode: bool = False,
) -> NewsPipelineResult:
    """Run one persisted Agno agent with its research tools."""
    settings = settings or NewsAgentSettings.load()
    effective_session_id = session_id or settings.default_session_id
    effective_user_id = user_id or settings.default_user_id

    owns_db = db is None
    session_db = db or create_session_db(settings)
    try:
        analyst = create_news_agent(settings=settings, db=session_db, debug_mode=debug_mode)
        report_response = analyst.run(prompt, session_id=effective_session_id, user_id=effective_user_id)

        return NewsPipelineResult(
            report_response=report_response,
            model_id=analyst.model.id,
            session_id=effective_session_id,
            user_id=effective_user_id,
        )
    finally:
        if owns_db:
            session_db.close()
