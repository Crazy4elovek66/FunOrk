"""Подбор безопасного направления Kwork для лота FunPay."""

from __future__ import annotations

from copy import deepcopy
import sqlite3
from typing import Any

from app.analyzers.normalizer import normalize_text
from app.db import fetch_kwork_categories
from app.models import RiskLevel, ScrapedItem
from app.rules.loader import load_funpay_types, load_mapping_rules


def map_to_kwork(item: ScrapedItem, risk_level: RiskLevel) -> dict[str, Any] | None:
    """Возвращает данные маппинга Kwork или None для красных лотов."""

    if risk_level == "RED":
        return None

    text = normalize_text(" ".join(filter(None, (item.title, item.description))))
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

        return _with_kwork_demand(_build_mapping_result(type_key, mapping))

    fallback = mapping_rules.get("other")
    if isinstance(fallback, dict):
        return _with_kwork_demand(_build_mapping_result("other", fallback))

    return None


def _build_mapping_result(normalized_type: str, mapping: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(mapping)
    result["normalized_type"] = normalized_type

    templates = result.get("templates") or []
    categories = result.get("kwork_categories") or []
    result["service_title"] = templates[0] if templates else None
    result["kwork_category"] = categories[0] if categories else None
    result.setdefault("forbidden_words", [])
    result.setdefault(
        "safe_wording",
        "Описывать услугу как консультацию, настройку, инструкцию или подготовку материалов без передачи доступов.",
    )
    return result


def _with_kwork_demand(result: dict[str, Any]) -> dict[str, Any]:
    category = normalize_text(result.get("kwork_category"))
    if not category:
        return result

    try:
        rows = fetch_kwork_categories()
    except sqlite3.OperationalError:
        return result

    for row in rows:
        if row["parse_status"] != "success":
            continue
        row_category = normalize_text(row["category_name"])
        row_subcategory = normalize_text(row["subcategory_name"])
        if category in {row_category, row_subcategory}:
            result["competitors_count"] = row["competitors_count"]
            result["average_price"] = row["average_price"]
            result["min_price"] = row["min_price"]
            return result
    return result


def _normalize_risk(value: object) -> RiskLevel:
    normalized = str(value or "yellow").upper()
    if normalized == "GREEN":
        return "GREEN"
    if normalized == "RED":
        return "RED"
    return "YELLOW"


def _contains_marker(text: str, marker: str) -> bool:
    normalized_marker = normalize_text(marker)
    if not normalized_marker:
        return False
    return normalized_marker in text
