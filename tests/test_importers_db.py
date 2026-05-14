import csv

from app.db import (
    clear_funpay_work_table,
    clear_kwork_work_table,
    clear_opportunities_work_table,
    clean_funpay_table_by_current_filters,
    clean_kwork_table_by_current_filters,
    clean_opportunities_table_by_current_filters,
    fetch_adapted_kwork_card,
    fetch_kwork_categories,
    fetch_opportunities,
    fetch_scraped_items,
    get_connection,
    init_db,
    save_adapted_kwork_card,
    save_opportunity,
    save_scraped_item,
    update_opportunity_status,
)
from app.adapters.kwork_card_generator import generate_adapted_kwork_card
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


def test_save_adapted_kwork_card_and_status(tmp_path):
    db_path = tmp_path / "analyzer.sqlite"
    init_db(db_path)
    opportunity_id = save_opportunity(
        Opportunity(
            source_category="FunPay",
            source_url="https://funpay.com/lots/test/2/",
            normalized_type="services",
            possible_kwork_service_title="Настройка описания",
            possible_kwork_category="Тексты",
            sell_price=1500,
            risk_level="GREEN",
            risk_reason="Без передачи доступов",
            verdict="Можно адаптировать",
            opportunity_score=82,
        ),
        db_path,
    )
    opportunity = dict(fetch_opportunities(db_path)[0])

    card = generate_adapted_kwork_card(opportunity)
    card_id = save_adapted_kwork_card(card, db_path)
    update_opportunity_status(opportunity_id, "in_progress", db_path)

    stored_card = fetch_adapted_kwork_card(opportunity_id, db_path)
    stored_opportunity = fetch_opportunities(db_path)[0]

    assert card_id == stored_card["id"]
    assert stored_card["title"] == "Настройка описания"
    assert stored_card["compliance_status"] == "ready"
    assert stored_opportunity["processing_status"] == "in_progress"


def test_adapted_kwork_card_marks_risky_text_for_manual_review(tmp_path):
    db_path = tmp_path / "analyzer.sqlite"
    init_db(db_path)
    save_opportunity(
        Opportunity(
            source_category="FunPay",
            source_url="https://funpay.com/lots/test/3/",
            normalized_type="services",
            possible_kwork_service_title="Настройка VPN",
            risk_level="YELLOW",
            risk_reason="Нужна проверка",
            verdict="Проверить",
            opportunity_score=50,
        ),
        db_path,
    )
    opportunity = dict(fetch_opportunities(db_path)[0])

    card = generate_adapted_kwork_card(opportunity)

    assert card.compliance_status == "needs_manual_review"
    assert "vpn" in card.compliance_markers


def test_clean_table_helpers_keep_valid_rows_and_remove_filtered_rows(tmp_path):
    db_path = tmp_path / "analyzer.sqlite"
    init_db(db_path)
    save_scraped_item(
        ScrapedItem(
            source="funpay",
            url="https://funpay.com/lots/roblox/1/",
            title="Roblox робуксы",
            category="Roblox",
            category_id="130",
        ),
        db_path,
    )
    save_scraped_item(
        ScrapedItem(
            source="funpay",
            url="https://funpay.com/lots/chatgpt/1/",
            title="ChatGPT подписка",
            category="ChatGPT",
        ),
        db_path,
    )
    save_scraped_item(
        ScrapedItem(
            source="kwork",
            url="https://kwork.ru/kwork/clear/1",
            title="Kwork item",
        ),
        db_path,
    )
    opportunity_id = save_opportunity(
        Opportunity(
            source_category="FunPay",
            source_url="https://funpay.com/lots/roblox/1/",
            normalized_type="services",
            risk_level="GREEN",
            risk_reason="Тест",
            verdict="Можно",
        ),
        db_path,
    )

    funpay_report = clean_funpay_table_by_current_filters(db_path)
    assert funpay_report["deleted"] == 1
    assert funpay_report["reasons"]["стоп-категория FunPay"] == 1
    assert funpay_report["reasons"]["игровая валюта"] == 1
    funpay_rows = fetch_scraped_items(db_path, source="funpay")
    assert len(funpay_rows) == 1
    assert funpay_rows[0]["category"] == "ChatGPT"
    assert len(fetch_scraped_items(db_path, source="kwork")) == 1

    assert len(fetch_opportunities(db_path)) == 0
    opportunity_report = clean_opportunities_table_by_current_filters(db_path)
    assert opportunity_report["deleted"] == 0
    assert len(fetch_opportunities(db_path)) == 0

    kwork_report = clean_kwork_table_by_current_filters(db_path)
    assert kwork_report["deleted"] == 0
    assert len(fetch_scraped_items(db_path, source="kwork")) == 1


def test_clean_funpay_table_uses_saved_category_id(tmp_path, monkeypatch):
    db_path = tmp_path / "analyzer.sqlite"
    init_db(db_path)
    monkeypatch.setattr(
        "app.filters.funpay_games.load_funpay_stop_categories",
        lambda: {
            "enabled": True,
            "categories": [],
            "disabled_categories": [],
            "category_ids": {"Age of Mythology: Retold": ["2725", "2727"]},
        },
    )
    save_scraped_item(
        ScrapedItem(
            source="funpay",
            url="https://funpay.com/orders/unknown/1/",
            title="Ключ Steam",
            category="Age of Mythology: Retold",
            category_id="2727",
        ),
        db_path,
    )
    save_scraped_item(
        ScrapedItem(
            source="funpay",
            url="https://funpay.com/orders/unknown/2/",
            title="Подписка",
            category="ChatGPT",
            category_id="706",
        ),
        db_path,
    )

    report = clean_funpay_table_by_current_filters(db_path)
    rows = fetch_scraped_items(db_path, source="funpay")

    assert report["deleted"] == 1
    assert len(rows) == 1
    assert rows[0]["category"] == "ChatGPT"
    assert rows[0]["category_id"] == "706"


def test_clean_funpay_table_uses_legacy_category_id_from_category_column(tmp_path, monkeypatch):
    db_path = tmp_path / "analyzer.sqlite"
    init_db(db_path)
    monkeypatch.setattr(
        "app.filters.funpay_games.load_funpay_stop_categories",
        lambda: {
            "enabled": True,
            "categories": [],
            "disabled_categories": [],
            "category_ids": {"Abyss of Dungeons": ["3486", "3487"]},
        },
    )
    save_scraped_item(
        ScrapedItem(
            source="funpay",
            url="https://funpay.com/orders/legacy/1/",
            title="Старый лот",
            category="FunPay 3487",
        ),
        db_path,
    )

    report = clean_funpay_table_by_current_filters(db_path)

    assert report["deleted"] == 1
    assert fetch_scraped_items(db_path, source="funpay") == []


def test_clean_funpay_table_deletes_large_batches(tmp_path, monkeypatch):
    db_path = tmp_path / "analyzer.sqlite"
    init_db(db_path)
    monkeypatch.setattr(
        "app.filters.funpay_games.load_funpay_stop_categories",
        lambda: {
            "enabled": True,
            "categories": [],
            "disabled_categories": [],
            "category_ids": {"Age of Mythology: Retold": ["2727"]},
        },
    )
    now = "2026-01-01T00:00:00+00:00"
    rows = [
        (
            "funpay",
            f"https://funpay.com/orders/batch/{index}/",
            f"Лот {index}",
            "Age of Mythology: Retold",
            "2727",
            "success",
            now,
        )
        for index in range(1200)
    ]
    opportunities = [
        (
            "Age of Mythology: Retold",
            f"https://funpay.com/orders/batch/{index}/",
            "services",
            "GREEN",
            "Тест",
            "Можно",
            now,
        )
        for index in range(1200)
    ]
    with get_connection(db_path) as connection:
        connection.executemany(
            """
            INSERT INTO scraped_items (
                source, url, title, category, category_id, parse_status, scraped_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        connection.executemany(
            """
            INSERT INTO opportunities (
                source_category, source_url, normalized_type, risk_level,
                risk_reason, verdict, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            opportunities,
        )

    report = clean_funpay_table_by_current_filters(db_path)

    assert report["deleted"] == 1200
    assert fetch_scraped_items(db_path, source="funpay") == []
    assert fetch_opportunities(db_path) == []


def test_scoped_full_clear_helpers_remove_only_selected_section(tmp_path):
    db_path = tmp_path / "analyzer.sqlite"
    init_db(db_path)
    save_scraped_item(
        ScrapedItem(
            source="funpay",
            url="https://funpay.com/lots/full-clear/1/",
            title="FunPay item",
        ),
        db_path,
    )
    save_scraped_item(
        ScrapedItem(
            source="kwork",
            url="https://kwork.ru/kwork/full-clear/1",
            title="Kwork item",
        ),
        db_path,
    )
    save_opportunity(
        Opportunity(
            source_category="FunPay",
            source_url="https://funpay.com/lots/full-clear/1/",
            normalized_type="services",
            risk_level="GREEN",
            risk_reason="Тест",
            verdict="Можно",
        ),
        db_path,
    )

    funpay_report = clear_funpay_work_table(db_path)

    assert funpay_report["FunPay лоты"] == 1
    assert funpay_report["Связанные выводы"] == 1
    assert fetch_scraped_items(db_path, source="funpay") == []
    assert len(fetch_scraped_items(db_path, source="kwork")) == 1
    assert fetch_opportunities(db_path) == []

    kwork_report = clear_kwork_work_table(db_path)

    assert kwork_report["Kwork услуги"] == 1
    assert fetch_scraped_items(db_path, source="kwork") == []

    save_opportunity(
        Opportunity(
            source_category="FunPay",
            source_url="https://funpay.com/lots/full-clear/2/",
            normalized_type="services",
            risk_level="GREEN",
            risk_reason="Тест",
            verdict="Можно",
        ),
        db_path,
    )

    comparison_report = clear_opportunities_work_table(db_path)

    assert comparison_report["Выводы сравнения"] == 1
    assert fetch_opportunities(db_path) == []
