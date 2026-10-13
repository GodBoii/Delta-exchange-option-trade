"""Present read-only tool evidence at the provider boundary, retaining raw audit results."""

import json
from typing import Any

from agno.models.message import Message
from agno.models.openrouter import OpenRouter

from .presentation import markdown_evidence

EVIDENCE_TOOLS = {
    "get_btc_market_packet": "BTC market evidence",
    "get_eth_market_packet": "ETH market evidence",
    "show_available_strategy": "Saved strategy catalogue",
    "preview_selected_strategy": "Assigned option evidence",
    "curate_public_sources": "Public sources",
    "search_public_discussion": "Public discussion",
    "search_exchange_announcements": "Exchange announcements",
    "search_news": "News search",
    "web_search": "Web search",
    "read_news_article": "Original article",
    "build_news_dossier": "News dossier",
}


class EvidenceOpenRouter(OpenRouter):
    def _format_message(self, message: Message, compress_tool_results: bool = False) -> dict[str, Any]:
        payload = super()._format_message(message, compress_tool_results)
        title = EVIDENCE_TOOLS.get(message.tool_name or "")
        content = payload.get("content")
        if message.role == "tool" and title and not message.tool_call_error and isinstance(content, str):
            try:
                evidence = json.loads(content)
            except ValueError:
                return payload
            if isinstance(evidence, (dict, list)):
                payload["content"] = markdown_evidence(evidence, title)
        return payload
