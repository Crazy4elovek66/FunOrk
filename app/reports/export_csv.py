"""CSV-экспорт отчета FunOrk."""

from __future__ import annotations

import csv
from pathlib import Path

from app.config import config
from app.reports.common import REPORT_COLUMNS, REPORT_LABELS, load_report_rows, report_filename


def export_to_csv() -> Path:
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = config.REPORTS_DIR / report_filename("csv")

    with report_path.open("w", encoding=config.REPORT_SETTINGS.encoding, newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=REPORT_COLUMNS,
            delimiter=config.REPORT_SETTINGS.csv_delimiter,
        )
        writer.writerow({column: REPORT_LABELS[column] for column in REPORT_COLUMNS})
        writer.writerows(load_report_rows())

    return report_path
