from decimal import Decimal

from app.analyzers.kwork_mapper import map_to_kwork
from app.analyzers.opportunity_scorer import score_opportunity
from app.analyzers.risk_classifier import analyze_risk
from app.collectors.funpay import parse_category
from app.collectors.kwork import _collect_catalog_urls, _collect_sitemap_urls, parse_kwork_category
from app.models import ScrapedItem


def test_risk_classifier_red():
    item = ScrapedItem(
        source="funpay",
        url="https://funpay.com/lots/1/",
        title="Купить аккаунт для игры",
    )

    risk_level, _ = analyze_risk(item)

    assert risk_level == "RED"


def test_opportunity_scorer_math():
    item = ScrapedItem(
        source="funpay",
        url="https://funpay.com/lots/2/",
        title="Настройка игрового профиля",
        price=Decimal("100"),
        currency="RUB",
    )

    opportunity = score_opportunity(
        item=item,
        risk_level="GREEN",
        risk_reason="Тестовый безопасный сценарий",
        mapping_data=None,
    )

    assert opportunity.sell_price == Decimal("175.00")
    assert opportunity.estimated_margin_percent == 40.0


def test_kwork_mapper_skips_red():
    item = ScrapedItem(
        source="funpay",
        url="https://funpay.com/lots/3/",
        title="Любой красный лот",
    )

    mapping = map_to_kwork(item, "RED")

    assert mapping is None


def test_opportunity_scorer_uses_kwork_competitors_for_demand():
    item = ScrapedItem(
        source="funpay",
        url="https://funpay.com/lots/4/",
        title="AI визуал",
        price=Decimal("100"),
        currency="RUB",
    )

    low = score_opportunity(
        item=item,
        risk_level="GREEN",
        risk_reason="Тест",
        mapping_data={"competitors_count": 1},
    )
    high = score_opportunity(
        item=item,
        risk_level="GREEN",
        risk_reason="Тест",
        mapping_data={"competitors_count": 25},
    )

    assert high.demand_weight > low.demand_weight
    assert high.opportunity_score > low.opportunity_score


def test_kwork_category_parser_collects_metrics_with_pagination(monkeypatch):
    pages = {
        "https://kwork.ru/categories/design": """
            <html><h1>Дизайн</h1>
            <aside class="filters"><a>Логотипы</a></aside>
            <article class="kwork-card">
                <a href="/kwork/1">Логотип</a>
                <span class="price">от 1 000 ₽</span>
                <a class="tag">брендинг</a>
            </article>
            <a rel="next" href="/categories/design?page=2">Следующая</a>
            </html>
        """,
        "https://kwork.ru/categories/design?page=2": """
            <html><h1>Дизайн</h1>
            <article class="kwork-card">
                <a href="/kwork/2">Баннер</a>
                <span class="price">2 000 ₽</span>
                <a class="tag">баннеры</a>
            </article>
            </html>
        """,
    }
    calls: list[str] = []

    def fake_get_html(self, url, *, source="kwork", force=False):
        calls.append(url)
        return pages[url]

    monkeypatch.setattr("app.collectors.kwork.HttpClient.get_html", fake_get_html)

    category, items = parse_kwork_category(
        "https://kwork.ru/categories/design",
        max_pages=3,
    )

    assert calls == [
        "https://kwork.ru/categories/design",
        "https://kwork.ru/categories/design?page=2",
    ]
    assert category.parse_status == "success"
    assert category.competitors_count == 2
    assert category.min_price == Decimal("1000")
    assert category.average_price == Decimal("1500.00")
    assert len(items) == 2
    assert items[0].source == "kwork"
    assert items[0].url == "https://kwork.ru/kwork/1"
    assert items[0].price == Decimal("1000")
    assert "брендинг" in category.keywords
    assert "Логотипы" in category.keywords


def test_funpay_category_parser_collects_features_with_pagination(monkeypatch):
    pages = {
        "https://funpay.com/lots/1/": """
            <html><body>
            <a class="tc-item" href="/lots/offer?id=1">
                <div class="tc-desc-text">Premium подписка по ID без пароля</div>
                <div class="tc-server">потребуется только ID профиля</div>
                <div class="tc-price">100 ₽</div>
            </a>
            <a rel="next" href="/lots/1/?page=2">Следующая</a>
            </body></html>
        """,
        "https://funpay.com/lots/1/?page=2": """
            <html><body>
            <a class="tc-item" href="/lots/offer?id=2">
                <div class="tc-desc-text">Настройка сервиса</div>
                <div class="tc-server">без доступа к аккаунту</div>
                <div class="tc-price">200 ₽</div>
            </a>
            </body></html>
        """,
    }

    def fake_get_html(self, url, *, source="funpay", force=False):
        return pages[url]

    monkeypatch.setattr("app.collectors.funpay.HttpClient.get_html", fake_get_html)

    items = parse_category("https://funpay.com/lots/1/")

    assert len(items) == 2
    assert items[0].is_subscription is True
    assert items[0].can_be_done_by_id is True
    assert items[0].requires_login_password is False
    assert items[1].is_service is True


def test_kwork_catalog_bfs_returns_only_leaf_categories(monkeypatch):
    pages = {
        "https://kwork.ru": """
            <html><body>
            <a href="/categories/design">Дизайн</a>
            <a href="/categories/texts">Тексты</a>
            </body></html>
        """,
        "https://kwork.ru/categories/design": """
            <html><body>
            <a href="/categories/logo">Логотипы</a>
            <a href="/categories/banners">Баннеры</a>
            </body></html>
        """,
        "https://kwork.ru/categories/texts": """
            <html><body>
            <a href="/categories/copywriting">Копирайтинг</a>
            </body></html>
        """,
        "https://kwork.ru/categories/logo": """
            <html><body>
            <article class="kwork-card">
                <a href="/kwork/1">Логотип</a>
                <span class="price">1 000 ₽</span>
            </article>
            </body></html>
        """,
        "https://kwork.ru/categories/banners": """
            <html><body>
            <article class="kwork-card">
                <a href="/kwork/2">Баннер</a>
                <span class="price">2 000 ₽</span>
            </article>
            </body></html>
        """,
        "https://kwork.ru/categories/copywriting": """
            <html><body>
            <article class="kwork-card">
                <a href="/kwork/3">Текст</a>
                <span class="price">3 000 ₽</span>
            </article>
            </body></html>
        """,
    }
    calls: list[str] = []

    def fake_get_html(self, url, *, source="kwork", force=False):
        calls.append(url)
        return pages[url]

    monkeypatch.setattr("app.collectors.kwork.HttpClient.get_html", fake_get_html)

    urls = _collect_catalog_urls(force=False, limit=2)

    assert urls == [
        "https://kwork.ru/categories/logo",
        "https://kwork.ru/categories/banners",
    ]
    assert "https://kwork.ru/categories/design" in calls
    assert len(calls) == len(set(calls))


def test_kwork_sitemap_prefers_market_categories_sitemap(monkeypatch):
    calls: list[str] = []

    def fake_get_html(self, url, *, source="kwork", force=False):
        calls.append(url)
        if url.endswith("sitemap_categories.xml"):
            return """
                <urlset>
                    <url><loc>https://kwork.ru/categories/logo</loc></url>
                    <url><loc>https://kwork.ru/categories/seo-audit</loc></url>
                </urlset>
            """
        raise AssertionError("Корневой sitemap не должен вызываться при успешном market sitemap")

    monkeypatch.setattr("app.collectors.kwork.HttpClient.get_html", fake_get_html)

    urls = _collect_sitemap_urls(force=False, limit=10)

    assert urls == [
        "https://kwork.ru/categories/logo",
        "https://kwork.ru/categories/seo-audit",
    ]
    assert calls == ["https://kwork.ru/sitemap/market_ru/sitemap_categories.xml"]


def test_kwork_category_marks_parse_failed_when_cards_are_missing(monkeypatch):
    def fake_get_html(self, url, *, source="kwork", force=False):
        return "<html><h1>Дизайн</h1><p>Нет карточек</p></html>"

    monkeypatch.setattr("app.collectors.kwork.HttpClient.get_html", fake_get_html)
    monkeypatch.setattr("app.collectors.kwork.config.KWORK_COOKIE", "invalid_cookie")

    category, items = parse_kwork_category("https://kwork.ru/categories/design")

    assert items == []
    assert category.parse_status == "parse_failed"
    assert category.competitors_count == 0
    assert category.parse_error == (
        "Карточки не найдены. Возможно, изменилась верстка сайта или "
        "требуется проверка браузера (капча)."
    )


def test_kwork_category_keeps_parse_failed_without_cookie(monkeypatch):
    def fake_get_html(self, url, *, source="kwork", force=False):
        return "<html><h1>Дизайн</h1><p>Нет карточек</p></html>"

    monkeypatch.setattr("app.collectors.kwork.HttpClient.get_html", fake_get_html)
    monkeypatch.setattr("app.collectors.kwork.config.KWORK_COOKIE", None)

    category, items = parse_kwork_category("https://kwork.ru/categories/design")

    assert items == []
    assert category.parse_status == "parse_failed"
    assert category.competitors_count == 0
