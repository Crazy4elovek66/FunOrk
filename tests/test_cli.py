import pytest

import app.cli as cli
from app.cli import run_pipeline
from app.models import ScrapedItem


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
    save_analysis_result_mock.assert_called_once()


def test_main_exports_without_url(mocker, capsys):
    run_pipeline_mock = mocker.patch("app.cli.run_pipeline")
    export_to_csv_mock = mocker.patch(
        "app.cli.export_to_csv",
        return_value="data/reports/report_test.csv",
    )
    mocker.patch("sys.argv", ["app.cli", "--export"])

    cli.main()

    run_pipeline_mock.assert_not_called()
    export_to_csv_mock.assert_called_once_with()
    output = capsys.readouterr().out
    assert "Генерация отчета по накопленной базе..." in output
    assert "Отчет сохранен: data/reports/report_test.csv" in output


def test_main_errors_without_url_or_export(mocker):
    mocker.patch("sys.argv", ["app.cli"])

    with pytest.raises(SystemExit) as error:
        cli.main()

    assert error.value.code == 2
