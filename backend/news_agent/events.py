"""Conservative story grouping and calendar comparisons with explicit missing context."""

import hashlib
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit


def same_story(left: dict, right: dict) -> bool:
    a = set(re.findall(r"\w+", left.get("title", "").casefold()))
    b = set(re.findall(r"\w+", right.get("title", "").casefold()))
    if len(a) < 4 or len(b) < 4 or len(a & b) / len(a | b) < 0.85:
        return False
    # Repeated monthly releases can have identical headlines.
    if not left.get("event_at") or not right.get("event_at"):
        return bool(set(left.get("source_urls", [])) & set(right.get("source_urls", [])))
    return left["event_at"] == right["event_at"]


def cluster_events(events: list[dict], history: list[dict]) -> list[dict]:
    groups: list[dict] = []
    for event in events:
        event = {**event, "independent_corroboration": "not_established"}
        previous = next((old for old in history if same_story(event, old)), None)
        if previous:
            event["event_id"] = previous["event_id"]
            event["change"] = "status_update" if previous.get("status") != event["status"] else "continuing"
        else:
            event["change"] = "new_to_memory"
        group = next((item for item in groups if same_story(event, item)), None)
        if group is None:
            groups.append(event)
        elif group["status"] == event["status"]:
            identities = {(claim["url"], claim["text"]) for claim in group["claims"]}
            group["claims"].extend(
                claim for claim in event["claims"] if (claim["url"], claim["text"]) not in identities
            )
            group["source_urls"] = sorted(set(group["source_urls"]) | set(event["source_urls"]))
        else:
            # Conflicting statuses remain visible; do not decide by majority or array order.
            groups.append(
                {
                    **event,
                    "change": "conflicting_status",
                    "event_id": hashlib.sha256((event["event_id"] + event["status"]).encode()).hexdigest(),
                }
            )
    for group in groups:
        group["source_domains"] = sorted({urlsplit(url).hostname for url in group["source_urls"]})
        group["source_count"] = len(group["source_urls"])
    return groups


def release_comparison(item: dict) -> dict:
    result = {
        "actual": item.get("actual"),
        "forecast": item.get("forecast"),
        "previous": item.get("previous"),
        "revised_previous": item.get("revised_previous"),
        "unit": item.get("unit"),
        "reference_period": item.get("reference_period"),
        "status": "insufficient_context",
    }
    if not item.get("unit") or not item.get("reference_period"):
        return result
    for prefix in ("actual", "forecast"):
        if (
            item.get(f"{prefix}_unit", item["unit"]) != item["unit"]
            or item.get(f"{prefix}_period", item["reference_period"]) != item["reference_period"]
        ):
            return {**result, "status": "mismatched_context"}
    try:
        actual, forecast = (Decimal(str(item[key])) for key in ("actual", "forecast"))
        if not actual.is_finite() or not forecast.is_finite():
            return result
    except (InvalidOperation, KeyError):
        return result
    return {
        **result,
        "status": "comparable",
        "actual_minus_forecast": str(actual - forecast),
        "delta_unit": "percentage_points" if item["unit"] == "percent" else item["unit"],
    }
