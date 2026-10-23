"""Summarize observed collection outcomes without equating discovery with verification."""

import json
from typing import Any


def collection_coverage(executions: list[Any]) -> dict[str, Any]:
    health: dict[str, dict] = {}
    for execution in executions:
        name = execution.get("tool_name") if isinstance(execution, dict) else execution.tool_name
        if name != "curate_public_sources":
            continue
        result = execution.get("result") if isinstance(execution, dict) else execution.result
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except ValueError:
                continue
        if not isinstance(result, dict):
            continue
        for row in result.get("source_health") or []:
            if isinstance(row, dict) and isinstance(row.get("source"), str):
                health[row["source"]] = row
    unavailable = sorted(name for name, row in health.items() if row.get("status") != "available")
    return {
        "status": "not_collected" if not health else "partial" if unavailable else "available",
        "checked_sources": len(health),
        "available_sources": sum(row.get("status") == "available" for row in health.values()),
        "limited_sources": [{"source": name, "status": health[name].get("status")} for name in unavailable],
        "note": "Collection availability does not establish that original articles were read or claims verified.",
    }


def coverage_notice(coverage: dict[str, Any]) -> str:
    if coverage["status"] != "partial":
        return ""
    counts: dict[str, int] = {}
    for row in coverage["limited_sources"]:
        status = row["status"] or "unknown"
        counts[status] = counts.get(status, 0) + 1
    details = ", ".join(f"{status.replace('_', ' ')}: {count}" for status, count in sorted(counts.items()))
    return (
        "\n\n## Coverage limitations\n\n"
        f"{coverage['available_sources']} of {coverage['checked_sources']} checked sources "
        "returned available material. "
        f"Other collection outcomes were {details}. Missing coverage cannot establish that no relevant event occurred. "
        "Search leads still require verification from originals."
    )
