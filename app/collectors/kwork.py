"""Сбор публичных категорий Kwork через sitemap/catalog и глубокий парсинг листинга."""

from __future__ import annotations

import re
from collections import deque
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

from bs4 import BeautifulSoup, Tag

from app.collectors.http_client import AntiBanError, HttpClient, HttpClientError
from app.config import config
from app.models import KworkCategory, ParseStatus, ScrapedItem
from app.selectors.kwork_selectors import BLOCKED_URL_PARTS, KWORK_CARD_SELECTORS


KWORK_CATEGORIES_SITEMAP_URL = "https://kwork.ru/sitemap/market_ru/sitemap_categories.xml"


def collect_catalog(
    *,
    force: bool = False,
    limit: int = 100,
) -> tuple[list[KworkCategory], list[ScrapedItem]]:
    urls = _collect_sitemap_urls(force=force, limit=limit)
    if not urls:
        urls = _collect_catalog_urls(force=force, limit=limit)

    categories: list[KworkCategory] = []
    items: list[ScrapedItem] = []
    for url in urls[:limit]:
        category, category_items = parse_kwork_category(url, force=force)
        categories.append(category)
        items.extend(category_items)
    return categories, items


def parse_kwork_category(
    url: str,
    *,
    force: bool = False,
    max_pages: int | None = None,
) -> tuple[KworkCategory, list[ScrapedItem]]:
    """Загружает листинг Kwork и возвращает метрики конкуренции по категории."""

    client = HttpClient()
    pages_limit = max(1, min(max_pages or config.MAX_PAGES_PER_RUN, 5))
    current_url = url
    seen_pages: set[str] = set()
    prices: list[Decimal] = []
    keywords: list[str] = []
    items: list[ScrapedItem] = []
    competitors_count = 0
    category_name: str | None = None

    try:
        for _ in range(pages_limit):
            normalized_page_url = _normalize_url(current_url)
            if normalized_page_url in seen_pages:
                break
            seen_pages.add(normalized_page_url)

            html = client.get_html(current_url, source="kwork", force=force)
            soup = BeautifulSoup(html, "html.parser")
            category_name = category_name or _extract_category_name(soup, url)

            cards = [
                card
                for card in soup.select(KWORK_CARD_SELECTORS["kwork_card"])
                if isinstance(card, Tag) and _looks_like_kwork_card(card)
            ]
            competitors_count += len(cards)

            for card in cards:
                price = _parse_price(
                    _extract_text(card, KWORK_CARD_SELECTORS["kwork_price"])
                )
                if price is not None:
                    prices.append(price)
                title = _extract_text(card, KWORK_CARD_SELECTORS["kwork_title"])
                item_url = _extract_card_url(
                    card,
                    KWORK_CARD_SELECTORS["kwork_url"],
                    current_url,
                )
                if title and item_url:
                    items.append(
                        ScrapedItem(
                            source="kwork",
                            url=item_url,
                            title=title,
                            price=price,
                            category=category_name or _category_name_from_url(url),
                            parse_status="success",
                        )
                    )
                keywords.extend(
                    _extract_keywords(card, KWORK_CARD_SELECTORS["kwork_keywords"])
                )

            keywords.extend(
                _extract_keywords(soup, KWORK_CARD_SELECTORS["filter_keywords"])
            )
            next_url = _find_next_page_url(soup, current_url, seen_pages)
            if not next_url:
                break
            current_url = next_url
    except AntiBanError:
        raise
    except HttpClientError as error:
        return _failed_category(url, "request_failed", str(error)), []

    if competitors_count == 0:
        return (
            _failed_category(
                url,
                "parse_failed",
                "Карточки не найдены. Возможно, изменилась верстка сайта или "
                "требуется проверка браузера (капча).",
                category_name=category_name,
            ),
            [],
        )

    return (
        KworkCategory(
            category_name=category_name or _category_name_from_url(url),
            url=url,
            average_price=_average_price(prices),
            min_price=min(prices) if prices else None,
            competitors_count=competitors_count,
            keywords=_unique_keywords(keywords),
            parse_status="success",
        ),
        items,
    )


def _collect_sitemap_urls(*, force: bool, limit: int) -> list[str]:
    client = HttpClient()
    sitemap_urls = (KWORK_CATEGORIES_SITEMAP_URL, config.KWORK_SITEMAP_URL)

    for sitemap_url in sitemap_urls:
        try:
            xml = client.get_html(sitemap_url, source="kwork", force=force)
        except Exception:
            continue

        try:
            root = ElementTree.fromstring(xml)
        except ElementTree.ParseError:
            continue

        urls = _extract_category_urls_from_sitemap(root, limit=limit)
        if urls:
            return urls

    return []


def _collect_catalog_urls(*, force: bool, limit: int) -> list[str]:
    client = HttpClient()
    queue: deque[str] = deque([config.KWORK_BASE_URL])
    seen: set[str] = set()
    leaf_categories: list[str] = []

    while queue and len(leaf_categories) < limit:
        current_url = _normalize_url(queue.popleft())
        if current_url in seen:
            continue
        seen.add(current_url)

        try:
            html = client.get_html(current_url, source="kwork", force=force)
        except (AntiBanError, HttpClientError):
            raise
        except Exception:
            continue

        soup = BeautifulSoup(html, "html.parser")
        cards = [
            card
            for card in soup.select(KWORK_CARD_SELECTORS["kwork_card"])
            if isinstance(card, Tag) and _looks_like_kwork_card(card)
        ]
        if cards and _is_category_url(current_url):
            leaf_categories.append(current_url)
            continue

        for category_url in _extract_category_links(soup, current_url):
            if category_url not in seen:
                queue.append(category_url)

    return leaf_categories


def _extract_category_urls_from_sitemap(
    root: ElementTree.Element,
    *,
    limit: int,
) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for element in root.iter():
        if not element.tag.endswith("loc") or not element.text:
            continue
        url = _normalize_url(element.text.strip())
        if not _is_category_url(url) or url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= limit:
            break
    return urls


def _extract_category_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for link in soup.select("a[href]"):
        href = link.get("href")
        if not href:
            continue
        url = _normalize_url(urljoin(base_url, str(href)))
        if not _is_category_url(url) or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def _is_category_url(url: str) -> bool:
    parts = urlsplit(url)
    if parts.netloc and parts.netloc != "kwork.ru":
        return False
    if not parts.path.startswith("/categories/"):
        return False
    if any(blocked in parts.path for blocked in BLOCKED_URL_PARTS):
        return False
    return True


def _category_from_url(url: str) -> KworkCategory:
    return KworkCategory(
        category_name=_category_name_from_url(url),
        url=url,
        competitors_count=None,
        parse_status="success",
    )


def _category_name_from_url(url: str) -> str:
    slug = url.rstrip("/").split("/")[-1]
    return re.sub(r"[-_]+", " ", slug).strip().title() or "Категория Kwork"


def _extract_category_name(soup: BeautifulSoup, url: str) -> str:
    for selector in ("h1", "[class*='page-title']", "[class*='category-title']"):
        title = soup.select_one(selector)
        if title:
            text = _normalize_spaces(title.get_text(" ", strip=True))
            if text:
                return text
    if soup.title and soup.title.string:
        title_text = _normalize_spaces(soup.title.string.split("|")[0])
        if title_text:
            return title_text
    return _category_name_from_url(url)


def _looks_like_kwork_card(card: Tag) -> bool:
    if card.get("data-kwork-id"):
        return True
    if card.select_one(KWORK_CARD_SELECTORS["kwork_price"]):
        return True
    hrefs = " ".join(str(link.get("href") or "") for link in card.select("a[href]"))
    return "/kwork/" in hrefs or "/offer/" in hrefs


def _extract_text(parent: Tag, selector: str) -> str | None:
    element = parent.select_one(selector) if selector else None
    if element is None:
        return None
    return _normalize_spaces(element.get_text(" ", strip=True))


def _extract_card_url(parent: Tag, selector: str, base_url: str) -> str | None:
    elements = parent.select(selector) if selector else []
    if not elements:
        return None

    fallback_href: str | None = None
    for element in elements:
        if not isinstance(element, Tag):
            continue
        href = element.get("href")
        if not href:
            continue
        href = str(href)
        if "/kwork/" in href or "/offer/" in href:
            return _normalize_url(urljoin(base_url, href))
        fallback_href = fallback_href or href

    href = fallback_href
    if not href:
        return None
    return _normalize_url(urljoin(base_url, href))


def _extract_keywords(parent: Tag | BeautifulSoup, selector: str) -> list[str]:
    values: list[str] = []
    for element in parent.select(selector):
        text = _normalize_spaces(element.get_text(" ", strip=True))
        if 2 <= len(text) <= 40 and not _looks_like_price(text):
            values.append(text)
    return values


def _parse_price(value: str | None) -> Decimal | None:
    if not value:
        return None
    match = re.search(r"(\d[\d\s.,]*)", value)
    if not match:
        return None
    raw = match.group(1).replace("\u00a0", " ").replace(" ", "").replace(",", ".")
    try:
        price = Decimal(raw)
    except InvalidOperation:
        return None
    return price if price >= 0 else None


def _looks_like_price(value: str) -> bool:
    return bool(
        re.search(r"\d", value)
        and re.search(r"(руб|₽|kwork|от)", value, re.IGNORECASE)
    )


def _find_next_page_url(
    soup: BeautifulSoup,
    current_url: str,
    seen_pages: set[str],
) -> str | None:
    current = _normalize_url(current_url)
    candidates: list[str] = []
    for link in soup.select(KWORK_CARD_SELECTORS["pagination_next"]):
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
            candidates.append(next_url)
    return candidates[0] if candidates else None


def _normalize_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def _average_price(prices: list[Decimal]) -> Decimal | None:
    if not prices:
        return None
    return (sum(prices) / Decimal(len(prices))).quantize(Decimal("0.01"))


def _unique_keywords(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_spaces(value)
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        result.append(normalized)
        if len(result) >= 50:
            break
    return result


def _normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _failed_category(
    url: str,
    status: ParseStatus,
    error: str,
    *,
    category_name: str | None = None,
) -> KworkCategory:
    return KworkCategory(
        category_name=category_name or _category_name_from_url(url),
        url=url,
        competitors_count=0,
        keywords=[],
        parse_status=status,
        parse_error=error,
    )
