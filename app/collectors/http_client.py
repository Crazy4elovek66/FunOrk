"""HTTP-клиент для безопасной загрузки публичных страниц."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass

import requests

from app.config import config


class HttpClientError(RuntimeError):
    """Базовая ошибка загрузки HTML-страницы."""


class AntiBanError(HttpClientError):
    """Сайт ограничил доступ или просит замедлить запросы."""


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

    def get_html(self, url: str) -> str:
        """Возвращает HTML страницы или выбрасывает понятную ошибку загрузки."""

        _sleep_before_request()

        try:
            response = self.session.get(url, timeout=self.timeout)
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

        return response.text


def _sleep_before_request() -> None:
    delay = random.uniform(config.REQUEST_DELAY_MIN, config.REQUEST_DELAY_MAX)
    if delay > 0:
        time.sleep(delay)


def get_html(url: str, timeout: float = 20.0) -> str:
    return HttpClient(timeout=timeout).get_html(url)
