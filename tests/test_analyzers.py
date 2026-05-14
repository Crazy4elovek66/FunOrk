from decimal import Decimal

from app.analyzers.kwork_mapper import map_to_kwork
from app.analyzers.opportunity_scorer import score_opportunity
from app.analyzers.risk_classifier import analyze_risk
from app.collectors.funpay import collect_catalog_entries, collect_catalog_id_groups, parse_category
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
            <div data-id="1" class="js-kwork-card kwork-card-item">
                <div class="kwork-card-item__title"><a href="/logo/1/logotip"><span class="first-letter">Логотип</span></a></div>
                <div class="kwork-card-item__info-price"><span class="price-wrap__value">от 1 000 ₽</span></div>
                <a class="tag">брендинг</a>
            </div>
            <a rel="next" href="/categories/design?page=2">Следующая</a>
            </html>
        """,
        "https://kwork.ru/categories/design?page=2": """
            <html><h1>Дизайн</h1>
            <div data-id="2" class="js-kwork-card kwork-card-item">
                <div class="kwork-card-item__title"><a href="/banner/2/banner"><span class="first-letter">Баннер</span></a></div>
                <div class="kwork-card-item__info-price"><span class="price-wrap__value">2 000 ₽</span></div>
                <a class="tag">баннеры</a>
            </div>
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
    assert items[0].url == "https://kwork.ru/logo/1/logotip"
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
    assert items[0].category == "FunPay"
    assert items[0].category_id == "1"
    assert items[1].is_service is True


def test_funpay_parser_uses_catalog_category_name_when_provided(monkeypatch):
    def fake_get_html(self, url, *, source="funpay", force=False):
        return """
            <html><body>
            <a class="tc-item" href="/lots/offer?id=1">
                <div class="tc-desc-text">Premium подписка по ID без пароля</div>
                <div class="tc-server">потребуется только ID профиля</div>
                <div class="tc-price">100 ₽</div>
            </a>
            </body></html>
        """

    monkeypatch.setattr("app.collectors.funpay.HttpClient.get_html", fake_get_html)

    items = parse_category("https://funpay.com/lots/1/", category_name="ChatGPT")

    assert items[0].category == "ChatGPT"
    assert items[0].category_id == "1"


def test_funpay_catalog_collects_main_category_names_and_ids(monkeypatch):
    def fake_get_html(self, url, *, source="funpay", force=False):
        return """
            <html><body>
            <div class="game-title" data-id="754">
                <a href="/lots/3486/">Abyss of Dungeons</a>
            </div>
            <div class="game-categories">
                <a href="/lots/3487/">Донат</a>
                <a href="/lots/3488/">Прочее</a>
            </div>
            <div class="game-title" data-id="155">
                <a href="/lots/706/">ChatGPT</a>
            </div>
            </body></html>
        """

    monkeypatch.setattr("app.collectors.funpay.HttpClient.get_html", fake_get_html)

    entries = collect_catalog_entries(force=True, limit=10)

    assert [entry.name for entry in entries] == ["Abyss of Dungeons", "ChatGPT"]
    assert [entry.category_id for entry in entries] == ["3486", "706"]
    assert entries[0].group_id == "754"


def test_funpay_catalog_id_groups_include_nested_lot_ids(monkeypatch):
    def fake_get_html(self, url, *, source="funpay", force=False):
        return """
            <html><body>
            <div class="game-title" data-id="754">
                <a href="/lots/3486/">Abyss of Dungeons</a>
            </div>
            <div class="game-categories">
                <a href="/lots/3487/">Донат</a>
                <a href="/lots/3490/">Прочее</a>
            </div>
            <div class="game-title" data-id="941">
                <a href="/lots/2725/">Age of Mythology: Retold</a>
            </div>
            <div class="game-categories">
                <a href="/lots/2726/">Аккаунты</a>
                <a href="/lots/2727/">Ключи</a>
                <a href="/lots/2728/">Оффлайн активации</a>
                <a href="/lots/2729/">Game Pass</a>
                <a href="/lots/2730/">Прочее</a>
            </div>
            </body></html>
        """

    monkeypatch.setattr("app.collectors.funpay.HttpClient.get_html", fake_get_html)

    groups = collect_catalog_id_groups(force=True, limit=10)
    groups_by_name = {group.name: group for group in groups}

    assert groups_by_name["Abyss of Dungeons"].category_ids == ("3486", "3487", "3490")
    assert groups_by_name["Age of Mythology: Retold"].category_ids == (
        "2725",
        "2726",
        "2727",
        "2728",
        "2729",
        "2730",
    )


def test_funpay_parser_splits_lot_title_and_subcategory(monkeypatch):
    def fake_get_html(self, url, *, source="funpay", force=False):
        return """
            <html><body>
            <a class="tc-item" href="/lots/offer?id=1">
                <div class="tc-desc-text">VK комментарии живыми людьми, Комментарии</div>
                <div class="tc-server">VK</div>
                <div class="tc-price">10 ₽</div>
            </a>
            </body></html>
        """

    monkeypatch.setattr("app.collectors.funpay.HttpClient.get_html", fake_get_html)

    items = parse_category("https://funpay.com/lots/706/")

    assert items[0].title == "VK комментарии живыми людьми"
    assert items[0].subcategory == "Комментарии"
    assert items[0].category == "FunPay"
    assert items[0].category_id == "706"


def test_funpay_parser_marks_account_slang_as_red(monkeypatch):
    def fake_get_html(self, url, *, source="funpay", force=False):
        return """
            <html><body>
            <a class="tc-item" href="/lots/offer?id=3">
                <div class="tc-desc-text">Продам аккаунт с родной почтой, лог:пасс выдам</div>
                <div class="tc-server">Аккаунты</div>
                <div class="tc-price">500 ₽</div>
            </a>
            </body></html>
        """

    monkeypatch.setattr("app.collectors.funpay.HttpClient.get_html", fake_get_html)

    items = parse_category("https://funpay.com/lots/accounts/")
    risk_level, risk_reason = analyze_risk(items[0])

    assert items[0].requires_login_password is True
    assert risk_level == "RED"
    assert "логин" in risk_reason


def test_kwork_mapper_recognizes_smm_services():
    item = ScrapedItem(
        source="funpay",
        url="https://funpay.com/lots/offer?id=1",
        title="VK комментарии живыми людьми",
        subcategory="Комментарии",
        category="VK",
        price=Decimal("10"),
    )

    risk_level, _ = analyze_risk(item)
    mapping = map_to_kwork(item, risk_level)

    assert risk_level == "YELLOW"
    assert mapping is not None
    assert mapping["normalized_type"] == "smm_services"
    assert mapping["kwork_category"] == "Соцсети и SMM"


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
            <div data-id="1" class="js-kwork-card kwork-card-item">
                <div class="kwork-card-item__title"><a href="/logo/1/logotip"><span class="first-letter">Логотип</span></a></div>
                <div class="kwork-card-item__info-price"><span class="price-wrap__value">1 000 ₽</span></div>
            </div>
            </body></html>
        """,
        "https://kwork.ru/categories/banners": """
            <html><body>
            <div data-id="2" class="js-kwork-card kwork-card-item">
                <div class="kwork-card-item__title"><a href="/banner/2/banner"><span class="first-letter">Баннер</span></a></div>
                <div class="kwork-card-item__info-price"><span class="price-wrap__value">2 000 ₽</span></div>
            </div>
            </body></html>
        """,
        "https://kwork.ru/categories/copywriting": """
            <html><body>
            <div data-id="3" class="js-kwork-card kwork-card-item">
                <div class="kwork-card-item__title"><a href="/text/3/text"><span class="first-letter">Текст</span></a></div>
                <div class="kwork-card-item__info-price"><span class="price-wrap__value">3 000 ₽</span></div>
            </div>
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
    assert "карточки услуг не найдены" in category.parse_error.casefold()


def test_kwork_category_keeps_parse_failed_without_cookie(monkeypatch):
    def fake_get_html(self, url, *, source="kwork", force=False):
        return "<html><h1>Дизайн</h1><p>Нет карточек</p></html>"

    monkeypatch.setattr("app.collectors.kwork.HttpClient.get_html", fake_get_html)
    monkeypatch.setattr("app.collectors.kwork.config.KWORK_COOKIE", None)

    category, items = parse_kwork_category("https://kwork.ru/categories/design")

    assert items == []
    assert category.parse_status == "parse_failed"
    assert category.competitors_count == 0
