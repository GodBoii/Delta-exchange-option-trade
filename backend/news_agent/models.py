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
    title: str = Field(min_length=1, max_length=500)
    url: str = Field(min_length=8, max_length=2_000)
    publisher: str | None = Field(default=None, max_length=200)
    published_at: str | None = Field(default=None, max_length=100)
    source_class: Literal[
        "primary_official",
        "official_organization",
        "licensed_financial_news",
        "established_news",
        "aggregator",
        "unknown",
    ]
    evidence_used: str = Field(min_length=1, max_length=1_000)


class NewsImageReference(StrictModel):
    image_url: str = Field(min_length=8, max_length=2_000)
    source_page_url: str = Field(min_length=8, max_length=2_000)
    alt_text: str | None = Field(default=None, max_length=500)
    caption: str | None = Field(default=None, max_length=1_000)
    width: int | None = Field(default=None, ge=1, le=50_000)
    height: int | None = Field(default=None, ge=1, le=50_000)


class DirectionalAssessment(StrictModel):
    asset: Literal["BTC", "US_EQUITIES", "USD", "US_RATES", "GOLD", "CRYPTO_BROAD"]
    direction: Literal["bullish", "bearish", "neutral", "mixed", "uncertain"]
    confidence: float = Field(ge=0, le=1)
    mechanisms: list[str] = Field(default_factory=list, max_length=8)


class EventAssessment(StrictModel):
    headline: str = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1, max_length=2_000)
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
    entities: list[str] = Field(default_factory=list, max_length=20)
    novelty: float = Field(ge=0, le=1)
    btc_relevance: float = Field(ge=0, le=1)
    volatility_impact: Literal["low", "moderate", "high", "extreme", "uncertain"]
    expected_horizon: Literal["minutes", "hours", "days", "weeks", "uncertain"]
    directional_assessments: list[DirectionalAssessment] = Field(default_factory=list, max_length=6)
    is_corroborated: bool
    source_urls: list[str] = Field(min_length=1, max_length=10)
    uncertainties: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("volatility_impact", mode="before")
    @classmethod
    def normalize_medium_volatility(cls, value: object) -> object:
        # Smaller free models commonly use the plain-English synonym even when
        # the generated JSON schema names the middle bucket "moderate".
        return _normalize_volatility_label(value)


class NewsAnalysisReport(StrictModel):
    query: str = Field(min_length=1, max_length=1_000)
    analyzed_at: datetime
    executive_summary: str = Field(min_length=1, max_length=4_000)
    events: list[EventAssessment] = Field(default_factory=list, max_length=12)
    aggregate_btc_direction: Literal["bullish", "bearish", "neutral", "mixed", "uncertain"]
    aggregate_volatility_risk: Literal["low", "moderate", "high", "extreme", "uncertain"]
    contradictions: list[str] = Field(default_factory=list, max_length=10)
    missing_information: list[str] = Field(default_factory=list, max_length=10)
    sources: list[SourceReference] = Field(default_factory=list, max_length=20)
    images: list[NewsImageReference] = Field(default_factory=list, max_length=12)
    risk_notice: str = Field(
        default="News analysis is probabilistic research, not a trade instruction or execution signal.",
        max_length=500,
    )

    @field_validator("aggregate_volatility_risk", mode="before")
    @classmethod
    def normalize_medium_volatility(cls, value: object) -> object:
        return _normalize_volatility_label(value)
