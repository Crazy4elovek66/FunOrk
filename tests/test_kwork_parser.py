from decimal import Decimal

from bs4 import BeautifulSoup

from app.collectors.kwork import (
    KWORK_SESSION_ERROR,
    SessionValidationError,
    extract_kwork_services_from_dom,
    extract_kwork_services_from_json_scripts,
    extract_kwork_services_from_links,
    collect_catalog,
    parse_kwork_category,
    verify_kwork_session,
)
from app.collectors.http_client import AntiBanError


def test_kwork_parser_extracts_data_id_card():
    soup = BeautifulSoup(
        """
        <div data-id="18028913" class="js-kwork-card kwork-card-item">
            <div class="kwork-card-item__title">
                <a href="https://kwork.ru/ai/18028913/nastroyu-gemini">
                    <span class="first-letter">Настрою Gemini для бизнеса</span>
                </a>
            </div>
            <div class="kwork-card-item__info-price"><span class="price-wrap__value">1 500 ₽</span></div>
        </div>
        """,
        "html.parser",
    )

    items = extract_kwork_services_from_dom(soup, "https://kwork.ru/categories/ai", "AI-услуги")

    assert len(items) == 1
    assert items[0].url == "https://kwork.ru/ai/18028913/nastroyu-gemini"
    assert items[0].title == "Настрою Gemini для бизнеса"
    assert items[0].price == Decimal("1500")


def test_kwork_parser_extracts_real_card_url_without_kwork_segment():
    soup = BeautifulSoup(
        """
        <div data-id="18028913" class="js-kwork-card kwork-card-item">
            <div class="kwork-card-item__title">
                <a href="https://kwork.ru/website-repair/18028913/dorabotka-sayta-na-1s-bitriks">
                    <span class="first-letter">Доработка сайта на 1С Битрикс</span>
                </a>
            </div>
            <div class="kwork-card-item__info-price">
                <span class="price-wrap__value">1 000 ₽</span>
            </div>
            <div class="kwork-card-item__username"><a>webmaster</a></div>
            <span class="kwork-card-item__rating-number">5.0</span>
            <span class="kwork-card-item__rating-count">12</span>
            <span class="kwork-card-item__user-level">Высший рейтинг</span>
        </div>
        """,
        "html.parser",
    )

    items = extract_kwork_services_from_dom(soup, "https://kwork.ru/categories/website-repair", "Разработка")

    assert len(items) == 1
    assert items[0].title == "Доработка сайта на 1С Битрикс"
    assert items[0].price == Decimal("1000")
    assert items[0].url == "https://kwork.ru/website-repair/18028913/dorabotka-sayta-na-1s-bitriks"
    assert items[0].source == "kwork"
    assert items[0].parse_status == "success"
    assert "Продавец: webmaster" in (items[0].description or "")


def test_kwork_parser_extracts_link_only_cards():
    soup = BeautifulSoup(
        """
        <div class="listing">
            <div class="tile">
                <a href="https://kwork.ru/video/18028914/ai-video-dlya-reklamy">AI-видео для рекламы</a>
                <span class="amount">2 000 руб.</span>
            </div>
        </div>
        """,
        "html.parser",
    )

    items = extract_kwork_services_from_links(soup, "https://kwork.ru/categories/video", "Видео")

    assert len(items) == 1
    assert items[0].title == "AI-видео для рекламы"
    assert items[0].price == Decimal("2000")


def test_kwork_parser_extracts_json_script():
    soup = BeautifulSoup(
        """
        <script type="application/ld+json">
        {"name":"Помощь с Runway AI","url":"https://kwork.ru/kwork/3","offers":{"price":"3500"}}
        </script>
        """,
        "html.parser",
    )

    items = extract_kwork_services_from_json_scripts(soup, "https://kwork.ru/categories/video", "Видео")

    assert len(items) == 1
    assert items[0].title == "Помощь с Runway AI"
    assert items[0].price == Decimal("3500")


def test_kwork_parser_extracts_window_state_data():
    soup = BeautifulSoup(
        """
        <script>
        window.stateData={"viewData":{"kworks":{"data":[
            {
                "id":27095427,
                "url":"/website-repair/27095427/dorabotka-na-django",
                "gtitle":"Доработка на Django",
                "price":1000,
                "userName":"pyaidev",
                "convertedUserRating":"4.9",
                "userRatingCount":"225",
                "days":1
            }
        ]}}};
        </script>
        """,
        "html.parser",
    )

    items = extract_kwork_services_from_json_scripts(
        soup,
        "https://kwork.ru/categories/website-repair",
        "Доработка сайта",
    )

    assert len(items) == 1
    assert items[0].title == "Доработка на Django"
    assert items[0].url == "https://kwork.ru/website-repair/27095427/dorabotka-na-django"
    assert items[0].price == Decimal("1000")
    assert items[0].description == "Кратко: продавец pyaidev; рейтинг 4.9, отзывов 225; срок от 1 дн."


def test_kwork_parser_cleans_seo_category_suffix():
    soup = BeautifulSoup(
        """
        <title>Создание сайта: услуги фрилансеров от 500 руб. – Kwork</title>
        <script>
        window.stateData={"viewData":{"kworks":{"data":[
            {"url":"/website/1/site","gtitle":"Сайт под ключ","price":500,"userName":"dev"}
        ]}}};
        </script>
        """,
        "html.parser",
    )

    items = extract_kwork_services_from_json_scripts(
        soup,
        "https://kwork.ru/categories/website",
        "Создание сайта: услуги фрилансеров от 500 руб. – Kwork",
    )

    assert items[0].category == "Создание сайта"


def test_kwork_parser_marks_empty_page_parse_failed(monkeypatch, tmp_path):
    debug_paths = []

    def fake_get_html(self, url, *, source="kwork", force=False):
        return "<html><h1>Дизайн</h1><p>Нет карточек</p></html>"

    def fake_save_debug(url, html, reason):
        debug_paths.append((url, reason))
        return tmp_path / "debug.html"

    monkeypatch.setattr("app.collectors.kwork.HttpClient.get_html", fake_get_html)
    monkeypatch.setattr("app.collectors.kwork.save_kwork_debug_html", fake_save_debug)

    category, items = parse_kwork_category("https://kwork.ru/categories/design")

    assert items == []
    assert category.parse_status == "parse_failed"
    assert "карточки услуг не найдены" in category.parse_error.casefold()
    assert debug_paths


def test_kwork_parser_stops_on_smartcaptcha(monkeypatch, tmp_path):
    debug_paths = []

    def fake_get_html(self, url, *, source="kwork", force=False):
        return '<html><div class="smart-captcha">Подтвердите, что вы не робот</div></html>'

    def fake_save_debug(url, html, reason):
        debug_paths.append((url, reason))
        return tmp_path / "debug.html"

    monkeypatch.setattr("app.collectors.kwork.HttpClient.get_html", fake_get_html)
    monkeypatch.setattr("app.collectors.kwork.save_kwork_debug_html", fake_save_debug)

    try:
        parse_kwork_category("https://kwork.ru/categories/design")
    except AntiBanError as error:
        assert "SmartCaptcha" in str(error)
    else:
        raise AssertionError("parse_kwork_category должен прерываться при SmartCaptcha")

    assert debug_paths


def test_verify_kwork_session_uses_uncached_request_and_detects_login(monkeypatch):
    calls = []

    def fake_get_html(self, url, *, source="kwork", force=False):
        calls.append((url, source, force))
        return '<html><script>window.USER_ID = "12345";</script><a href="/user/test">Профиль</a></html>'

    monkeypatch.setattr("app.collectors.kwork.HttpClient.get_html", fake_get_html)
    monkeypatch.setattr("app.collectors.kwork.config.KWORK_COOKIE", "session=alive")

    assert verify_kwork_session() is True
    assert calls == [("https://kwork.ru", "kwork", True)]


def test_verify_kwork_session_falls_back_to_seller_page(monkeypatch):
    calls = []

    def fake_get_html(self, url, *, source="kwork", force=False):
        calls.append((url, source, force))
        if url == "https://kwork.ru":
            return '<html><script>window.USER_ID = "";</script></html>'
        return '<html><script>window.USER_ID = "12345"; window.actorType="worker";</script></html>'

    monkeypatch.setattr("app.collectors.kwork.HttpClient.get_html", fake_get_html)
    monkeypatch.setattr("app.collectors.kwork.config.KWORK_COOKIE", "session=alive")

    assert verify_kwork_session() is True
    assert calls == [
        ("https://kwork.ru", "kwork", True),
        ("https://kwork.ru/seller", "kwork", True),
    ]


def test_collect_catalog_fails_fast_when_session_is_invalid(monkeypatch):
    monkeypatch.setattr("app.collectors.kwork.verify_kwork_session", lambda force=True: False)

    try:
        collect_catalog(force=True, limit=1)
    except SessionValidationError as error:
        assert str(error) == KWORK_SESSION_ERROR
    else:
        raise AssertionError("collect_catalog должен прерываться до обхода каталога")
