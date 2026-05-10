"""Классификация рисков для собранных лотов."""

from __future__ import annotations

import re

from app.models import RiskLevel, ScrapedItem
from app.rules.loader import load_funpay_types, load_kwork_prohibited


def analyze_risk(item: ScrapedItem) -> tuple[RiskLevel, str]:
    """Возвращает уровень риска и человекочитаемую причину."""

    text = _normalize_text(" ".join(filter(None, (item.title, item.description))))
    prohibited = load_kwork_prohibited()

    for word in prohibited.get("forbidden_words", []):
        if _contains_marker(text, str(word)):
            return "RED", f"Найдено запрещенное слово: {word}"

    matched_types: list[tuple[str, str, str]] = []
    for type_key, type_data in load_funpay_types().items():
        markers = type_data.get("markers", []) if isinstance(type_data, dict) else []
        for marker in markers:
            if _contains_marker(text, str(marker)):
                matched_types.append(
                    (
                        type_key,
                        str(type_data.get("risk", "yellow")).lower(),
                        str(type_data.get("ru_name", type_key)),
                    )
                )
                break

    for type_key, risk, ru_name in matched_types:
        if risk == "red":
            return "RED", f"Тип FunPay запрещен для Kwork: {ru_name} ({type_key})"

    for type_key, risk, ru_name in matched_types:
        if risk == "yellow":
            return "YELLOW", f"Требуется ручная проверка типа FunPay: {ru_name} ({type_key})"

    for type_key, risk, ru_name in matched_types:
        if risk == "green":
            return "GREEN", f"Найден безопасный тип FunPay: {ru_name} ({type_key})"

    return "YELLOW", "Тип лота не распознан, нужна ручная проверка"


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _contains_marker(text: str, marker: str) -> bool:
    normalized_marker = _normalize_text(marker)
    if not normalized_marker:
        return False
    return normalized_marker in text
