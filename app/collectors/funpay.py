"""MVP-парсер публичных страниц категорий FunPay."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from app.collectors.http_client import AntiBanError, HttpClient, HttpClientError
from app.models import ParseStatus, ScrapedItem
from app.selectors.funpay_selectors import (
    BASE_URL,
    LOTS_SELECTORS,
    PRICE_PATTERNS,
    REQUIRED_SELECTORS,
)


def parse_category(url: str) -> list[ScrapedItem]:
    """Загружает категорию FunPay и возвращает структурированные лоты."""

    try:
        html = HttpClient().get_html(url)
    except AntiBanError:
        raise
    except HttpClientError as error:
        return [_failed_item(url, "request_failed", str(error))]

    soup = BeautifulSoup(html, "html.parser")
    selector_error = _validate_required_selectors(soup)
    if selector_error:
        return [_failed_item(url, "parse_failed", selector_error)]

    items: list[ScrapedItem] = []
    for row in soup.select(LOTS_SELECTORS["lot_row"]):
        if not isinstance(row, Tag):
            continue

        item = _parse_lot_row(row, url)
        if item is not None:
            items.append(item)

    if not items:
        return [
            _failed_item(
                url,
                "parse_failed",
                "Селекторы найдены, но валидные лоты не собраны",
            )
        ]

    return items


def _validate_required_selectors(soup: BeautifulSoup) -> str | None:
    for selector_name in REQUIRED_SELECTORS["lots"]:
        selector = LOTS_SELECTORS.get(selector_name)
        if not selector:
            return f"В funpay_selectors не описан обязательный селектор: {selector_name}"
        if not soup.select_one(selector):
            return f"На странице FunPay не найден обязательный селектор {selector_name}: {selector}"
    return None


def _parse_lot_row(row: Tag, category_url: str) -> ScrapedItem | None:
    href = row.get("href")
    if not href:
        link = row.select_one("a[href]")
        href = link.get("href") if isinstance(link, Tag) else None

    lot_url = urljoin(BASE_URL, str(href)) if href else category_url
    title = _extract_text(row, LOTS_SELECTORS["lot_title"]) or row.get_text(
        " ", strip=True
    )
    title = _normalize_spaces(title)
    if not title:
        return None

    price_text = _extract_text(row, LOTS_SELECTORS["lot_price"])
    price, currency = _parse_price(price_text)
    description = _extract_text(row, LOTS_SELECTORS.get("lot_delivery", ""))

    return ScrapedItem(
        source="funpay",
        url=lot_url,
        title=title,
        price=price,
        currency=currency,
        description=description,
        category=category_url,
        parse_status="success",
    )


def _extract_text(row: Tag, selector: str) -> str | None:
    if not selector:
        return None
    element = row.select_one(selector)
    if element is None:
        return None
    return _normalize_spaces(element.get_text(" ", strip=True))


def _parse_price(value: str | None) -> tuple[Decimal | None, str | None]:
    if not value:
        return None, None

    for pattern in PRICE_PATTERNS:
        match = re.search(pattern, value, flags=re.IGNORECASE)
        if not match:
            continue

        raw_price = match.group("price").replace(" ", "").replace(",", ".")
        raw_currency = match.groupdict().get("currency")
        try:
            price = Decimal(raw_price)
        except InvalidOperation:
            return None, _normalize_currency(raw_currency)

        return price, _normalize_currency(raw_currency)

    return None, None


def _normalize_currency(value: str | None) -> str | None:
    if not value:
        return None

    normalized = value.strip().upper()
    if normalized in {"₽", "РУБ", "РУБ.", "RUB"}:
        return "RUB"
    if normalized in {"$", "USD"}:
        return "USD"
    if normalized in {"€", "EUR"}:
        return "EUR"
    return value.strip()


def _normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _failed_item(url: str, status: ParseStatus, error: str) -> ScrapedItem:
    return ScrapedItem(
        source="funpay",
        url=url,
        title="FunPay: ошибка сбора лотов",
        parse_status=status,
        parse_error=error,
    )
