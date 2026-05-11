"""Сбор публичных категорий и услуг Kwork без обхода защит сайта."""

from __future__ import annotations

import json
import re
from collections import deque
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

from bs4 import BeautifulSoup, Tag
from soupsieve import SelectorSyntaxError

from app.collectors.http_client import AntiBanError, HttpClient, HttpClientError
from app.config import config
from app.models import KworkCategory, ParseStatus, ScrapedItem
from app.selectors.kwork_selectors import BLOCKED_URL_PARTS, KWORK_CARD_SELECTORS


KWORK_CATEGORIES_SITEMAP_URL = "https://kwork.ru/sitemap/market_ru/sitemap_categories.xml"
KWORK_CAPTCHA_ERROR = (
    "Kwork вернул SmartCaptcha. Парсинг requests невозможен. "
    "Добавьте актуальный KWORK_COOKIE или используйте ручной импорт."
)
KWORK_SESSION_ERROR = (
    "KWORK_COOKIE недействительна или Kwork требует капчу. "
    "Пожалуйста, обновите куки в файле .env"
)
KWORK_SERVICE_URL_RE = re.compile(
    r"^(?:https?://kwork\.ru)?/[^/?#]+/\d+/[^/?#]+",
    re.IGNORECASE,
)


class SessionValidationError(RuntimeError):
    """Kwork не подтвердил активную пользовательскую сессию."""


def verify_kwork_session(force: bool = True) -> bool:
    """Проверяет, что Kwork доступен без капчи и видит авторизованного пользователя."""

    client = HttpClient()
    try:
        html = client.get_html(config.KWORK_BASE_URL, source="kwork", force=True)
    except AntiBanError as error:
        html = getattr(error, "html", None)
        if html:
            save_kwork_debug_html(config.KWORK_BASE_URL, html, "pre-flight: Kwork вернул SmartCaptcha")
        return False
    except HttpClientError:
        return False

    if _has_smart_captcha(html):
        save_kwork_debug_html(config.KWORK_BASE_URL, html, "pre-flight: найдена SmartCaptcha")
        return False

    soup = BeautifulSoup(html, "html.parser")
    if _looks_like_authenticated_kwork_session(html, soup):
        return True

    seller_url = urljoin(config.KWORK_BASE_URL.rstrip("/") + "/", "seller")
    try:
        html = client.get_html(seller_url, source="kwork", force=True)
    except AntiBanError as error:
        html = getattr(error, "html", None)
        if html:
            save_kwork_debug_html(seller_url, html, "pre-flight: Kwork вернул SmartCaptcha")
        return False
    except HttpClientError:
        return False

    if _has_smart_captcha(html):
        save_kwork_debug_html(seller_url, html, "pre-flight: найдена SmartCaptcha")
        return False

    return _looks_like_authenticated_kwork_session(html, BeautifulSoup(html, "html.parser"))


def collect_catalog(
    *,
    force: bool = False,
    limit: int = 100,
) -> tuple[list[KworkCategory], list[ScrapedItem]]:
    if not verify_kwork_session(force=True):
        raise SessionValidationError(KWORK_SESSION_ERROR)

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
    """Загружает листинг Kwork и извлекает реальные карточки услуг."""

    client = HttpClient()
    pages_limit = max(1, max_pages or config.KWORK_MAX_PAGES_PER_CATEGORY)
    current_url = url
    seen_pages: set[str] = set()
    prices: list[Decimal] = []
    keywords: list[str] = []
    items: list[ScrapedItem] = []
    category_name: str | None = None
    last_html = ""
    last_reason = "услуги Kwork не найдены"

    try:
        for _ in range(pages_limit):
            normalized_page_url = _normalize_url(current_url)
            if normalized_page_url in seen_pages:
                break
            seen_pages.add(normalized_page_url)

            html = client.get_html(current_url, source="kwork", force=force)
            last_html = html
            soup = BeautifulSoup(html, "html.parser")
            category_name = category_name or _extract_category_name(soup, url)

            if _has_smart_captcha(html):
                save_kwork_debug_html(current_url, html, "найдена SmartCaptcha")
                raise AntiBanError(KWORK_CAPTCHA_ERROR, html=html)

            page_items = _dedupe_items(
                [
                    *extract_kwork_services_from_dom(soup, current_url, category_name),
                    *extract_kwork_services_from_links(soup, current_url, category_name),
                    *extract_kwork_services_from_json_scripts(soup, current_url, category_name),
                ]
            )
            if not page_items:
                last_reason = _empty_page_reason(html, soup)

            for item in page_items:
                if _is_valid_kwork_service(item):
                    items.append(item)
                    if item.price is not None:
                        prices.append(item.price)

            keywords.extend(_extract_keywords(soup, KWORK_CARD_SELECTORS["filter_keywords"]))
            keywords.extend(_extract_keywords(soup, KWORK_CARD_SELECTORS["kwork_keywords"]))
            for item in page_items:
                if item.title:
                    keywords.extend(_keywords_from_text(item.title))

            next_url = _find_next_page_url(soup, current_url, seen_pages)
            if not next_url:
                break
            current_url = next_url
    except AntiBanError as error:
        html = getattr(error, "html", None)
        if html:
            save_kwork_debug_html(current_url, html, "найдена SmartCaptcha")
        raise
    except HttpClientError as error:
        return _failed_category(url, "request_failed", str(error), category_name=category_name), []

    items = _dedupe_items(items)
    if not items:
        if last_html:
            save_kwork_debug_html(url, last_html, last_reason)
        return (
            _failed_category(
                url,
                "parse_failed",
                last_reason,
                category_name=category_name,
            ),
            [],
        )

    return (
        KworkCategory(
            category_name=category_name or _category_name_from_url(url),
            subcategory_name=_subcategory_name_from_url(url, category_name),
            url=url,
            average_price=_average_price(prices),
            min_price=min(prices) if prices else None,
            competitors_count=len(items),
            keywords=_unique_keywords(keywords),
            parse_status="success",
        ),
        items,
    )


def extract_kwork_services_from_dom(
    soup: BeautifulSoup,
    page_url: str,
    category_name: str | None,
) -> list[ScrapedItem]:
    """Извлекает услуги из явных карточек листинга Kwork."""

    services: list[ScrapedItem] = []
    for card in _safe_select(soup, KWORK_CARD_SELECTORS["kwork_card"]):
        if not isinstance(card, Tag) or not _looks_like_kwork_card(card):
            continue
        item = _item_from_container(card, page_url, category_name)
        if item is not None:
            services.append(item)
    return services


def extract_kwork_services_from_links(
    soup: BeautifulSoup,
    page_url: str,
    category_name: str | None,
) -> list[ScrapedItem]:
    """Ищет ссылки на услуги Kwork и поднимается к ближайшему контейнеру карточки."""

    services: list[ScrapedItem] = []
    for link in _safe_select(soup, "a[href]"):
        if not isinstance(link, Tag):
            continue
        href = str(link.get("href") or "")
        service_url = _normalize_kwork_service_url(urljoin(page_url, href))
        if not service_url:
            continue
        container = _nearest_service_container(link)
        item = _item_from_container(container or link, page_url, category_name, service_url=service_url)
        if item is not None:
            services.append(item)
    return services


def extract_kwork_services_from_json_scripts(
    soup: BeautifulSoup,
    page_url: str,
    category_name: str | None,
) -> list[ScrapedItem]:
    """Извлекает услуги из JSON-скриптов и встроенных JSON-фрагментов."""

    services: list[ScrapedItem] = []
    for script in soup.find_all("script"):
        if not isinstance(script, Tag):
            continue
        text = script.string or script.get_text() or ""
        if not text.strip():
            continue
        for payload in _json_payloads_from_script(text):
            for candidate in _walk_json_services(payload):
                item = _item_from_json(candidate, page_url, category_name)
                if item is not None:
                    services.append(item)
    return _dedupe_items(services)


def save_kwork_debug_html(url: str, html: str, reason: str) -> Path:
    """Сохраняет HTML и краткую диагностику страницы Kwork."""

    debug_dir = config.DATA_DIR / "debug" / "kwork"
    debug_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = _slug_from_url(url)
    html_path = debug_dir / f"kwork_debug_{timestamp}_{slug}.html"
    summary_path = debug_dir / f"kwork_debug_{timestamp}_{slug}.txt"

    soup = BeautifulSoup(html, "html.parser")
    link_count = len(
        [
            link
            for link in _safe_select(soup, "a[href]")
            if _is_kwork_service_url(urljoin(url, str(link.get("href") or "")))
        ]
    )
    script_count = len(soup.find_all("script"))
    price_like_count = len(re.findall(r"\b\d[\d\s.,]*(?:₽|руб|р\.|kwork)\b", html, re.IGNORECASE))

    html_path.write_text(html, encoding="utf-8")
    summary_path.write_text(
        "\n".join(
            [
                f"URL: {url}",
                f"Причина: {reason}",
                f"Длина HTML: {len(html)}",
                f"Ссылок /kwork/: {link_count}",
                f"script-тегов: {script_count}",
                f"price-like текстов: {price_like_count}",
                f"SmartCaptcha: {'да' if _has_smart_captcha(html) else 'нет'}",
                f"KWORK_COOKIE: {'задан' if config.KWORK_COOKIE else 'KWORK_COOKIE не задан'}",
            ]
        ),
        encoding="utf-8",
    )
    return html_path


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
        if extract_kwork_services_from_dom(soup, current_url, _category_name_from_url(current_url)) and _is_category_url(current_url):
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


def _category_name_from_url(url: str) -> str:
    slug = url.rstrip("/").split("/")[-1]
    return _clean_category_name(re.sub(r"[-_]+", " ", slug).strip().title()) or "Категория Kwork"


def _subcategory_name_from_url(url: str, category_name: str | None) -> str | None:
    from_url = _category_name_from_url(url)
    if category_name and from_url.casefold() == category_name.casefold():
        return None
    return from_url


def _extract_category_name(soup: BeautifulSoup, url: str) -> str:
    for selector in ("h1", "[class*='page-title']", "[class*='category-title']"):
        title = soup.select_one(selector)
        if title:
            text = _clean_category_name(title.get_text(" ", strip=True))
            if text:
                return text
    if soup.title and soup.title.string:
        title_text = _clean_category_name(soup.title.string.split("|")[0])
        if title_text:
            return title_text
    return _category_name_from_url(url)


def _safe_select(parent: Tag | BeautifulSoup, selector: str) -> list[Tag]:
    try:
        return [node for node in parent.select(selector) if isinstance(node, Tag)]
    except SelectorSyntaxError:
        return []


def _looks_like_kwork_card(card: Tag) -> bool:
    classes = set(card.get("class") or [])
    if card.get("data-id") and "kwork-card-item" in classes:
        return True
    if card.get("data-kwork-id"):
        return True
    for link in card.select("a[href]"):
        if _is_kwork_service_url(str(link.get("href") or "")):
            return True
    if card.select_one(KWORK_CARD_SELECTORS["kwork_price"]):
        return bool(card.get("data-id") or card.select_one(KWORK_CARD_SELECTORS["kwork_url"]))
    return False


def _item_from_container(
    container: Tag,
    page_url: str,
    category_name: str | None,
    *,
    service_url: str | None = None,
) -> ScrapedItem | None:
    item_url = service_url or _extract_card_url(container, KWORK_CARD_SELECTORS["kwork_url"], page_url)
    item_url = _normalize_kwork_service_url(item_url or "")
    if not item_url:
        return None

    title = _extract_text(container, KWORK_CARD_SELECTORS["kwork_title"])
    if not title:
        link = container if container.name == "a" else _first_service_link(container, page_url)
        title = _normalize_spaces(link.get_text(" ", strip=True)) if isinstance(link, Tag) else None
    title = _clean_title(title)
    if not title:
        return None

    price_text = _extract_text(container, KWORK_CARD_SELECTORS["kwork_price"])
    if not price_text:
        price_text = _extract_generic_price_text(container)
    price = _parse_price(price_text)
    description = _build_kwork_description(container)
    return ScrapedItem(
        source="kwork",
        url=item_url,
        title=title,
        price=price,
        currency="RUB" if price is not None else None,
        description=description,
        category=category_name or _category_name_from_url(page_url),
        subcategory=_subcategory_name_from_url(page_url, category_name),
        is_service=True,
        parse_status="success",
    )


def _nearest_service_container(link: Tag) -> Tag | None:
    current: Tag | None = link
    best: Tag | None = link
    for _ in range(6):
        if current is None or not isinstance(current.parent, Tag):
            break
        current = current.parent
        text_len = len(_normalize_spaces(current.get_text(" ", strip=True)))
        if _looks_like_kwork_card(current):
            return current
        if text_len <= 1200 and _first_service_link(current, ""):
            best = current
    return best


def _extract_text(parent: Tag, selector: str) -> str | None:
    element = parent.select_one(selector) if selector else None
    if element is None:
        return None
    return _normalize_spaces(element.get_text(" ", strip=True))


def _first_service_link(parent: Tag, base_url: str) -> Tag | None:
    for link in parent.select("a[href]"):
        href = str(link.get("href") or "")
        candidate = urljoin(base_url, href) if base_url else href
        if _is_kwork_service_url(candidate):
            return link
    return None


def _build_kwork_description(container: Tag) -> str | None:
    parts: list[str] = []
    seller = _extract_text(container, KWORK_CARD_SELECTORS["kwork_seller"])
    rating = _extract_text(container, KWORK_CARD_SELECTORS["kwork_rating"])
    reviews = _extract_text(container, KWORK_CARD_SELECTORS["kwork_reviews"])
    seller_level = _extract_text(container, KWORK_CARD_SELECTORS["kwork_seller_level"])

    if seller:
        parts.append(f"Продавец: {seller}")
    if rating:
        parts.append(f"рейтинг: {rating}")
    if reviews:
        parts.append(f"отзывы: {reviews}")
    if seller_level:
        parts.append(f"уровень: {seller_level}")

    return ", ".join(parts) if parts else None


def _extract_generic_price_text(container: Tag) -> str | None:
    for element in container.find_all(string=True):
        text = _normalize_spaces(str(element))
        if _looks_like_price(text):
            return text
    return None


def _extract_card_url(parent: Tag, selector: str, base_url: str) -> str | None:
    elements = parent.select(selector) if selector else []
    for element in elements:
        if not isinstance(element, Tag):
            continue
        href = element.get("href")
        normalized_url = _normalize_url(urljoin(base_url, str(href or "")))
        if _is_kwork_service_url(normalized_url):
            return normalized_url
    return None


def _extract_keywords(parent: Tag | BeautifulSoup, selector: str) -> list[str]:
    values: list[str] = []
    for element in _safe_select(parent, selector):
        text = _normalize_spaces(element.get_text(" ", strip=True))
        if 2 <= len(text) <= 40 and not _looks_like_price(text):
            values.append(text)
    return values


def _json_payloads_from_script(text: str) -> list[object]:
    payloads: list[object] = []
    stripped = text.strip()
    if not stripped:
        return payloads

    payloads.extend(_json_payloads_from_js_assignments(stripped))

    if stripped[0] in "[{":
        try:
            payloads.append(json.loads(stripped))
            return payloads
        except json.JSONDecodeError:
            pass

    decoder = json.JSONDecoder()
    for match in re.finditer(r"[\[{]", stripped):
        try:
            payload, _ = decoder.raw_decode(stripped[match.start() :])
        except json.JSONDecodeError:
            continue
        payloads.append(payload)
        if len(payloads) >= 200:
            break
    return payloads


def _json_payloads_from_js_assignments(text: str) -> list[object]:
    payloads: list[object] = []
    decoder = json.JSONDecoder()
    for match in re.finditer(r"window\.(?:stateData|__INITIAL_STATE__)\s*=", text):
        start = match.end()
        while start < len(text) and text[start].isspace():
            start += 1
        if start >= len(text) or text[start] not in "{[":
            continue
        try:
            payload, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        payloads.append(payload)
    return payloads


def _walk_json_services(value: object) -> list[dict[str, object]]:
    services: list[dict[str, object]] = []
    if isinstance(value, dict):
        title = _first_value(value, ("title", "gtitle", "name"))
        url = _first_value(value, ("url", "link", "href"))
        price = _json_price(value)
        if title and (url or price is not None):
            services.append(value)
        for nested in value.values():
            services.extend(_walk_json_services(nested))
    elif isinstance(value, list):
        for item in value:
            services.extend(_walk_json_services(item))
    return services


def _item_from_json(
    data: dict[str, object],
    page_url: str,
    category_name: str | None,
) -> ScrapedItem | None:
    title = _clean_title(_first_value(data, ("title", "gtitle", "name")))
    if not title:
        return None

    raw_url = _first_value(data, ("url", "link", "href"))
    service_url = _normalize_kwork_service_url(urljoin(page_url, raw_url)) if raw_url else None
    price = _json_price(data)
    if not service_url and price is None:
        return None
    if raw_url and not service_url:
        return None

    description = _json_description(data, title)
    return ScrapedItem(
        source="kwork",
        url=service_url or page_url,
        title=title,
        price=price,
        currency="RUB" if price is not None else None,
        description=description,
        category=_clean_category_name(category_name) or _category_name_from_url(page_url),
        subcategory=_subcategory_name_from_url(page_url, category_name),
        is_service=True,
        parse_status="success",
    )


def _first_value(data: dict[str, object], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, (str, int, float)):
            text = _normalize_spaces(str(value))
            if text:
                return text
    return None


def _json_description(data: dict[str, object], title: str) -> str | None:
    explicit = _first_value(
        data,
        (
            "description",
            "desc",
            "text",
            "shortDescription",
            "short_description",
            "subtitle",
        ),
    )
    if explicit and explicit != title:
        return _normalize_spaces(explicit)

    parts: list[str] = []
    seller = _first_value(data, ("userName", "seller", "workerName"))
    rating = _first_value(data, ("convertedUserRating", "userRating"))
    reviews = _first_value(data, ("userRatingCount", "reviews", "reviewsCount"))
    days = _first_value(data, ("days",))
    queue = _first_value(data, ("queueCount",))
    volume = _json_volume(data)

    if seller:
        parts.append(f"продавец {seller}")
    if rating:
        rating_text = f"рейтинг {rating}"
        if reviews:
            rating_text = f"{rating_text}, отзывов {reviews}"
        parts.append(rating_text)
    if days:
        parts.append(f"срок от {days} дн.")
    if queue and queue != "0":
        parts.append(f"в очереди {queue}")
    if volume:
        parts.append(volume)

    if not parts:
        return None
    description = "Кратко: " + "; ".join(parts)
    return description if description.endswith((".", "!", "?")) else f"{description}."


def _json_volume(data: dict[str, object]) -> str | None:
    base_volume = _first_value(data, ("baseVolume", "packageVolume"))
    short_name = _first_value(data, ("baseVolumeShortName",))
    if not base_volume or not short_name:
        return None
    return f"объем {base_volume} {short_name}"


def _json_price(data: dict[str, object]) -> Decimal | None:
    for key in ("price", "amount", "minPrice", "min_price", "average_price"):
        price = _parse_price(str(data.get(key))) if data.get(key) is not None else None
        if price is not None:
            return price
    offers = data.get("offers")
    if isinstance(offers, dict):
        return _json_price(offers)
    if isinstance(offers, list):
        for offer in offers:
            if isinstance(offer, dict):
                price = _json_price(offer)
                if price is not None:
                    return price
    return None


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
    return bool(re.search(r"\d", value) and re.search(r"(руб|₽|kwork|от)", value, re.IGNORECASE))


def _find_next_page_url(
    soup: BeautifulSoup,
    current_url: str,
    seen_pages: set[str],
) -> str | None:
    current = _normalize_url(current_url)
    candidates: list[str] = []
    for link in _safe_select(soup, KWORK_CARD_SELECTORS["pagination_next"]):
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


def _normalize_kwork_service_url(url: str) -> str | None:
    normalized = _normalize_url(url)
    if not _is_kwork_service_url(normalized):
        return None
    parts = urlsplit(normalized)
    return normalized if parts.scheme else f"https://kwork.ru{parts.path}"


def _is_kwork_service_url(url: str) -> bool:
    normalized = _normalize_url(url)
    parts = urlsplit(normalized)
    if parts.netloc and parts.netloc != "kwork.ru":
        return False
    if any(blocked in parts.path for blocked in BLOCKED_URL_PARTS):
        return False
    if parts.path.startswith("/categories/"):
        return False
    if KWORK_SERVICE_URL_RE.match(normalized):
        return True
    return bool(parts.netloc == "kwork.ru" and re.match(r"^/kwork/\d+", parts.path))


def _is_valid_kwork_service(item: ScrapedItem) -> bool:
    return bool(item.title and _normalize_kwork_service_url(item.url))


def _dedupe_items(items: list[ScrapedItem]) -> list[ScrapedItem]:
    result: list[ScrapedItem] = []
    seen: set[str] = set()
    for item in items:
        url_key = _normalize_kwork_service_url(item.url) or ""
        title_key = _normalize_spaces(item.title).casefold()
        key = url_key or title_key
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


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


def _keywords_from_text(text: str) -> list[str]:
    return [word for word in re.findall(r"[A-Za-zА-Яа-яЁё0-9]{3,}", text)[:8]]


def _clean_title(value: str | None) -> str | None:
    if not value:
        return None
    title = _normalize_spaces(value)
    if not (3 <= len(title) <= 180):
        return None
    blocked = {"следующая", "назад", "войти", "регистрация", "корзина", "каталог"}
    if title.casefold() in blocked:
        return None
    return title


def _normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _clean_category_name(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = _normalize_spaces(value)
    cleaned = re.sub(
        r":\s*услуги\s+.*?\s+от\s+\d[\d\s]*\s*руб\.?\s*[–-]\s*Kwork$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s*[–-]\s*Kwork$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip(" :-–") or None


def _has_smart_captcha(html: str) -> bool:
    lowered = html.casefold()
    if "smart-captcha" in lowered:
        return True
    if "подтвердите, что вы не робот" in lowered and "captcha" in lowered:
        return True
    if "captcha-container" in lowered and "yandexsmartcaptcha" in lowered:
        return True
    return False


def _looks_like_authenticated_kwork_session(html: str, soup: BeautifulSoup) -> bool:
    if not config.KWORK_COOKIE:
        return False

    if re.search(r'window\.USER_ID\s*=\s*["\']\d+["\']', html):
        return True
    if re.search(r'window\.actorType\s*=\s*["\'][^"\']+["\']', html):
        return True
    if _safe_select(soup, "a[href*='/user/'], a[href*='/manage_orders'], a[href*='/manage_kworks'], a[href*='/balance']"):
        return True
    if re.search(r'window\.USER_ID\s*=\s*["\']\s*["\']', html):
        return False
    if re.search(r"window\.actorType\s*=\s*null", html):
        return False

    login_markers = (
        'href="/login"',
        "href='/login'",
        "Войти",
        "Регистрация",
    )
    return not any(marker in html for marker in login_markers)


def _empty_page_reason(html: str, soup: BeautifulSoup) -> str:
    if _has_smart_captcha(html):
        return KWORK_CAPTCHA_ERROR
    if len(html.strip()) < 1000:
        return "HTML Kwork подозрительно короткий, карточки услуг не найдены"
    if _safe_select(soup, "a[href*='/categories/']"):
        return "Страница категории Kwork открылась, но карточки услуг не найдены"
    return "Карточки услуг Kwork не найдены: DOM, ссылки /kwork/ и JSON fallback не дали результатов"


def _slug_from_url(url: str) -> str:
    parts = urlsplit(url)
    raw = (parts.path.strip("/") or parts.netloc or "kwork").replace("/", "_")
    raw = re.sub(r"[^A-Za-zА-Яа-яЁё0-9_-]+", "_", raw).strip("_")
    return (raw[:80] or "kwork").lower()


def _failed_category(
    url: str,
    status: ParseStatus,
    error: str,
    *,
    category_name: str | None = None,
) -> KworkCategory:
    return KworkCategory(
        category_name=category_name or _category_name_from_url(url),
        subcategory_name=_subcategory_name_from_url(url, category_name),
        url=url,
        competitors_count=0,
        keywords=[],
        parse_status=status,
        parse_error=error,
    )
