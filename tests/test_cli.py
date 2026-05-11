import pytest

import app.cli as cli
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


def test_main_errors_without_url_or_export(mocker):
    mocker.patch("sys.argv", ["app.cli"])

    with pytest.raises(SystemExit) as error:
        cli.main()

    assert error.value.code == 2
