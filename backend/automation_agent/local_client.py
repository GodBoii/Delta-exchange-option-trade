"""Private research calls to the trading writer's internal endpoint."""

import json
from typing import Any

import httpx

from app.errors import AppError


def response_value(response: httpx.Response) -> Any:
    try:
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or data.get("status") != "success":
            raise ValueError("Unconfirmed application-data response")
        return data.get("value")
    except httpx.HTTPStatusError as error:
        # Preserve the writer's own rejection so tools can report a correctable reason.
        try:
            detail = error.response.json().get("error") or {}
        except ValueError:
            detail = {}
        if isinstance(detail, dict) and detail.get("code") and error.response.status_code < 500:
            raise AppError(
                error.response.status_code, str(detail.get("message") or detail["code"]), str(detail["code"])
            ) from error
        raise AppError(503, "Trading application data is unavailable", "application_data_unavailable") from error
    except (httpx.HTTPError, ValueError) as error:
        raise AppError(503, "Trading application data is unavailable", "application_data_unavailable") from error


def saved_row(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("definitionJson"), str):
        raise AppError(503, "Saved strategy data is invalid", "application_data_invalid")
    definition = json.loads(value["definitionJson"])
    if not isinstance(definition, dict):
        raise AppError(503, "Saved strategy definition is invalid", "application_data_invalid")
    return {
        key: item for key, item in value.items() if key not in {"_id", "_creationTime", "definitionJson", "deleted"}
    } | {"definition_json": definition}


class LocalResearchClient:
    def __init__(self, backend_url: str, secret: str | None) -> None:
        if not secret:
            raise ValueError("ANALYSIS_SERVICE_SECRET is required for research calls")
        self.url = backend_url.rstrip("/")
        self.secret = secret

    def request_sync(self, path: str, args: dict[str, Any], *, mutation: bool = False) -> Any:
        try:
            with httpx.Client(timeout=httpx.Timeout(30, connect=5)) as client:
                response = client.post(
                    f"{self.url}/internal/research",
                    headers={"X-Analysis-Secret": self.secret},
                    json={"path": path, "args": args, "mutation": mutation},
                )
        except httpx.HTTPError as error:
            raise AppError(503, "Trading application data is unavailable", "application_data_unavailable") from error
        return response_value(response)

    def selection_context(
        self, user_id: str, strategy_id: str | None = None
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        capital = self.request_sync("library:getCapital", {"userId": user_id})
        if strategy_id is not None:
            value = self.request_sync("library:serverGet", {"userId": user_id, "id": strategy_id})
            return ([saved_row(value)] if value is not None else []), capital
        rows = []
        for defaults in (False, True):
            cursor = None
            while True:
                page = self.request_sync("library:serverList", {
                    "userId": user_id, "defaults": defaults,
                    "paginationOpts": {"numItems": 100, "cursor": cursor},
                })
                rows.extend(saved_row(record) for record in page["page"])
                if page["isDone"]:
                    break
                if page["continueCursor"] == cursor:
                    raise AppError(503, "Library pagination did not advance", "application_data_invalid")
                cursor = page["continueCursor"]
        return rows, capital
