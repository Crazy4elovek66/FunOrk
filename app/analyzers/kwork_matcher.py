"""Поиск похожих услуг Kwork для оценки спроса на лоты FunPay."""

from __future__ import annotations

import re
from decimal import Decimal
from statistics import mean
from typing import TypedDict

from app.db import fetch_scraped_items
from app.models import ScrapedItem


class KworkMatch(TypedDict):
    service: ScrapedItem
    score: int
    markers: list[str]


MARKER_ALIASES = {
    "ai": {"ai", "ии", "нейросеть", "нейросети"},
    "chatgpt": {"chatgpt", "gpt", "чатгпт"},
    "gemini": {"gemini", "джемини"},
    "midjourney": {"midjourney", "миджорни"},
    "runway": {"runway", "ранвей"},
    "premium": {"premium", "премиум"},
    "video": {"video", "видео", "ролик", "ролики"},
    "design": {"design", "дизайн", "баннер", "логотип"},
    "text": {"text", "текст", "копирайтинг", "описание"},
    "game": {"game", "игра", "игровой"},
    "consulting": {"коучинг", "консультация", "настройка", "помощь"},
    "service": {"сервис", "услуга", "подписка"},
    "smm": {"smm", "смм", "продвижение", "соцсети", "соцсетей"},
    "vk": {"vk", "вк", "вконтакте", "vkontakte"},
    "likes": {"лайк", "лайки", "лайков", "likes"},
    "followers": {"подписчик", "подписчики", "подписчиков", "followers"},
    "comments": {"комментарий", "комментарии", "комментариев", "коммент", "комменты", "comments"},
    "reposts": {"репост", "репосты", "репостов", "repost"},
    "views": {"просмотр", "просмотры", "просмотров", "views"},
    "bot": {"бот", "боты", "bot", "bots"},
    "automation": {"автоматизация", "скрипт", "скрипты", "api", "парсер"},
}
TOKEN_STOPWORDS = {
    "читать",
    "описание",
    "цена",
    "указана",
    "шт",
    "штук",
    "быстро",
    "быстрый",
    "гарантия",
    "качество",
    "автовыдача",
    "отзыв",
    "отзывы",
    "любой",
    "любые",
    "ваш",
    "ваша",
    "ваше",
    "для",
    "под",
    "без",
    "про",
    "или",
}


def load_kwork_services() -> list[ScrapedItem]:
    services: list[ScrapedItem] = []
    for row in fetch_scraped_items(source="kwork"):
        if row["parse_status"] != "success":
            continue
        services.append(
            ScrapedItem(
                id=row["id"],
                source="kwork",
                url=row["url"],
                title=row["title"],
                price=Decimal(row["price"]) if row["price"] else None,
                currency=row["currency"],
                description=row["description"],
                category=row["category"],
                subcategory=row["subcategory"],
                is_service=bool(row["is_service"]),
                parse_status=row["parse_status"],
                parse_error=row["parse_error"],
            )
        )
    return services


def build_kwork_index(kwork_services: list[ScrapedItem]) -> list[dict[str, object]]:
    return [
        {
            "service": service,
            "tokens": _tokens_for_service(service),
            "markers": _markers_for_text(_service_text(service)),
        }
        for service in kwork_services
    ]


def match_funpay_to_kwork(
    funpay_item: ScrapedItem,
    kwork_services: list[ScrapedItem] | list[dict[str, object]],
) -> list[KworkMatch]:
    funpay_tokens = _tokens_for_service(funpay_item)
    funpay_markers = _markers_for_text(_service_text(funpay_item))
    matches: list[KworkMatch] = []

    for entry in kwork_services:
        if isinstance(entry, dict):
            service = entry["service"]
            service_tokens = set(entry["tokens"])
            service_markers = set(entry["markers"])
        else:
            service = entry
            service_tokens = _tokens_for_service(service)
            service_markers = _markers_for_text(_service_text(service))

        if not isinstance(service, ScrapedItem):
            continue

        token_overlap = funpay_tokens & service_tokens
        marker_overlap = funpay_markers & service_markers
        score = len(token_overlap) + len(marker_overlap) * 2
        if score < 3:
            continue
        matches.append(
            {
                "service": service,
                "score": score,
                "markers": sorted(token_overlap | marker_overlap),
            }
        )

    return sorted(matches, key=lambda match: match["score"], reverse=True)


def calculate_demand_from_matches(matches: list[KworkMatch]) -> dict[str, object]:
    prices = [
        match["service"].price
        for match in matches
        if match["service"].price is not None
    ]
    avg_price = Decimal(str(mean(prices))).quantize(Decimal("0.01")) if prices else None
    min_price = min(prices) if prices else None
    matches_count = len(matches)

    if matches_count == 0:
        demand_weight = 10
    elif matches_count <= 3:
        demand_weight = 35
    elif matches_count <= 10:
        demand_weight = 60
    else:
        demand_weight = 80

    if matches_count and avg_price and avg_price > 0:
        demand_weight += 10

    return {
        "matches_count": matches_count,
        "min_kwork_price": min_price,
        "avg_kwork_price": avg_price,
        "demand_weight": min(100, demand_weight),
    }


def _service_text(item: ScrapedItem) -> str:
    return " ".join(
        part
        for part in (item.title, item.description, item.category, item.subcategory)
        if part
    )


def _tokens_for_service(item: ScrapedItem) -> set[str]:
    text = _normalize(_service_text(item))
    return {
        token
        for token in re.findall(r"[a-zа-яё0-9]{3,}", text)
        if len(token) >= 3 and token not in TOKEN_STOPWORDS
    }


def _markers_for_text(text: str) -> set[str]:
    normalized = _normalize(text)
    markers: set[str] = set()
    tokens = set(re.findall(r"[a-zа-яё0-9]{2,}", normalized))
    for marker, aliases in MARKER_ALIASES.items():
        if tokens & aliases:
            markers.add(marker)
    return markers


def _normalize(value: str) -> str:
    normalized = value.casefold().replace("ё", "е")
    return re.sub(r"[^a-zа-я0-9]+", " ", normalized)
