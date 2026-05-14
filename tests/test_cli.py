import pytest
from unittest.mock import ANY

import app.cli as cli
from app.collectors.funpay import FunPayCatalogEntry
from app.filters.funpay_games import (
    active_stop_categories,
    classify_game_category_reasons,
    is_game_related_category,
    is_game_related_item,
)
from app.cli import run_pipeline
from app.models import KworkCategory, ScrapedItem


def test_run_pipeline_uses_mocks_without_network_or_real_db(mocker):
    success_item = ScrapedItem(
        source="funpay",
        url="https://funpay.com/lots/ok/",
        title="Настройка профиля",
        price=100,
        currency="RUB",
        parse_status="success",
    )
    failed_item = ScrapedItem(
        source="funpay",
        url="https://funpay.com/lots/failed/",
        title="FunPay: ошибка сбора лотов",
        parse_status="parse_failed",
        parse_error="Не найден обязательный селектор",
    )

    init_db_mock = mocker.patch("app.cli.init_db")
    parse_category_mock = mocker.patch(
        "app.cli.parse_category",
        return_value=[success_item, failed_item],
    )
    save_scraped_item_mock = mocker.patch(
        "app.cli.save_scraped_item",
        side_effect=[101, 102],
    )
    save_analysis_result_mock = mocker.patch(
        "app.cli.save_analysis_result",
        return_value=201,
    )
    save_opportunity_mock = mocker.patch("app.cli.save_opportunity", return_value=301)

    summary = run_pipeline("https://funpay.com/test-category/")

    assert summary == {
        "scraped": 2,
        "saved": 2,
        "analyzed": 1,
        "skipped": 1,
    }
    init_db_mock.assert_called_once_with()
    parse_category_mock.assert_called_once_with("https://funpay.com/test-category/")
    assert save_scraped_item_mock.call_count == 2
    save_opportunity_mock.assert_called_once()
    save_analysis_result_mock.assert_called_once()


def test_main_exports_without_url(mocker, caplog):
    caplog.set_level("INFO", logger="app.cli")
    run_pipeline_mock = mocker.patch("app.cli.run_pipeline")
    export_to_csv_mock = mocker.patch(
        "app.cli.export_to_csv",
        return_value="data/reports/report_test.csv",
    )
    mocker.patch("sys.argv", ["app.cli", "--export"])

    cli.main()

    run_pipeline_mock.assert_not_called()
    export_to_csv_mock.assert_called_once_with()
    assert "Генерация отчета по накопленной базе..." in caplog.text
    assert "Отчет сохранен: data/reports/report_test.csv" in caplog.text


def test_collect_kwork_saves_categories_and_items(mocker):
    category = KworkCategory(
        category_name="Kwork",
        url="https://kwork.ru/categories/test",
        parse_status="success",
    )
    item = ScrapedItem(
        source="kwork",
        url="https://kwork.ru/kwork/1",
        title="Kwork item",
        parse_status="success",
    )

    mocker.patch("app.cli.init_db")
    collect_catalog_mock = mocker.patch(
        "app.cli.collect_kwork_catalog",
        return_value=([category], [item]),
    )
    save_kwork_category_mock = mocker.patch("app.cli.save_kwork_category")
    save_scraped_item_mock = mocker.patch("app.cli.save_scraped_item")

    saved = cli.collect_kwork(force=True)

    assert saved == 1
    collect_catalog_mock.assert_called_once_with(
        force=True,
        limit=cli.config.KWORK_MAX_CATEGORIES,
    )
    save_kwork_category_mock.assert_called_once_with(category)
    save_scraped_item_mock.assert_called_once_with(item)


def test_collect_funpay_skips_game_related_goods(mocker):
    game_item = ScrapedItem(
        source="funpay",
        url="https://funpay.com/lots/roblox/1",
        title="Roblox робуксы",
        category="Roblox",
        parse_status="success",
    )
    service_item = ScrapedItem(
        source="funpay",
        url="https://funpay.com/lots/services/1",
        title="Настройка описания",
        category="Цифровые услуги",
        parse_status="success",
    )

    mocker.patch("app.cli.init_db")
    mocker.patch(
        "app.cli.collect_funpay_catalog_entries",
        return_value=[
            FunPayCatalogEntry(
                url="https://funpay.com/lots/roblox/",
                name="Roblox",
                text="Roblox Робуксы Аккаунты Скины Донат",
            ),
            FunPayCatalogEntry(
                url="https://funpay.com/lots/services/",
                name="ChatGPT",
                text="ChatGPT Аккаунты Подписка Услуги",
            ),
        ],
    )
    parse_category_mock = mocker.patch(
        "app.cli.parse_category",
        return_value=[game_item, service_item],
    )
    save_funpay_category_mock = mocker.patch("app.cli.save_funpay_category")
    save_scraped_item_mock = mocker.patch("app.cli.save_scraped_item")

    saved = cli.collect_funpay(force=True)

    assert saved == 1
    parse_category_mock.assert_called_once_with(
        "https://funpay.com/lots/services/",
        force=True,
        category_name="ChatGPT",
        progress_callback=ANY,
    )
    save_funpay_category_mock.assert_called_once()
    save_scraped_item_mock.assert_called_once_with(service_item)


def test_funpay_game_filter_keeps_ai_and_blocks_game_categories():
    assert not is_game_related_category(
        name="ChatGPT",
        text="ChatGPT Аккаунты Подписка Прочее",
        url="https://funpay.com/lots/chatgpt/",
    )
    assert not is_game_related_category(
        name="Gemini",
        text="Gemini Аккаунты Услуги Подписка",
        url="https://funpay.com/lots/gemini/",
    )
    assert is_game_related_category(
        name="Roblox",
        text="Roblox Робуксы Подарочные карты Донат Аккаунты Скины",
        url="https://funpay.com/lots/roblox/",
    )
    assert is_game_related_category(
        name="Blum",
        text="Blum Рефералы Услуги Прочее",
        url="https://funpay.com/lots/blum/",
    )
    assert is_game_related_category(
        name="Counter-Strike 2",
        text="Counter-Strike 2 Аккаунты Prime Скины Кейсы Буст Обучение",
        url="https://funpay.com/lots/cs2/",
    )


def test_funpay_game_filter_skips_telegram_games_but_keeps_subscription():
    game_item = ScrapedItem(
        source="funpay",
        url="https://funpay.com/lots/telegram/1",
        title="Telegram Игры",
        category="Telegram",
        subcategory="Игры",
    )
    subscription_item = ScrapedItem(
        source="funpay",
        url="https://funpay.com/lots/chatgpt/1",
        title="ChatGPT подписка",
        category="ChatGPT",
        subcategory="Подписка",
    )

    assert is_game_related_item(game_item)
    assert not is_game_related_item(subscription_item)


def test_previous_game_markers_are_visible_in_stop_categories():
    stop_categories = set(active_stop_categories())

    assert {"CS2", "CSGO", "Dota", "Dota2", "WOT"}.issubset(stop_categories)
    assert {"Скины", "Робуксы", "Игровая валюта", "Twitch Drops"}.issubset(stop_categories)


def test_funpay_game_filter_blocks_category_by_lots_id(monkeypatch):
    monkeypatch.setattr(
        "app.filters.funpay_games.load_funpay_stop_categories",
        lambda: {
            "enabled": True,
            "categories": [],
            "disabled_categories": [],
            "category_ids": {"Roblox": "130"},
        },
    )

    reasons = classify_game_category_reasons(
        name="Неизвестная категория",
        text="",
        url="https://funpay.com/lots/130/",
    )

    assert reasons == ["стоп-ID категории FunPay"]


def test_funpay_game_filter_blocks_nested_category_ids(monkeypatch):
    monkeypatch.setattr(
        "app.filters.funpay_games.load_funpay_stop_categories",
        lambda: {
            "enabled": True,
            "categories": [],
            "disabled_categories": [],
            "category_ids": {"Age of Mythology: Retold": ["2725", "2726", "2727"]},
        },
    )

    reasons = classify_game_category_reasons(
        name="Ключи",
        text="",
        url="https://funpay.com/lots/2727/",
    )

    assert reasons == ["стоп-ID категории FunPay"]


def test_main_errors_without_url_or_export(mocker):
    mocker.patch("sys.argv", ["app.cli"])

    with pytest.raises(SystemExit) as error:
        cli.main()

    assert error.value.code == 2
