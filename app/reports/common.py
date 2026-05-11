"""Общие данные для CSV/XLSX/HTML отчетов."""

from __future__ import annotations

import csv

from app.config import config
from app.db import get_connection

REPORT_COLUMNS = (
    "source_url",
    "source_category",
    "source_subcategory",
    "normalized_type",
    "possible_kwork_service_title",
    "possible_kwork_category",
    "buy_price",
    "sell_price",
    "estimated_margin_percent",
    "risk_level",
    "risk_reason",
    "moderation_risk",
    "dispute_risk",
    "demand_weight",
    "margin_weight",
    "risk_weight",
    "opportunity_score",
    "verdict",
    "recommendation",
    "forbidden_words",
    "safe_wording",
    "buyer_requirements",
    "forbidden_buyer_requests",
    "report_format",
    "created_at",
)

REPORT_LABELS = {
    "source_url": "Источник",
    "source_category": "Категория FunPay",
    "source_subcategory": "Подкатегория FunPay",
    "normalized_type": "Тип",
    "possible_kwork_service_title": "Безопасное название услуги",
    "possible_kwork_category": "Категория Kwork",
    "buy_price": "Цена закупки",
    "sell_price": "Цена продажи",
    "estimated_margin_percent": "Маржа, %",
    "risk_level": "Риск",
    "risk_reason": "Причина риска",
    "moderation_risk": "Риск модерации",
    "dispute_risk": "Риск спора",
    "demand_weight": "Вес спроса",
    "margin_weight": "Вес маржи",
    "risk_weight": "Вес риска",
    "opportunity_score": "Итоговый балл",
    "verdict": "Вердикт",
    "recommendation": "Рекомендация",
    "forbidden_words": "Запрещенные слова",
    "safe_wording": "Безопасная формулировка",
    "buyer_requirements": "Что запросить у покупателя",
    "forbidden_buyer_requests": "Что нельзя просить у покупателя",
    "report_format": "Формат результата",
    "created_at": "Дата анализа",
}

HTML_BLOCKS = (
    ("Лучшие направления для теста", "можно брать в тест"),
    ("Можно тестировать осторожно", "тестировать осторожно"),
    ("Требуется ручной анализ", "только ручная проверка"),
    ("Не брать", "не брать"),
)

KWORK_SERVICE_COLUMNS = (
    "url",
    "title",
    "price",
    "currency",
    "category",
    "subcategory",
    "description",
    "scraped_at",
)

KWORK_SERVICE_LABELS = {
    "url": "Ссылка",
    "title": "Услуга",
    "price": "Цена",
    "currency": "Валюта",
    "category": "Категория",
    "subcategory": "Подкатегория",
    "description": "Описание",
    "scraped_at": "Дата сбора",
}


def load_report_rows() -> list[dict[str, object]]:
    with get_connection() as connection:
        opportunity_count = connection.execute(
            "SELECT COUNT(*) AS count FROM opportunities"
        ).fetchone()["count"]
        if opportunity_count:
            rows = connection.execute(
                """
                SELECT *
                FROM opportunities
                ORDER BY opportunity_score DESC, created_at DESC
                """
            ).fetchall()
            return [{column: row[column] for column in REPORT_COLUMNS} for row in rows]

        rows = connection.execute(
            """
            SELECT
                si.url AS source_url,
                si.category AS source_category,
                si.subcategory AS source_subcategory,
                'other' AS normalized_type,
                si.title AS possible_kwork_service_title,
                NULL AS possible_kwork_category,
                si.price AS buy_price,
                NULL AS sell_price,
                NULL AS estimated_margin_percent,
                ar.risk_level,
                ar.risk_reason,
                NULL AS moderation_risk,
                NULL AS dispute_risk,
                NULL AS demand_weight,
                NULL AS margin_weight,
                NULL AS risk_weight,
                ar.score AS opportunity_score,
                ar.verdict,
                ar.recommendation,
                '[]' AS forbidden_words,
                NULL AS safe_wording,
                NULL AS buyer_requirements,
                NULL AS forbidden_buyer_requests,
                NULL AS report_format,
                ar.created_at
            FROM analysis_results ar
            JOIN scraped_items si ON si.id = ar.item_id
            ORDER BY ar.score DESC, ar.created_at DESC
            """
        ).fetchall()
        return [{column: row[column] for column in REPORT_COLUMNS} for row in rows]


def report_filename(extension: str) -> str:
    from datetime import datetime

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"report_{timestamp}.{extension}"


def load_kwork_service_rows() -> list[dict[str, object]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT url, title, price, currency, category, subcategory, description, scraped_at
            FROM scraped_items
            WHERE source = 'kwork' AND parse_status = 'success'
            ORDER BY scraped_at DESC, id DESC
            """
        ).fetchall()
        return [{column: row[column] for column in KWORK_SERVICE_COLUMNS} for row in rows]


def export_kwork_services_csv() -> None:
    rows = load_kwork_service_rows()
    if not rows:
        return

    from datetime import datetime

    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.REPORTS_DIR / f"kwork_services_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with path.open("w", encoding=config.REPORT_SETTINGS.encoding, newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=KWORK_SERVICE_COLUMNS,
            delimiter=config.REPORT_SETTINGS.csv_delimiter,
        )
        writer.writerow({column: KWORK_SERVICE_LABELS[column] for column in KWORK_SERVICE_COLUMNS})
        writer.writerows(rows)
