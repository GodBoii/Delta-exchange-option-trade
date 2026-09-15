"""Named Convex operations for the migrated strategy library and capital policy."""

import json
from typing import Any

import httpx

from .errors import AppError


def response_value(response: httpx.Response) -> Any:
    try:
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or data.get("status") != "success":
            raise ValueError("Unconfirmed application-data response")
        return data.get("value")
    except (httpx.HTTPError, ValueError) as error:
        raise AppError(503, "Convex application data is unavailable", "application_data_unavailable") from error


def saved_row(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not isinstance(value.get("definitionJson"), str):
        raise AppError(503, "Saved strategy data is invalid", "application_data_invalid")
    definition = json.loads(value["definitionJson"])
    if not isinstance(definition, dict):
        raise AppError(503, "Saved strategy definition is invalid", "application_data_invalid")
    return {
        key: item for key, item in value.items() if key not in {"_id", "_creationTime", "definitionJson", "deleted"}
    } | {"definition_json": definition}


class ConvexApplicationData:
    def __init__(self, url: str, secret: str, client: httpx.AsyncClient | None = None) -> None:
        if not url or not secret:
            raise ValueError("Convex library requires CONVEX_URL and CONVEX_TRADING_SECRET")
        self.url = url.rstrip("/")
        self.secret = secret
        self.client = client

    def body(self, path: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"path": path, "args": {"secret": self.secret, **args}, "format": "json"}

    async def request(self, path: str, args: dict[str, Any], *, mutation: bool = False) -> Any:
        if self.client is None:
            raise RuntimeError("An async HTTP client is required")
        try:
            response = await self.client.post(
                f"{self.url}/api/{'mutation' if mutation else 'query'}", json=self.body(path, args)
            )
        except httpx.HTTPError as error:
            raise AppError(503, "Convex application data is unavailable", "application_data_unavailable") from error
        return response_value(response)

    async def saved_strategies(self, user_id: str, strategy_id: str | None = None) -> list[dict[str, Any]]:
        if strategy_id is not None:
            value = await self.request("library:serverGet", {"userId": user_id, "id": strategy_id})
            return [saved_row(value)] if value is not None else []
        records = []
        for defaults in (False, True):
            cursor = None
            while True:
                page = await self.request(
                    "library:serverList",
                    {"userId": user_id, "defaults": defaults, "paginationOpts": {"numItems": 100, "cursor": cursor}},
                )
                records.extend(saved_row(row) for row in page["page"])
                if page["isDone"]:
                    break
                if cursor == page["continueCursor"]:
                    raise AppError(503, "Library pagination did not advance", "application_data_invalid")
                cursor = page["continueCursor"]
        return records

    def selection_context(
        self, user_id: str, strategy_id: str | None = None
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Synchronous Agno tools read Convex before opening their SQL transaction."""
        with httpx.Client(timeout=httpx.Timeout(15, connect=5)) as client:

            def query(path: str, args: dict[str, Any]) -> Any:
                return response_value(client.post(f"{self.url}/api/query", json=self.body(path, args)))

            capital = query("library:getCapital", {"userId": user_id})
            if strategy_id is not None:
                value = query("library:serverGet", {"userId": user_id, "id": strategy_id})
                return ([saved_row(value)] if value is not None else []), capital
            records = []
            for defaults in (False, True):
                cursor = None
                while True:
                    page = query(
                        "library:serverList",
                        {
                            "userId": user_id,
                            "defaults": defaults,
                            "paginationOpts": {"numItems": 100, "cursor": cursor},
                        },
                    )
                    records.extend(saved_row(row) for row in page["page"])
                    if page["isDone"]:
                        break
                    if cursor == page["continueCursor"]:
                        raise AppError(503, "Library pagination did not advance", "application_data_invalid")
                    cursor = page["continueCursor"]
            return records, capital
