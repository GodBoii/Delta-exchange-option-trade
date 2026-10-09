"""Map upstream diagnostics to bounded codes; never retain message bodies."""

ACCESS_ERRORS = frozenset({"authentication_required", "access_blocked"})
INFORMATIONAL_WARNINGS = frozenset({"unsupported_card", "unsupported_media"})


def warning_code(record: dict) -> str:
    module = record.get("name", "")
    message = record.get("message", "")
    if module == "twscrape.models":
        if message.startswith("Unknown card type"):
            return "unsupported_card"
        if message.startswith("Unknown media type"):
            return "unsupported_media"
        return "parser_error"
    if module == "twscrape.api" and "pagination stalled" in message:
        return "pagination_stalled"
    if module in {"twscrape.queue_client", "twscrape.logger"}:
        if any(code in message for code in ("(32)", "(89)", "(326)")):
            return "authentication_required"
        if any(text in message for text in ("Session expired", "Ban detected", "Missing authentication")):
            return "authentication_required"
        if "Blocked by" in message:
            return "access_blocked"
        if "429" in message or "Rate limited" in message:
            return "rate_limited"
        if "API busy" in message:
            return "upstream_busy"
        if "XClId" in message:
            return "client_metadata_error"
        if "cooling account for 60s" in message:
            return "transport_error"
        return "upstream_error"
    return "upstream_warning"
