from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _normalize_volatility_label(value: object) -> object:
    if not isinstance(value, str):
        return value
    normalized = value.strip().lower().replace("_", "-").replace(" ", "-")
    return {"medium": "moderate", "medium-high": "high"}.get(normalized, value)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceReference(StrictModel):
    title: str
    url: str
    publisher: str | None = None
    published_at: str | None = None
    source_class: Literal[
        "primary_official",
        "official_organization",
        "licensed_financial_news",
        "established_news",
        "aggregator",
        "unknown",
    ]
    evidence_used: str


class NewsImageReference(StrictModel):
    image_url: str
    source_page_url: str
    alt_text: str | None = None
    caption: str | None = None
    width: int | None = None
    height: int | None = None


class DirectionalAssessment(StrictModel):
    asset: Literal["BTC", "US_EQUITIES", "USD", "US_RATES", "GOLD", "CRYPTO_BROAD"]
    direction: Literal["bullish", "bearish", "neutral", "mixed", "uncertain"]
    confidence: float = Field(ge=0, le=1)
    mechanisms: list[str] = Field(default_factory=list)


class EventAssessment(StrictModel):
    headline: str
    summary: str
    event_type: Literal[
        "monetary_policy",
        "inflation_labor",
        "growth_fiscal",
        "trade_policy",
        "regulation_enforcement",
        "crypto_adoption",
        "crypto_infrastructure",
        "geopolitics",
        "market_structure",
        "corporate_systemic",
        "other",
    ]
    event_status: Literal[
        "rumor",
        "reported",
        "official_statement",
        "announced",
        "signed",
        "effective",
        "delayed",
        "denied",
        "corrected",
        "retracted",
        "unknown",
    ]
    entities: list[str] = Field(default_factory=list)
    novelty: float = Field(ge=0, le=1)
    btc_relevance: float = Field(ge=0, le=1)
    volatility_impact: Literal["low", "moderate", "high", "extreme", "uncertain"]
    expected_horizon: Literal["minutes", "hours", "days", "weeks", "uncertain"]
    directional_assessments: list[DirectionalAssessment] = Field(default_factory=list)
    is_corroborated: bool
    source_urls: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)

    @field_validator("volatility_impact", mode="before")
    @classmethod
    def normalize_medium_volatility(cls, value: object) -> object:
        # Smaller free models commonly use the plain-English synonym even when
        # the generated JSON schema names the middle bucket "moderate".
        return _normalize_volatility_label(value)


class NewsAnalysisReport(StrictModel):
    query: str
    analyzed_at: datetime
    executive_summary: str
    events: list[EventAssessment] = Field(default_factory=list)
    aggregate_btc_direction: Literal["bullish", "bearish", "neutral", "mixed", "uncertain"]
    aggregate_volatility_risk: Literal["low", "moderate", "high", "extreme", "uncertain"]
    contradictions: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    sources: list[SourceReference] = Field(default_factory=list)
    images: list[NewsImageReference] = Field(default_factory=list)
    risk_notice: str = "News analysis is probabilistic research, not a trade instruction or execution signal."

    @field_validator("aggregate_volatility_risk", mode="before")
    @classmethod
    def normalize_medium_volatility(cls, value: object) -> object:
        return _normalize_volatility_label(value)
