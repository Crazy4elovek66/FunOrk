"""Ручной импорт CSV/JSON в рабочую SQLite-базу."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable

from app.analyzers.normalizer import clean_text, parse_decimal, parse_int
from app.db import init_db, save_kwork_category, save_scraped_item
from app.models import KworkCategory, ScrapedItem


def import_funpay_file(path: Path | str) -> int:
    init_db()
    rows = _read_rows(Path(path))
    imported = 0
    for row in rows:
        item = ScrapedItem(
            source="funpay",
            url=_required(row, "url"),
            title=_required(row, "title"),
            price=parse_decimal(row.get("price")),
            currency=clean_text(row.get("currency")) or "RUB",
            description=clean_text(row.get("description")),
            category=clean_text(row.get("category")),
            subcategory=clean_text(row.get("subcategory")),
            parse_status="success",
        )
        save_scraped_item(item)
        imported += 1
    return imported


def import_kwork_file(path: Path | str) -> int:
    init_db()
    rows = _read_rows(Path(path))
    imported = 0
    for row in rows:
        if clean_text(row.get("title")):
            item = ScrapedItem(
                source="kwork",
                url=_required(row, "url"),
                title=_required(row, "title"),
                price=parse_decimal(row.get("price")),
                currency=clean_text(row.get("currency")) or "RUB",
                description=clean_text(row.get("description")),
                category=clean_text(row.get("category") or row.get("category_name")),
                subcategory=clean_text(row.get("subcategory") or row.get("subcategory_name")),
                is_service=True,
                parse_status="success",
            )
            save_scraped_item(item)
        else:
            category = KworkCategory(
                category_name=_required(row, "category_name", fallback="category"),
                subcategory_name=clean_text(row.get("subcategory_name") or row.get("subcategory")),
                url=_required(row, "url"),
                average_price=parse_decimal(row.get("average_price")),
                min_price=parse_decimal(row.get("min_price")),
                competitors_count=parse_int(row.get("competitors_count")),
                keywords=_split_keywords(row.get("keywords")),
                parse_status="success",
            )
            save_kwork_category(category)
        imported += 1
    return imported


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Файл импорта не найден: {path}")

    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            return [dict(row) for row in csv.DictReader(file)]
    if suffix == ".json":
        with path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        if isinstance(payload, dict):
            payload = payload.get("items") or payload.get("rows") or []
        if not isinstance(payload, list):
            raise ValueError("JSON импорт должен содержать массив или объект с полем items/rows")
        return [dict(row) for row in payload if isinstance(row, dict)]

    raise ValueError("Поддерживаются только CSV и JSON файлы")


def _required(row: dict[str, Any], key: str, fallback: str | None = None) -> str:
    value = clean_text(row.get(key))
    if value is None and fallback is not None:
        value = clean_text(row.get(fallback))
    if value is None:
        raise ValueError(f"В файле импорта нет обязательного поля: {key}")
    return value


def _split_keywords(value: object | None) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = clean_text(value)
    if not text:
        return []
    return [part.strip() for part in text.replace(";", ",").split(",") if part.strip()]
