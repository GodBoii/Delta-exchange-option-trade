"""Private research calls to the local trading writer."""

from typing import Any

import httpx

from app.application_data import response_value, saved_row
from app.errors import AppError


class LocalResearchClient:
    def __init__(self, backend_url: str, secret: str | None) -> None:
        if not secret:
            raise ValueError("ANALYSIS_SERVICE_SECRET is required for local research calls")
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
            raise AppError(503, "Local application data is unavailable", "application_data_unavailable") from error
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
