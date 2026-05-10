import csv
from decimal import Decimal

from app.db import init_db, save_analysis_result, save_scraped_item
from app.models import AnalysisResult, ScrapedItem
from app.reports import exporter


def test_export_to_csv_uses_temp_db_and_writes_excel_friendly_file(tmp_path):
    db_path = tmp_path / "analyzer.sqlite"
    reports_dir = tmp_path / "reports"
    exporter.config.DATABASE_PATH = db_path
    exporter.config.REPORTS_DIR = reports_dir

    init_db()
    first_item_id = save_scraped_item(
        ScrapedItem(
            source="funpay",
            url="https://funpay.com/lots/low/",
            title="Тестовый лот с низким скором",
            price=Decimal("100"),
            currency="RUB",
        )
    )
    second_item_id = save_scraped_item(
        ScrapedItem(
            source="funpay",
            url="https://funpay.com/lots/high/",
            title="Тестовый лот с высоким скором",
            price=Decimal("200"),
            currency="RUB",
        )
    )
    save_analysis_result(
        AnalysisResult(
            item_id=first_item_id,
            risk_level="YELLOW",
            score=40,
            verdict="только ручная проверка",
            recommendation="Проверить вручную",
        )
    )
    save_analysis_result(
        AnalysisResult(
            item_id=second_item_id,
            risk_level="GREEN",
            score=90,
            verdict="можно брать в тест",
            is_recommended=True,
            recommendation="Запустить тест",
        )
    )

    report_path = exporter.export_to_csv()

    assert report_path.parent == reports_dir
    assert report_path.name.startswith("report_")
    assert report_path.read_bytes().startswith(b"\xef\xbb\xbf")

    with report_path.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(
            csv.DictReader(
                file,
                delimiter=exporter.config.REPORT_SETTINGS.csv_delimiter,
            )
        )

    assert [row["url"] for row in rows] == [
        "https://funpay.com/lots/high/",
        "https://funpay.com/lots/low/",
    ]
    assert rows[0]["title"] == "Тестовый лот с высоким скором"
    assert rows[0]["price"] == "200"
    assert rows[0]["verdict"] == "можно брать в тест"
    assert rows[0]["score"] == "90.0"
    assert rows[0]["recommendation"] == "Запустить тест"
