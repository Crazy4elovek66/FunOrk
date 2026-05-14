"""MVP-парсер публичных страниц категорий FunPay."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Callable
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

ProgressCallback = Callable[[int, int, str], None]


@dataclass(frozen=True)
class FunPayCatalogEntry:
    url: str
    name: str
    text: str
    category_id: str | None = None
    group_id: str | None = None


@dataclass(frozen=True)
class FunPayCatalogIdGroup:
    name: str
    text: str
    category_ids: tuple[str, ...]
    group_id: str | None = None


def parse_category(
    url: str,
    *,
    force: bool = False,
    category_name: str | None = None,
    progress_callback: ProgressCallback | None = None,
) -> list[ScrapedItem]:
    """Загружает категорию FunPay и возвращает структурированные лоты."""

    client = HttpClient()
    max_pages = max(1, config.MAX_PAGES_PER_RUN)
    current_url = url
    seen_pages: set[str] = set()
    seen_items: set[str] = set()
    items: list[ScrapedItem] = []

    try:
        for page_number in range(1, max_pages + 1):
            normalized_page_url = _normalize_url(current_url)
            if normalized_page_url in seen_pages:
                break
            seen_pages.add(normalized_page_url)
            _report_progress(
                progress_callback,
                page_number,
                max_pages,
                f"Читаю страницу FunPay {page_number}/{max_pages}: {current_url}",
            )

            html = client.get_html(current_url, source="funpay", force=force)
            soup = BeautifulSoup(html, "html.parser")
            selector_error = _validate_required_selectors(soup)
            if selector_error:
                if not items:
                    return [_failed_item(current_url, "parse_failed", selector_error)]
                break

            items.extend(_parse_items_from_page(soup, current_url, seen_items, category_name))

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
    category_name: str | None = None,
) -> list[ScrapedItem]:
    items: list[ScrapedItem] = []
    for row in soup.select(LOTS_SELECTORS["lot_row"]):
        if not isinstance(row, Tag):
            continue
        if _is_pagination_link(row):
            continue

        item = _parse_lot_row(row, page_url, category_name=category_name)
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


def _parse_lot_row(
    row: Tag,
    category_url: str,
    *,
    category_name: str | None = None,
) -> ScrapedItem | None:
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

    category_id = extract_lots_category_id(lot_url) or extract_lots_category_id(category_url)

    return ScrapedItem(
        source="funpay",
        url=lot_url,
        title=title,
        price=price,
        currency=currency,
        description=description,
        category=category_name or _extract_category_name(row, category_url),
        category_id=category_id,
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

    return [entry.url for entry in collect_catalog_entries(base_url, force=force, limit=limit)]


def collect_catalog_entries(
    base_url: str = BASE_URL,
    *,
    force: bool = False,
    limit: int = 100,
) -> list[FunPayCatalogEntry]:
    """Собирает категории FunPay вместе с текстом карточки на главной странице."""

    html = HttpClient().get_html(base_url, source="funpay", force=force)
    soup = BeautifulSoup(html, "html.parser")
    entries: list[FunPayCatalogEntry] = []
    seen: set[str] = set()

    title_blocks = [block for block in soup.select(".game-title") if isinstance(block, Tag)]
    if title_blocks:
        for block in title_blocks:
            link = block.select_one("a[href]")
            if not isinstance(link, Tag):
                continue
            entry = _catalog_entry_from_link(link, seen, group_id=_normalize_optional(block.get("data-id")))
            if entry is None:
                continue
            entries.append(entry)
            if len(entries) >= limit:
                break
        return entries

    for link in soup.select("a[href]"):
        if not isinstance(link, Tag):
            continue
        entry = _catalog_entry_from_link(link, seen, group_id=None)
        if entry is None:
            continue
        entries.append(entry)
        if len(entries) >= limit:
            break
    return entries


def collect_catalog_id_groups(
    base_url: str = BASE_URL,
    *,
    force: bool = False,
    limit: int = 10000,
) -> list[FunPayCatalogIdGroup]:
    """Собирает основные и вложенные ID /lots/ для каждой игры FunPay."""

    html = HttpClient().get_html(base_url, source="funpay", force=force)
    soup = BeautifulSoup(html, "html.parser")
    groups: list[FunPayCatalogIdGroup] = []
    seen_group_names: set[str] = set()

    title_blocks = [block for block in soup.select(".game-title") if isinstance(block, Tag)]
    if not title_blocks:
        entries = collect_catalog_entries(base_url, force=force, limit=limit)
        return [
            FunPayCatalogIdGroup(
                name=entry.name,
                text=entry.text,
                category_ids=(entry.category_id,),
                group_id=entry.group_id,
            )
            for entry in entries
            if entry.category_id
        ]

    for block in title_blocks:
        root_link = block.select_one("a[href]")
        if not isinstance(root_link, Tag):
            continue

        root_entry = _catalog_entry_from_link(root_link, set(), group_id=_normalize_optional(block.get("data-id")))
        if root_entry is None or not root_entry.category_id:
            continue

        match_key = _normalize_spaces(root_entry.name).casefold()
        if match_key in seen_group_names:
            continue
        seen_group_names.add(match_key)

        links = _collect_group_lot_links(block)
        ids: list[str] = []
        text_parts = [root_entry.name, root_entry.text]
        for link in links:
            entry = _catalog_entry_from_link(link, set(), group_id=root_entry.group_id)
            if entry is None or not entry.category_id:
                continue
            if entry.category_id not in ids:
                ids.append(entry.category_id)
            if entry.text:
                text_parts.append(entry.text)

        if root_entry.category_id not in ids:
            ids.insert(0, root_entry.category_id)

        groups.append(
            FunPayCatalogIdGroup(
                name=root_entry.name,
                text=_normalize_spaces(" ".join(text_parts)),
                category_ids=tuple(ids),
                group_id=root_entry.group_id,
            )
        )
        if len(groups) >= limit:
            break

    return groups


def _collect_group_lot_links(title_block: Tag) -> list[Tag]:
    links: list[Tag] = []
    seen_urls: set[str] = set()

    def add_links(container: Tag) -> None:
        for link in container.select("a[href]"):
            if not isinstance(link, Tag):
                continue
            href = str(link.get("href") or "")
            url = urljoin(BASE_URL, href)
            if "/lots/" not in url or url in seen_urls:
                continue
            seen_urls.add(url)
            links.append(link)

    add_links(title_block)

    parent = title_block.parent
    if isinstance(parent, Tag) and len(parent.select(".game-title")) == 1:
        add_links(parent)
        return links

    for sibling in title_block.next_siblings:
        if not isinstance(sibling, Tag):
            continue
        if "game-title" in sibling.get("class", []) or sibling.select_one(".game-title"):
            break
        add_links(sibling)

    return links


def _catalog_entry_from_link(
    link: Tag,
    seen: set[str],
    *,
    group_id: str | None,
) -> FunPayCatalogEntry | None:
    href = link.get("href")
    if not href:
        return None
    url = urljoin(BASE_URL, str(href))
    if "/lots/" not in url or url in seen:
        return None
    seen.add(url)
    text = _normalize_spaces(link.get_text(" ", strip=True))
    return FunPayCatalogEntry(
        url=url,
        name=_extract_catalog_name(link, text, url),
        text=text,
        category_id=extract_lots_category_id(url),
        group_id=group_id,
    )


def _normalize_optional(value: object) -> str | None:
    if value is None:
        return None
    normalized = _normalize_spaces(str(value))
    return normalized or None


def _extract_catalog_name(link: Tag, text: str, url: str) -> str:
    if link.parent and isinstance(link.parent, Tag) and "game-title" in link.parent.get("class", []):
        link_text = _normalize_spaces(link.get_text(" ", strip=True))
        if link_text:
            return link_text
    for selector in (".game-title", ".media-user-name", ".inside", "span", "div"):
        candidate = _extract_text(link, selector)
        if candidate:
            return candidate
    if text:
        return text.split(" ", 1)[0] if "\n" not in text else text.splitlines()[0]
    slug = url.rstrip("/").split("/")[-1]
    return slug or "FunPay"


def extract_lots_category_id(url: str) -> str | None:
    match = re.search(r"/lots/(\d+)/?", urlsplit(url).path)
    return match.group(1) if match else None


def _report_progress(
    progress_callback: ProgressCallback | None,
    current: int,
    total: int,
    message: str,
) -> None:
    if progress_callback is not None:
        progress_callback(current, total, message)


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
    for selector in (".tc-game", ".game-title", ".breadcrumb a", ".breadcrumbs a"):
        text = _extract_text(row, selector)
        if text:
            candidates.append(text)
    for candidate in candidates:
        cleaned = _normalize_spaces(candidate)
        if cleaned and not _looks_like_price(cleaned):
            return cleaned
    slug = category_url.rstrip("/").split("/")[-1]
    return f"FunPay {slug}" if slug and not slug.isdigit() else "FunPay"


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
