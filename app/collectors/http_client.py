"""HTTP-клиент для безопасной загрузки публичных страниц."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import requests

from app.config import config
from app.db import cache_page, get_cached_page


class HttpClientError(RuntimeError):
    """Базовая ошибка загрузки HTML-страницы."""


class AntiBanError(HttpClientError):
    """Сайт ограничил доступ или просит замедлить запросы."""

    def __init__(self, message: str, *, html: str | None = None) -> None:
        super().__init__(message)
        self.html = html


class PageNotFoundError(HttpClientError):
    """Страница не найдена."""


@dataclass(slots=True)
class HttpClient:
    timeout: float = 20.0
    session: requests.Session | None = None

    def __post_init__(self) -> None:
        if self.session is None:
            self.session = requests.Session()
        self.session.headers.update({"User-Agent": config.USER_AGENT})

    def get_html(self, url: str, *, source: str = "funpay", force: bool = False) -> str:
        """Возвращает HTML страницы или выбрасывает понятную ошибку загрузки."""

        if not force:
            cached = get_cached_page(url)
            if cached is not None:
                fetched_at = datetime.fromisoformat(cached["fetched_at"])
                if datetime.now() - fetched_at <= timedelta(hours=config.CACHE_TTL_HOURS):
                    return str(cached["html"])

        _sleep_before_request()

        headers = _build_request_headers(url, source=source)

        try:
            response = self.session.get(url, timeout=self.timeout, headers=headers)
        except requests.RequestException as error:
            raise HttpClientError(f"Не удалось загрузить страницу {url}: {error}") from error

        if response.status_code in (403, 429):
            raise AntiBanError(
                f"FunOrk получил HTTP {response.status_code} при загрузке {url}. "
                "Сайт ограничил доступ, нужно увеличить задержку или повторить позже."
            )
        if response.status_code == 404:
            raise PageNotFoundError(f"Страница не найдена: {url}")
        if response.status_code >= 400:
            raise HttpClientError(
                f"Неожиданный HTTP {response.status_code} при загрузке {url}"
            )

        if not response.text.strip():
            raise HttpClientError(f"Страница вернула пустой HTML: {url}")

        if _has_blocking_smart_captcha(response.text):
            raise AntiBanError(
                "Kwork вернул SmartCaptcha (Yandex SmartCaptcha). Парсинг requests невозможен. "
                "Добавьте актуальный KWORK_COOKIE или используйте ручной импорт.",
                html=response.text,
            )

        cache_page(url, source=source, html=response.text, status_code=response.status_code)
        return response.text


def _sleep_before_request() -> None:
    min_interval = 60 / config.MAX_REQUESTS_PER_MINUTE
    delay = random.uniform(config.REQUEST_DELAY_MIN, config.REQUEST_DELAY_MAX)
    time.sleep(max(delay, min_interval))


def _build_request_headers(url: str, *, source: str) -> dict[str, str]:
    if source != "kwork":
        return {}

    headers = {
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
            "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
        ),
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "max-age=0",
        "Referer": "https://kwork.ru/seller",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": config.USER_AGENT,
        "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": '"iOS"',
    }

    if urlsplit(url).netloc == "kwork.ru" and config.KWORK_COOKIE:
        headers["Cookie"] = config.KWORK_COOKIE

    return headers


def _has_blocking_smart_captcha(html: str) -> bool:
    lowered = html.casefold()
    if "smart-captcha" in lowered:
        return True
    if "подтвердите, что вы не робот" in lowered and "captcha" in lowered:
        return True
    if "captcha-container" in lowered and "yandexsmartcaptcha" in lowered:
        return True
    return False


def get_html(url: str, timeout: float = 20.0, *, source: str = "funpay", force: bool = False) -> str:
    return HttpClient(timeout=timeout).get_html(url, source=source, force=force)
