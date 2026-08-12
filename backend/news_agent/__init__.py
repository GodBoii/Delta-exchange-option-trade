"""Isolated Agno prototype for evidence-based news analysis."""

from .agent import create_news_agent
from .pipeline import NewsPipelineResult, run_news_pipeline

__all__ = [
    "NewsPipelineResult",
    "create_news_agent",
    "run_news_pipeline",
]
