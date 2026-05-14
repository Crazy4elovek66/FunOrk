"""Фильтр игровых товаров FunPay, которые нельзя нормально перенести на Kwork."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from app.models import ScrapedItem
from app.rules.loader import load_funpay_stop_categories, normalize_rule_text_list


GAME_URL_MARKERS = {
    "apex",
    "brawl-stars",
    "clash-royale",
    "cs2",
    "csgo",
    "dota",
    "dota2",
    "fortnite",
    "genshin",
    "minecraft",
    "pubg",
    "roblox",
    "steam",
    "valorant",
    "warface",
    "wot",
}

NON_GAME_CATEGORY_NAMES = {
    "acrobat",
    "adobe",
    "after effects",
    "app store & itunes",
    "blum",
    "canva",
    "capcut",
    "catizen",
    "chatgpt",
    "claude",
    "crunchyroll",
    "cursor ai",
    "discord",
    "elevenlabs",
    "esim",
    "facebook",
    "figma",
    "fl studio",
    "gemini",
    "geforce now",
    "github copilot",
    "grok",
    "hailuo ai",
    "higgsfield",
    "instagram",
    "kick",
    "kimi",
    "kling ai",
    "leonardo ai",
    "likee",
    "lovable ai",
    "midjourney",
    "nano banana",
    "netflix",
    "noping",
    "perplexity",
    "photoshop",
    "razer gold",
    "runway",
    "soundcloud",
    "spotify",
    "suno",
    "telegram",
    "threads",
    "tiktok",
    "tinder",
    "trovo",
    "twitch",
    "twitter",
    "veo 3",
    "vk coin",
    "voicemod",
    "windsurf",
    "wtfast",
    "youtube",
    "вконтакте",
    "дзен",
    "одноклассники",
    "тиндер",
}

GAME_CONTEXT_MARKERS = {
    "apex",
    "brawl stars",
    "clash royale",
    "counter-strike",
    "cs go",
    "cs2",
    "dota",
    "fortnite",
    "genshin",
    "minecraft",
    "pubg",
    "roblox",
    "steam",
    "valorant",
    "warface",
    "world of tanks",
    "wot",
}

DIRECT_GAME_GOODS_MARKERS = {
    "игры",
    "робуксы",
    "скин",
    "games",
    "robux",
    "skin",
    "skins",
    "v-bucks",
}

CONTEXTUAL_GAME_GOODS_MARKERS = {
    "боевой пропуск",
    "буст",
    "бустинг",
    "валюта",
    "вещи",
    "гемы",
    "голда",
    "золото",
    "инвентарь",
    "кейсы",
    "ключи",
    "коины",
    "монеты",
    "предмет",
    "прокачка",
    "ранг",
    "фарм",
    "battle pass",
    "boost",
    "coins",
    "diamonds",
    "farm",
    "gold",
    "inventory",
    "items",
    "rank",
}

CATEGORY_GAME_GOODS_MARKERS = {
    "аккаунты с играми",
    "боевой пропуск",
    "бонус-коды",
    "буст",
    "бустинг",
    "валюта",
    "вирты",
    "гайды",
    "гемы",
    "голда",
    "донат",
    "достижения",
    "золото",
    "игры",
    "инвентарь",
    "кейсы",
    "ключи",
    "кланы",
    "кредиты",
    "монеты",
    "оффлайн активации",
    "предметы",
    "прокачка",
    "рейды",
    "ресурсы",
    "скины",
    "токены",
    "фарм",
    "game pass",
    "prime gaming",
    "pve",
    "pvp",
    "twitch drops",
}

REASON_MARKERS = (
    ("аккаунты с играми", ("аккаунты с играми",)),
    ("скины", ("скин", "скины", "skin", "skins")),
    ("игровая валюта", ("робуксы", "валюта", "вирты", "гемы", "голда", "золото", "коины", "монеты", "кредиты", "токены", "robux", "v-bucks", "coins", "diamonds", "gold")),
    ("донат", ("донат",)),
    ("игровые предметы", ("предмет", "предметы", "инвентарь", "кейсы", "вещи", "items", "inventory")),
    ("буст и прокачка", ("буст", "бустинг", "прокачка", "ранг", "фарм", "boost", "rank", "farm")),
    ("ключи и активации игр", ("ключи", "ключ", "оффлайн активации", "онлайн активации", "game pass")),
    ("игровые бонусы", ("twitch drops", "prime gaming", "бонус-коды", "боевой пропуск", "battle pass")),
    ("игровые услуги", ("рейды", "pve", "pvp", "квесты", "достижения", "кланы")),
    ("игры", ("игры", "games")),
)


def is_game_related_category_url(url: str) -> bool:
    path = urlsplit(url).path.casefold()
    normalized_path = path.replace("_", "-")
    return any(marker in normalized_path for marker in GAME_URL_MARKERS)


def active_stop_category_ids() -> set[str]:
    payload = load_funpay_stop_categories()
    if not bool(payload.get("enabled", True)):
        return set()
    category_ids = payload.get("category_ids", {})
    if not isinstance(category_ids, dict):
        return set()
    disabled = {
        _normalize_text(str(item))
        for item in normalize_rule_text_list(payload.get("disabled_categories", []))
        if str(item).strip()
    }
    active_ids: set[str] = set()
    for category, raw_ids in category_ids.items():
        if _normalize_text(str(category)) in disabled:
            continue
        values = raw_ids if isinstance(raw_ids, list) else [raw_ids]
        for category_id in values:
            id_text = str(category_id).strip()
            if id_text:
                active_ids.add(id_text)
    return active_ids


def is_game_related_category(
    *,
    name: str | None = None,
    text: str | None = None,
    url: str | None = None,
) -> bool:
    return bool(classify_game_category_reasons(name=name, text=text, url=url))


def classify_game_category_reasons(
    *,
    name: str | None = None,
    text: str | None = None,
    url: str | None = None,
) -> list[str]:
    normalized_name = _normalize_text(name or "")
    normalized_raw_text = _normalize_text(text or "")
    normalized_text = _normalize_text(" ".join(filter(None, (name, text, url))))
    if url and _extract_lots_category_id(url) in active_stop_category_ids():
        return ["стоп-ID категории FunPay"]
    if _matches_active_stop_category(normalized_name, normalized_raw_text, normalized_text):
        return ["стоп-категория FunPay"]

    if (
        normalized_name in NON_GAME_CATEGORY_NAMES
        or _starts_with_non_game_category(normalized_raw_text)
        or _starts_with_non_game_category(normalized_text)
    ):
        return []

    reasons: set[str] = set()
    if url and is_game_related_category_url(url):
        reasons.add("игровая категория")
    if any(_contains_marker(normalized_text, marker) for marker in GAME_CONTEXT_MARKERS):
        reasons.add("игровая категория")
    if any(_contains_marker(normalized_text, marker) for marker in CATEGORY_GAME_GOODS_MARKERS):
        reasons.update(_classify_marker_reasons(normalized_text))
    return sorted(reasons)


def is_game_related_item(item: ScrapedItem) -> bool:
    return bool(classify_game_item_reasons(item))


def classify_game_item_reasons(item: ScrapedItem) -> list[str]:
    text = _normalize_text(
        " ".join(
            filter(
                None,
                (
                    item.title,
                    item.description,
                    item.category,
                    item.category_id,
                    item.subcategory,
                    item.url,
                ),
            )
        )
    )
    if not text:
        return []

    category_reasons = classify_game_category_reasons(
        name=item.category,
        text=" ".join(filter(None, (item.title, item.description, item.subcategory))),
        url=_category_url_for_item(item),
    )
    has_direct_game_goods = any(_contains_marker(text, marker) for marker in DIRECT_GAME_GOODS_MARKERS)
    has_contextual_game_goods = any(_contains_marker(text, marker) for marker in CONTEXTUAL_GAME_GOODS_MARKERS)
    reasons: set[str] = set()
    if has_direct_game_goods:
        reasons.update(category_reasons)
        reasons.update(_classify_marker_reasons(text))
    if category_reasons and has_contextual_game_goods:
        reasons.update(category_reasons)
        reasons.update(_classify_marker_reasons(text))
    return sorted(reasons)


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _starts_with_non_game_category(text: str) -> bool:
    return any(text == name or text.startswith(f"{name} ") for name in NON_GAME_CATEGORY_NAMES)


def active_stop_categories() -> list[str]:
    payload = load_funpay_stop_categories()
    if not bool(payload.get("enabled", True)):
        return []
    categories = normalize_rule_text_list(payload.get("categories", []))
    disabled = {
        _normalize_text(str(item))
        for item in normalize_rule_text_list(payload.get("disabled_categories", []))
        if str(item).strip()
    }
    return [category for category in categories if _normalize_text(category) not in disabled]


def _matches_active_stop_category(*texts: str) -> bool:
    normalized_categories = {_normalize_text(category) for category in active_stop_categories()}
    for text in texts:
        if not text:
            continue
        for category in normalized_categories:
            if text == category or text.startswith(f"{category} "):
                return True
    return False


def _extract_lots_category_id(url: str) -> str | None:
    match = re.search(r"/lots/(\d+)/?", url)
    return match.group(1) if match else None


def _category_url_for_item(item: ScrapedItem) -> str:
    if item.category_id:
        return f"https://funpay.com/lots/{item.category_id}/"
    return item.url


def _classify_marker_reasons(text: str) -> set[str]:
    reasons: set[str] = set()
    for reason, markers in REASON_MARKERS:
        if any(_contains_marker(text, marker) for marker in markers):
            reasons.add(reason)
    if not reasons:
        reasons.add("игровая категория")
    return reasons


def _contains_marker(text: str, marker: str) -> bool:
    normalized_marker = _normalize_text(marker)
    if not normalized_marker:
        return False
    if len(normalized_marker) <= 3:
        return normalized_marker in set(re.findall(r"[a-zа-яё0-9]+", text))
    return normalized_marker in text
