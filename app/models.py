"""Типизированные модели данных для обмена между модулями."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


RiskLevel = Literal["GREEN", "YELLOW", "RED"]
ParseStatus = Literal["pending", "success", "parse_failed", "request_failed", "skipped"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ScrapedItem(BaseModel):
    """Минимальная единица публично собранных данных."""

    model_config = ConfigDict(validate_assignment=True)

    id: int | None = None
    source: Literal["funpay", "kwork"]
    url: str
    title: str = Field(..., min_length=1)
    price: Decimal | None = Field(default=None, ge=0)
    currency: str | None = None
    description: str | None = None
    category: str | None = None
    subcategory: str | None = None
    parse_status: ParseStatus = "success"
    parse_error: str | None = None
    scraped_at: datetime = Field(default_factory=utc_now)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("URL должен начинаться с http:// или https://")
        return value


class AnalysisResult(BaseModel):
    """Результат анализа и скоринга для одной записи."""

    model_config = ConfigDict(validate_assignment=True)

    id: int | None = None
    item_id: int
    risk_level: RiskLevel
    score: float = Field(..., ge=0, le=100)
    verdict: str = Field(..., min_length=1)
    is_recommended: bool = False
    risk_reason: str | None = None
    recommendation: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("is_recommended")
    @classmethod
    def red_items_are_not_recommended(
        cls, value: bool, info: object
    ) -> bool:
        data = getattr(info, "data", {})
        if data.get("risk_level") == "RED" and value:
            raise ValueError("Красное направление не может быть рекомендовано")
        return value


class FunPayCategory(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    id: int | None = None
    category_name: str
    subcategory_name: str | None = None
    url: str
    raw_type: str | None = None
    normalized_type: str | None = None
    min_price: Decimal | None = Field(default=None, ge=0)
    median_price: Decimal | None = Field(default=None, ge=0)
    currency: str | None = None
    offers_count: int | None = Field(default=None, ge=0)
    delivery_method: str | None = None
    requires_login_password: bool = False
    can_be_done_by_id: bool = False
    is_code_or_key: bool = False
    is_subscription: bool = False
    is_service: bool = False
    last_checked_at: datetime = Field(default_factory=utc_now)
    parse_status: ParseStatus = "pending"
    parse_error: str | None = None


class KworkCategory(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    id: int | None = None
    category_name: str
    subcategory_name: str | None = None
    url: str
    average_price: Decimal | None = Field(default=None, ge=0)
    min_price: Decimal | None = Field(default=None, ge=0)
    competitors_count: int | None = Field(default=None, ge=0)
    keywords: list[str] = Field(default_factory=list)
    last_checked_at: datetime = Field(default_factory=utc_now)
    parse_status: ParseStatus = "pending"
    parse_error: str | None = None


class Opportunity(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    id: int | None = None
    funpay_category_id: int | None = None
    source_category: str
    source_subcategory: str | None = None
    source_url: str
    normalized_type: str
    possible_kwork_service_title: str | None = None
    possible_kwork_category: str | None = None
    buy_price: Decimal | None = Field(default=None, ge=0)
    sell_price: Decimal | None = Field(default=None, ge=0)
    estimated_margin_percent: float | None = None
    risk_level: RiskLevel
    risk_reason: str
    moderation_risk: str | None = None
    dispute_risk: str | None = None
    demand_weight: float = Field(default=0, ge=0, le=100)
    margin_weight: float = Field(default=0, ge=0, le=100)
    risk_weight: float = Field(default=0, ge=0, le=100)
    opportunity_score: float = Field(default=0, ge=0, le=100)
    verdict: str
    recommendation: str | None = None
    forbidden_words: list[str] = Field(default_factory=list)
    safe_wording: str | None = None
    buyer_requirements: str | None = None
    report_format: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
