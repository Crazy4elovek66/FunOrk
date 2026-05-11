import csv

from app.db import (
    fetch_kwork_categories,
    fetch_opportunities,
    fetch_scraped_items,
    init_db,
    save_opportunity,
    save_scraped_item,
)
from app.importers import import_funpay_file, import_kwork_file
from app.models import Opportunity, ScrapedItem


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


def test_import_kwork_service_csv(tmp_path, monkeypatch):
    db_path = tmp_path / "analyzer.sqlite"
    monkeypatch.setattr("app.db.config.DATABASE_PATH", db_path)
    init_db(db_path)

    csv_path = tmp_path / "kwork_services.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["url", "title", "price", "currency", "description", "category", "subcategory"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "url": "https://kwork.ru/kwork/1",
                "title": "Помощь с Gemini",
                "price": "1500",
                "currency": "RUB",
                "description": "Настройка промптов",
                "category": "AI-услуги",
                "subcategory": "Нейросети",
            }
        )

    assert import_kwork_file(csv_path) == 1
    rows = fetch_scraped_items(db_path, source="kwork")
    assert rows[0]["title"] == "Помощь с Gemini"
    assert rows[0]["is_service"] == 1


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


def test_save_opportunity_updates_existing_source_url(tmp_path):
    db_path = tmp_path / "analyzer.sqlite"
    init_db(db_path)

    source_url = "https://funpay.com/lots/test/1/"
    first_id = save_opportunity(
        Opportunity(
            source_category="FunPay",
            source_url=source_url,
            normalized_type="services",
            risk_level="YELLOW",
            risk_reason="Нужна ручная проверка",
            verdict="Проверить",
            opportunity_score=40,
        ),
        db_path,
    )
    second_id = save_opportunity(
        Opportunity(
            source_category="FunPay",
            source_url=source_url,
            normalized_type="services",
            possible_kwork_service_title="Настройка профиля",
            risk_level="GREEN",
            risk_reason="Без передачи доступов",
            verdict="Можно адаптировать",
            opportunity_score=82,
        ),
        db_path,
    )

    rows = fetch_opportunities(db_path)
    assert first_id == second_id
    assert len(rows) == 1
    assert rows[0]["verdict"] == "Можно адаптировать"
    assert rows[0]["opportunity_score"] == 82
