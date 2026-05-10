"""CSV-экспорт результатов анализа для просмотра в Excel."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from app.config import config
from app.db import get_connection


REPORT_COLUMNS = (
    "url",
    "title",
    "price",
    "currency",
    "category",
    "subcategory",
    "risk_level",
    "score",
    "verdict",
    "is_recommended",
    "risk_reason",
    "recommendation",
    "scraped_at",
    "analyzed_at",
)


def export_to_csv() -> Path:
    """Выгружает все проанализированные лоты в CSV, отсортированные по score DESC."""

    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = config.REPORTS_DIR / _report_filename()

    rows = _load_report_rows()
    with report_path.open(
        "w",
        encoding=config.REPORT_SETTINGS.encoding,
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=REPORT_COLUMNS,
            delimiter=config.REPORT_SETTINGS.csv_delimiter,
        )
        writer.writeheader()
        writer.writerows(rows)

    return report_path


def _load_report_rows() -> list[dict[str, object]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                si.url,
                si.title,
                si.price,
                si.currency,
                si.category,
                si.subcategory,
                ar.risk_level,
                ar.score,
                ar.verdict,
                ar.is_recommended,
                ar.risk_reason,
                ar.recommendation,
                si.scraped_at,
                ar.created_at AS analyzed_at
            FROM analysis_results ar
            JOIN scraped_items si ON si.id = ar.item_id
            ORDER BY ar.score DESC, ar.created_at DESC
            """
        ).fetchall()

    return [dict(row) for row in rows]


def _report_filename() -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"report_{timestamp}.csv"
