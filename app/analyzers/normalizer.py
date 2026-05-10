"""Нормализация текста и чисел для импорта, маппинга и скоринга."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


def normalize_text(value: object | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").casefold()).strip()


def clean_text(value: object | None) -> str | None:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text or None


def parse_decimal(value: object | None) -> Decimal | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    raw = raw.replace("\u00a0", " ").replace(" ", "").replace(",", ".")
    raw = re.sub(r"[^0-9.\-]", "", raw)
    if not raw:
        return None
    try:
        parsed = Decimal(raw)
    except InvalidOperation:
        return None
    return parsed if parsed >= 0 else None


def parse_int(value: object | None) -> int | None:
    decimal = parse_decimal(value)
    if decimal is None:
        return None
    return int(decimal)
