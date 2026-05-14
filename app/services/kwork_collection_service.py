"""Сервис сбора и сохранения каталога Kwork."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.collectors.kwork import collect_catalog as collect_kwork_catalog
from app.config import config
from app.db import (
    init_db,
    save_kwork_category,
    save_parse_error,
    save_run_history,
    save_scraped_item,
)
from app.models import KworkCategory, ParseError, RunHistory, ScrapedItem

ProgressCallback = Callable[[int, int, str], None]


def collect_kwork_with_summary(
    force: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, object]:
    """Собирает категории и услуги Kwork, сохраняет их и возвращает summary."""

    init_db()
    started_at = datetime.now(timezone.utc)
    processed = 0
    try:
        categories, items = collect_kwork_catalog(
            force=force,
            limit=config.KWORK_MAX_CATEGORIES,
            progress_callback=progress_callback,
        )
        processed = len(categories)
        _report_progress(progress_callback, 95, 100, "Сохраняю категории и услуги Kwork в базу...")
        summary = save_kwork_collection_summary(categories, items)
        save_run_history(
            RunHistory(
                command="ui:collect-kwork",
                status="success",
                started_at=started_at,
                processed_count=processed,
            )
        )
        return summary
    except Exception as error:
        save_run_history(
            RunHistory(
                command="ui:collect-kwork",
                status="failed",
                started_at=started_at,
                processed_count=processed,
                error=str(error),
            )
        )
        raise


def _report_progress(
    progress_callback: ProgressCallback | None,
    current: int,
    total: int,
    message: str,
) -> None:
    if progress_callback is not None:
        progress_callback(current, total, message)


def save_kwork_collection_summary(
    categories: list[KworkCategory],
    items: list[ScrapedItem],
) -> dict[str, object]:
    """Сохраняет результат Kwork collector и возвращает счетчики для UI/CLI."""

    categories_saved = 0
    services_saved = 0
    parse_errors = 0

    for category in categories:
        save_kwork_category(category)
        categories_saved += 1

        if category.parse_status != "success" and category.parse_error:
            save_parse_error(
                ParseError(
                    source="kwork",
                    url=category.url,
                    status=category.parse_status,
                    error=category.parse_error,
                )
            )
            parse_errors += 1

    for item in items:
        if not _is_saveable_kwork_service(item):
            if item.parse_status != "success" and item.parse_error:
                save_parse_error(
                    ParseError(
                        source="kwork",
                        url=item.url,
                        status=item.parse_status,
                        error=item.parse_error,
                    )
                )
                parse_errors += 1
            continue

        save_scraped_item(item)
        services_saved += 1

        if item.parse_status != "success" and item.parse_error:
            save_parse_error(
                ParseError(
                    source="kwork",
                    url=item.url,
                    status=item.parse_status,
                    error=item.parse_error,
                )
            )
            parse_errors += 1

    return {
        "categories_saved": categories_saved,
        "services_saved": services_saved,
        "parse_errors": parse_errors,
        "debug_files": _list_kwork_debug_files(),
    }


def _is_saveable_kwork_service(item: ScrapedItem) -> bool:
    return bool(
        item.source == "kwork"
        and item.parse_status == "success"
        and item.title
        and item.url.startswith(("http://", "https://"))
    )


def _list_kwork_debug_files() -> list[str]:
    debug_dir = config.DATA_DIR / "debug" / "kwork"
    if not debug_dir.exists():
        return []

    files: list[Path] = [
        path for path in debug_dir.iterdir() if path.is_file() and path.suffix in {".html", ".txt"}
    ]
    files.sort(key=lambda path: path.stat().st_mtime)
    return [str(path) for path in files]


__all__ = ["collect_kwork_with_summary", "save_kwork_collection_summary"]
