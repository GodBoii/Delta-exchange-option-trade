from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Instrument(StrictModel):
    index: Literal["BTCUSD", "ETHUSD"]
    underlying: Literal["BTC", "ETH"]
    underlyingFrom: Literal["cash", "futures"]


class EntrySettings(StrictModel):
    strategyType: Literal["intraday", "btst", "positional"]
    entryAt: datetime
    exitAt: datetime


class StrategyLeg(StrictModel):
    id: str = Field(min_length=1, max_length=40)
    lots: int = Field(ge=1, le=100_000)
    position: Literal["buy", "sell"]
    optionType: Literal["call", "put"]
    expiry: date
    strikeMode: Literal["atm", "itm", "otm", "exact"]
    strikeSteps: int = Field(default=0, ge=0, le=100)
    exactStrike: PositiveFloat | None = None
    orderType: Literal["market_order", "limit_order"] = "market_order"
    limitPrice: str | None = Field(default=None, pattern=r"^\d+(\.\d+)?$")
    targetProfit: PositiveFloat | None = None
    stopLoss: PositiveFloat | None = None
    trailStop: PositiveFloat | None = None
    reentryOnTarget: int = Field(default=0, ge=0, le=10)
    reentryOnStop: int = Field(default=0, ge=0, le=10)

    @model_validator(mode="after")
    def validate_order(self) -> "StrategyLeg":
        if self.strikeMode == "exact" and self.exactStrike is None:
            raise ValueError("Exact strike is required")
        if self.orderType == "limit_order" and not self.limitPrice:
            raise ValueError("Limit price is required")
        if self.position == "sell" and self.stopLoss is None:
            raise ValueError("Short option legs require a stop loss")
        return self


class StrategyDefinition(StrictModel):
    name: str = Field(min_length=2, max_length=80)
    instrument: Instrument
    entry: EntrySettings
    squareOff: Literal["partial", "complete"] = "complete"
    trailToBreakEven: bool = False
    breakEvenScope: Literal["all_legs", "stop_loss_legs"] = "all_legs"
    overallTarget: PositiveFloat | None = None
    overallStopLoss: PositiveFloat | None = None
    legs: list[StrategyLeg] = Field(min_length=1, max_length=12)
    acknowledgement: Literal[True]

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Strategy name is too short")
        return value

    @model_validator(mode="after")
    def validate_schedule(self) -> "StrategyDefinition":
        if self.entry.exitAt <= self.entry.entryAt:
            raise ValueError("Exit must be after entry")
        return self


class ConnectRequest(StrictModel):
    apiKey: str = Field(min_length=16, max_length=128)
    apiSecret: str = Field(min_length=24, max_length=256)


class SaveStrategyRequest(StrictModel):
    strategy: StrategyDefinition
    status: Literal["draft", "scheduled"]


class CancelOrderRequest(StrictModel):
    productId: int = Field(gt=0)
    confirm: Literal[True]
