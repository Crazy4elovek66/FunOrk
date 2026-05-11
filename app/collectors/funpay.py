"""MVP-парсер публичных страниц категорий FunPay."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from app.collectors.http_client import AntiBanError, HttpClient, HttpClientError
from app.config import config
from app.models import ParseStatus, ScrapedItem
from app.rules.loader import load_funpay_types
from app.selectors.funpay_selectors import (
    BASE_URL,
    LOTS_SELECTORS,
    PRICE_PATTERNS,
    REQUIRED_SELECTORS,
)


def parse_category(url: str, *, force: bool = False) -> list[ScrapedItem]:
    """Загружает категорию FunPay и возвращает структурированные лоты."""

    client = HttpClient()
    max_pages = max(1, config.MAX_PAGES_PER_RUN)
    current_url = url
    seen_pages: set[str] = set()
    seen_items: set[str] = set()
    items: list[ScrapedItem] = []

    try:
        for _ in range(max_pages):
            normalized_page_url = _normalize_url(current_url)
            if normalized_page_url in seen_pages:
                break
            seen_pages.add(normalized_page_url)

            html = client.get_html(current_url, source="funpay", force=force)
            soup = BeautifulSoup(html, "html.parser")
            selector_error = _validate_required_selectors(soup)
            if selector_error:
                if not items:
                    return [_failed_item(current_url, "parse_failed", selector_error)]
                break

            items.extend(_parse_items_from_page(soup, current_url, seen_items))

            next_url = _find_next_page_url(soup, current_url, seen_pages)
            if not next_url:
                break
            current_url = next_url
    except AntiBanError:
        raise
    except HttpClientError as error:
        if items:
            return items
        return [_failed_item(current_url, "request_failed", str(error))]

    if not items:
        return [
            _failed_item(
                url,
                "parse_failed",
                "Селекторы найдены, но валидные лоты не собраны.",
            )
        ]

    return items


def _parse_items_from_page(
    soup: BeautifulSoup,
    page_url: str,
    seen_items: set[str],
) -> list[ScrapedItem]:
    items: list[ScrapedItem] = []
    for row in soup.select(LOTS_SELECTORS["lot_row"]):
        if not isinstance(row, Tag):
            continue
        if _is_pagination_link(row):
            continue

        item = _parse_lot_row(row, page_url)
        if item is None:
            continue

        item_key = _normalize_url(item.url)
        if item_key in seen_items:
            continue
        seen_items.add(item_key)
        items.append(item)
    return items


def _is_pagination_link(row: Tag) -> bool:
    href = str(row.get("href") or "")
    rel = " ".join(row.get("rel", [])).casefold()
    class_name = " ".join(row.get("class", [])).casefold()
    text = _normalize_spaces(row.get_text(" ", strip=True)).casefold()
    if "next" in rel or "pagination" in class_name or "pager" in class_name:
        return True
    if "page=" in href and text in {"", ">", "следующая"}:
        return True
    return False


def _validate_required_selectors(soup: BeautifulSoup) -> str | None:
    for selector_name in REQUIRED_SELECTORS["lots"]:
        selector = LOTS_SELECTORS.get(selector_name)
        if not selector:
            return f"В funpay_selectors не описан обязательный селектор: {selector_name}"
        if not soup.select_one(selector):
            return (
                "На странице FunPay не найден обязательный селектор "
                f"{selector_name}: {selector}"
            )
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
    title, subcategory = _split_title_subcategory(title)

    price_text = _extract_text(row, LOTS_SELECTORS["lot_price"])
    price, currency = _parse_price(price_text)
    description = _extract_text(row, LOTS_SELECTORS.get("lot_delivery", ""))
    features = _detect_features(row, title, description)

    return ScrapedItem(
        source="funpay",
        url=lot_url,
        title=title,
        price=price,
        currency=currency,
        description=description,
        category=_extract_category_name(row, category_url),
        subcategory=subcategory,
        requires_login_password=features["requires_login_password"],
        can_be_done_by_id=features["can_be_done_by_id"],
        is_code_or_key=features["is_code_or_key"],
        is_subscription=features["is_subscription"],
        is_service=features["is_service"],
        parse_status="success",
    )


def collect_catalog(
    base_url: str = BASE_URL,
    *,
    force: bool = False,
    limit: int = 100,
) -> list[str]:
    """Собирает ссылки на публичные категории FunPay с главной страницы."""

    html = HttpClient().get_html(base_url, source="funpay", force=force)
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    seen: set[str] = set()
    for link in soup.select("a[href]"):
        href = link.get("href")
        if not href:
            continue
        url = urljoin(BASE_URL, str(href))
        if "/lots/" not in url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= limit:
            break
    return urls


def _split_title_subcategory(title: str) -> tuple[str, str | None]:
    parts = [_normalize_spaces(part) for part in title.rsplit(",", 1)]
    if len(parts) != 2:
        return title, None
    name, maybe_subcategory = parts
    if not name or not maybe_subcategory:
        return title, None
    if len(maybe_subcategory) > 40:
        return title, None
    return name, maybe_subcategory


def _extract_category_name(row: Tag, category_url: str) -> str:
    candidates: list[str] = []
    for selector in (".tc-server", ".tc-game", ".game-title", ".breadcrumb a", ".breadcrumbs a"):
        text = _extract_text(row, selector)
        if text:
            candidates.append(text)
    for candidate in candidates:
        cleaned = _normalize_spaces(candidate)
        if cleaned and not _looks_like_price(cleaned):
            return cleaned
    slug = category_url.rstrip("/").split("/")[-1]
    return f"FunPay {slug}" if slug else "FunPay"


def _detect_features(row: Tag, title: str, description: str | None) -> dict[str, bool]:
    text = _normalize_spaces(
        " ".join(filter(None, (title, description, row.get_text(" ", strip=True))))
    ).casefold()

    type_markers = _matched_type_markers(text)
    requires_login_password = _requires_credentials(text)
    can_be_done_by_id = _contains_any(
        text,
        (
            "по id",
            "по айди",
            "id",
            "uid",
            "ник",
            "nickname",
        ),
    )

    return {
        "requires_login_password": requires_login_password,
        "can_be_done_by_id": can_be_done_by_id,
        "is_code_or_key": bool({"keys", "gift_cards"} & type_markers)
        or _contains_any(text, ("ключ", "код", "key", "code", "gift card")),
        "is_subscription": "subscription" in type_markers
        or _contains_any(text, ("подписка", "subscription", "premium")),
        "is_service": "services" in type_markers
        or _contains_any(
            text,
            (
                "услуга",
                "настройка",
                "помощь",
                "консультация",
                "service",
                "setup",
                "coaching",
            ),
        ),
    }


def _matched_type_markers(text: str) -> set[str]:
    matched: set[str] = set()
    for type_key, type_data in load_funpay_types().items():
        markers = type_data.get("markers", []) if isinstance(type_data, dict) else []
        if any(_contains_marker(text, str(marker)) for marker in markers):
            matched.add(str(type_key))
    return matched


def _requires_credentials(text: str) -> bool:
    if _contains_any(
        text,
        (
            "без пароля",
            "без логина",
            "без доступа",
            "пароль не нужен",
            "логин не нужен",
            "no password",
            "without password",
            "without login",
        ),
    ):
        return False
    return _contains_any(
        text,
        (
            "логин",
            "пароль",
            "лог:пас",
            "лог:пасс",
            "лог пас",
            "лог пасс",
            "родная почта",
            "фулл доступ",
            "полный доступ",
            "с почтой",
            "аккаунт",
            "account",
            "login",
            "password",
        ),
    )


def _find_next_page_url(
    soup: BeautifulSoup,
    current_url: str,
    seen_pages: set[str],
) -> str | None:
    current = _normalize_url(current_url)
    selectors = (
        "a[rel='next']",
        ".pagination a.next",
        ".pagination a[href]",
        "a[href*='page=']",
    )
    for selector in selectors:
        for link in soup.select(selector):
            if not isinstance(link, Tag):
                continue
            href = link.get("href")
            if not href:
                continue
            text = _normalize_spaces(link.get_text(" ", strip=True)).casefold()
            class_name = " ".join(link.get("class", [])).casefold()
            rel = " ".join(link.get("rel", [])).casefold()
            if "next" not in rel and "next" not in class_name:
                if text not in {"", ">", "следующая"} and "page=" not in str(href):
                    continue
            next_url = _normalize_url(urljoin(current_url, str(href)))
            if next_url != current and next_url not in seen_pages:
                return next_url
    return None


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


def _looks_like_price(value: str) -> bool:
    return bool(re.search(r"\d", value) and re.search(r"(₽|руб|rub|usd|eur|\$|€)", value, re.IGNORECASE))


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


def _normalize_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(_contains_marker(text, marker) for marker in markers)


def _contains_marker(text: str, marker: str) -> bool:
    normalized_marker = _normalize_spaces(marker).casefold()
    if not normalized_marker:
        return False
    if len(normalized_marker) <= 3:
        return normalized_marker in set(re.findall(r"[a-zа-яё0-9]+", text))
    return normalized_marker in text


def _failed_item(url: str, status: ParseStatus, error: str) -> ScrapedItem:
    return ScrapedItem(
        source="funpay",
        url=url,
        title="FunPay: ошибка сбора лотов",
        parse_status=status,
        parse_error=error,
    )
