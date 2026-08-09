"""Isolated Agno prototype for evidence-based news analysis."""

from .agent import create_news_agent
from .models import NewsAnalysisReport

__all__ = ["NewsAnalysisReport", "create_news_agent"]
