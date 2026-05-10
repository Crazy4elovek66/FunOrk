"""Подбор безопасного направления Kwork для лота FunPay."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from app.models import RiskLevel, ScrapedItem
from app.rules.loader import load_funpay_types, load_mapping_rules


def map_to_kwork(item: ScrapedItem, risk_level: RiskLevel) -> dict[str, Any] | None:
    """Возвращает данные маппинга Kwork или None для красных лотов."""

    if risk_level == "RED":
        return None

    text = _normalize_text(" ".join(filter(None, (item.title, item.description))))
    mapping_rules = load_mapping_rules()

    for type_key, type_data in load_funpay_types().items():
        markers = type_data.get("markers", []) if isinstance(type_data, dict) else []
        if not any(_contains_marker(text, str(marker)) for marker in markers):
            continue

        mapping = mapping_rules.get(type_key)
        if not isinstance(mapping, dict):
            continue

        mapping_risk = _normalize_risk(mapping.get("risk"))
        if mapping_risk == "RED":
            return None
        if risk_level == "GREEN" and mapping_risk == "YELLOW":
            continue

        return _build_mapping_result(type_key, mapping)

    fallback = mapping_rules.get("other")
    if isinstance(fallback, dict):
        return _build_mapping_result("other", fallback)

    return None


def _build_mapping_result(normalized_type: str, mapping: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(mapping)
    result["normalized_type"] = normalized_type

    templates = result.get("templates") or []
    categories = result.get("kwork_categories") or []
    result["service_title"] = templates[0] if templates else None
    result["kwork_category"] = categories[0] if categories else None
    return result


def _normalize_risk(value: object) -> RiskLevel:
    normalized = str(value or "yellow").upper()
    if normalized == "GREEN":
        return "GREEN"
    if normalized == "RED":
        return "RED"
    return "YELLOW"


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _contains_marker(text: str, marker: str) -> bool:
    normalized_marker = _normalize_text(marker)
    if not normalized_marker:
        return False
    return normalized_marker in text
