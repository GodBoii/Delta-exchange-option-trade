from datetime import date, datetime
from typing import Literal
from uuid import UUID

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

    @model_validator(mode="after")
    def validate_timezones(self) -> "EntrySettings":
        if self.entryAt.utcoffset() is None or self.exitAt.utcoffset() is None:
            raise ValueError("Entry and exit times must include a timezone")
        return self


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
    role: str | None = Field(default=None, max_length=40)
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
        return self


class StrategyDefinition(StrictModel):
    schemaVersion: Literal[2] = 2
    version: int = Field(default=1, ge=1)
    name: str = Field(min_length=2, max_length=80)
    description: str = Field(default="", max_length=500)
    category: Literal["premium_buying", "premium_selling", "defined_risk_premium_selling"] = "premium_selling"
    marketOutlook: Literal[
        "bullish",
        "bearish",
        "large_move_unknown_direction",
        "very_large_move_unknown_direction",
        "sideways",
        "wide_sideways",
        "tight_sideways",
    ] = "sideways"
    enabledForAi: bool = False
    instrument: Instrument
    entry: EntrySettings
    holdingMode: Literal["intraday", "hold_to_expiry"] = "intraday"
    expiryPolicy: Literal["same_day", "next_day", "7_day", "30_day"] = "same_day"
    exitMinutesBeforeExpiry: int = Field(default=5, ge=1, le=1_440)
    sameExpiryRequired: bool = True
    squareOff: Literal["partial", "complete"] = "complete"
    riskMode: Literal["legwise", "combined_premium", "strategy_level"] = "legwise"
    riskBasis: Literal["net_debit", "net_credit", "defined_max_loss"] = "net_credit"
    stopLossPercent: float = Field(default=100, gt=0, le=100)
    takeProfitPercent: float = Field(default=50, gt=0, le=1_000)
    combinedStopLossPercent: float | None = Field(default=None, gt=0, le=100)
    emergencyStopLossPercent: float | None = Field(default=None, gt=0, le=5000)
    emergencyExitEnabled: bool = True
    trailToBreakEven: bool = False
    breakEvenScope: Literal["all_legs", "stop_loss_legs"] = "all_legs"
    overallTarget: PositiveFloat | None = None
    overallStopLoss: PositiveFloat | None = None
    allocationMode: Literal["one_of_three_account_slots"] = "one_of_three_account_slots"
    lotsMode: Literal["auto", "manual"] = "manual"
    maximumLots: int | None = Field(default=None, ge=1, le=100_000)
    equalLotsRequired: bool = False
    legs: list[StrategyLeg] = Field(min_length=1, max_length=12)
    acknowledgement: Literal[True]

    @model_validator(mode="before")
    @classmethod
    def hydrate_legacy_definition(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        hydrated = dict(value)
        hydrated.pop("selectionCriteria", None)
        legs = hydrated.get("legs")
        has_short_leg = isinstance(legs, list) and any(
            isinstance(leg, dict) and leg.get("position") == "sell" for leg in legs
        )
        hydrated.setdefault("riskBasis", "net_credit" if has_short_leg else "net_debit")
        if "combinedStopLossPercent" in hydrated and "stopLossPercent" not in hydrated:
            hydrated["stopLossPercent"] = hydrated["combinedStopLossPercent"]
        return hydrated

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
        if self.sameExpiryRequired and len({leg.expiry for leg in self.legs}) > 1:
            raise ValueError("Every leg must use the same expiry")
        if self.equalLotsRequired and len({leg.lots for leg in self.legs}) > 1:
            raise ValueError("Every leg must use equal lots")
        if self.riskBasis == "net_debit" and any(leg.position == "sell" for leg in self.legs):
            raise ValueError("Net-debit strategies cannot contain short legs in this version")
        if self.riskBasis in {"net_credit", "defined_max_loss"} and not any(
            leg.position == "sell" for leg in self.legs
        ):
            raise ValueError("Credit strategies require at least one short leg")
        short_legs = [leg for leg in self.legs if leg.position == "sell"]
        if self.riskMode == "legwise" and any(leg.stopLoss is None for leg in short_legs):
            raise ValueError("Short option legs require a stop loss in legwise mode")
        if self.riskMode == "combined_premium":
            if self.combinedStopLossPercent is None:
                raise ValueError("Combined premium mode requires a combined stop loss percentage")
            if len(short_legs) < 2:
                raise ValueError("Combined premium mode requires at least two short legs")
        if self.riskMode == "strategy_level" and self.squareOff != "complete":
            raise ValueError("Strategy-level risk requires complete square off")
        return self


class ConnectRequest(StrictModel):
    apiKey: str = Field(min_length=16, max_length=128)
    apiSecret: str = Field(min_length=24, max_length=256)


class SaveStrategyRequest(StrictModel):
    strategy: StrategyDefinition
    status: Literal["draft", "scheduled"]
    savedStrategyId: UUID | None = None


class CancelOrderRequest(StrictModel):
    productId: int = Field(gt=0)
    confirm: Literal[True]


class ClosePositionRequest(StrictModel):
    confirm: Literal[True]
