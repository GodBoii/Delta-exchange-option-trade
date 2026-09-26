"""Small in-memory stand-ins for the local application-data operations used by the engine."""

from typing import Any


class FakeApplicationData:
    def __init__(
        self, saved: list[dict[str, Any]] | None = None, capital: dict[str, Any] | None = None
    ) -> None:
        self.saved = saved or []
        self.capital = capital or {"allocation_mode": "half_balance", "capital_amount": None}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def request(self, path: str, args: dict[str, Any], *, mutation: bool = False) -> Any:
        self.calls.append((path, args))
        if path == "accounts:executionGroupsForUsers":
            return [{"userId": user_id, "accountId": user_id} for user_id in args["userIds"]]
        if path == "library:getCapital":
            return {"user_id": args["userId"], **self.capital}
        if path == "library:setCapital":
            self.capital = {key: args["value"][key] for key in ("allocation_mode", "capital_amount")}
            return None
        raise AssertionError(f"Unexpected application-data operation: {path}")

    async def saved_strategies(self, user_id: str, strategy_id: str | None = None) -> list[dict[str, Any]]:
        return [row for row in self.saved if strategy_id is None or row["id"] == strategy_id]
