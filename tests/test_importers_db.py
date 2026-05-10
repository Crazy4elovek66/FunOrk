import csv

from app.db import fetch_kwork_categories, fetch_scraped_items, init_db, save_scraped_item
from app.importers import import_funpay_file, import_kwork_file
from app.models import ScrapedItem


def test_import_funpay_csv(tmp_path, monkeypatch):
    db_path = tmp_path / "analyzer.sqlite"
    monkeypatch.setattr("app.db.config.DATABASE_PATH", db_path)

    csv_path = tmp_path / "funpay.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["url", "title", "price", "currency"])
        writer.writeheader()
        writer.writerow(
            {
                "url": "https://funpay.com/lots/test/",
                "title": "Настройка профиля",
                "price": "100",
                "currency": "RUB",
            }
        )

    assert import_funpay_file(csv_path) == 1
    rows = fetch_scraped_items(db_path)
    assert rows[0]["title"] == "Настройка профиля"
    assert rows[0]["price"] == "100"


def test_import_kwork_csv_and_db_schema(tmp_path, monkeypatch):
    db_path = tmp_path / "analyzer.sqlite"
    monkeypatch.setattr("app.db.config.DATABASE_PATH", db_path)
    init_db(db_path)

    csv_path = tmp_path / "kwork.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["url", "category_name", "competitors_count", "keywords"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "url": "https://kwork.ru/categories/ai",
                "category_name": "AI-услуги",
                "competitors_count": "25",
                "keywords": "ai, автоматизация",
            }
        )

    assert import_kwork_file(csv_path) == 1
    rows = fetch_kwork_categories(db_path)
    assert rows[0]["category_name"] == "AI-услуги"
    assert rows[0]["competitors_count"] == 25


def test_fetch_scraped_items_filters_source_and_limit(tmp_path):
    db_path = tmp_path / "analyzer.sqlite"
    init_db(db_path)
    save_scraped_item(
        ScrapedItem(
            source="funpay",
            url="https://funpay.com/lots/1/",
            title="FunPay lot",
        ),
        db_path,
    )
    save_scraped_item(
        ScrapedItem(
            source="kwork",
            url="https://kwork.ru/kwork/1",
            title="Kwork lot 1",
        ),
        db_path,
    )
    save_scraped_item(
        ScrapedItem(
            source="kwork",
            url="https://kwork.ru/kwork/2",
            title="Kwork lot 2",
        ),
        db_path,
    )

    rows = fetch_scraped_items(db_path, source="kwork", limit=1)

    assert len(rows) == 1
    assert rows[0]["source"] == "kwork"
