"""SQLite-слой FunOrk: импорт, анализ, отчеты, кеш и ошибки парсинга."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator, TypeVar, TypedDict

from app.config import config
from app.models import (
    AdaptedKworkCard,
    AnalysisResult,
    FunPayCategory,
    KworkCategory,
    Opportunity,
    ParseError,
    RunHistory,
    ScrapedItem,
)
from app.filters.funpay_games import (
    classify_game_category_reasons,
    classify_game_item_reasons,
    is_game_related_category,
    is_game_related_item,
)

SCRAPED_ITEMS_UNIQUE_COLUMNS = ("source", "url")
SQLITE_DELETE_BATCH_SIZE = 500
ProgressCallback = Callable[[int, int, str], None]
T = TypeVar("T")


class CleanReport(TypedDict):
    deleted: int
    reasons: dict[str, int]


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or config.DATABASE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL;")
    connection.execute("PRAGMA foreign_keys=ON;")
    return connection


@contextmanager
def get_connection(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    connection = _connect(db_path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db(db_path: Path | None = None) -> None:
    """Создает таблицы и безопасно добавляет недостающие колонки."""

    with get_connection(db_path) as connection:
        table_exists = connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'scraped_items'
            """
        ).fetchone()

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS scraped_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                url TEXT NOT NULL,
                title TEXT NOT NULL,
                price TEXT,
                currency TEXT,
                description TEXT,
                category TEXT,
                category_id TEXT,
                subcategory TEXT,
                requires_login_password INTEGER NOT NULL DEFAULT 0,
                can_be_done_by_id INTEGER NOT NULL DEFAULT 0,
                is_code_or_key INTEGER NOT NULL DEFAULT 0,
                is_subscription INTEGER NOT NULL DEFAULT 0,
                is_service INTEGER NOT NULL DEFAULT 0,
                parse_status TEXT NOT NULL,
                parse_error TEXT,
                scraped_at TEXT NOT NULL,
                UNIQUE(source, url)
            )
            """
        )
        _ensure_column(connection, "scraped_items", "requires_login_password", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "scraped_items", "category_id", "TEXT")
        _ensure_column(connection, "scraped_items", "can_be_done_by_id", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "scraped_items", "is_code_or_key", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "scraped_items", "is_subscription", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "scraped_items", "is_service", "INTEGER NOT NULL DEFAULT 0")
        if table_exists:
            _ensure_scraped_items_unique_constraint(connection)

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS analysis_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                risk_level TEXT NOT NULL,
                score REAL NOT NULL,
                verdict TEXT NOT NULL,
                is_recommended INTEGER NOT NULL DEFAULT 0,
                risk_reason TEXT,
                recommendation TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(item_id) REFERENCES scraped_items(id) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS opportunities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                funpay_category_id INTEGER,
                source_category TEXT NOT NULL,
                source_subcategory TEXT,
                source_url TEXT NOT NULL,
                normalized_type TEXT NOT NULL,
                possible_kwork_service_title TEXT,
                possible_kwork_category TEXT,
                buy_price TEXT,
                sell_price TEXT,
                estimated_margin_percent REAL,
                risk_level TEXT NOT NULL,
                risk_reason TEXT NOT NULL,
                moderation_risk TEXT,
                dispute_risk TEXT,
                demand_weight REAL NOT NULL DEFAULT 0,
                margin_weight REAL NOT NULL DEFAULT 0,
                risk_weight REAL NOT NULL DEFAULT 0,
                opportunity_score REAL NOT NULL DEFAULT 0,
                verdict TEXT NOT NULL,
                processing_status TEXT NOT NULL DEFAULT 'new',
                recommendation TEXT,
                forbidden_words TEXT NOT NULL DEFAULT '[]',
                safe_wording TEXT,
                buyer_requirements TEXT,
                forbidden_buyer_requests TEXT,
                report_format TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(source_url)
            )
            """
        )
        _ensure_column(connection, "opportunities", "forbidden_buyer_requests", "TEXT")
        _ensure_column(connection, "opportunities", "processing_status", "TEXT NOT NULL DEFAULT 'new'")
        _ensure_opportunities_unique_index(connection)

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS adapted_kwork_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                opportunity_id INTEGER NOT NULL UNIQUE,
                title TEXT NOT NULL,
                kwork_category TEXT NOT NULL,
                image_prompt TEXT NOT NULL,
                description TEXT NOT NULL,
                buyer_requirements TEXT NOT NULL,
                base_price TEXT,
                extra_options TEXT NOT NULL DEFAULT '[]',
                faq TEXT NOT NULL DEFAULT '[]',
                risk_warnings TEXT NOT NULL,
                source_funpay_url TEXT NOT NULL,
                compliance_status TEXT NOT NULL,
                compliance_markers TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                FOREIGN KEY(opportunity_id) REFERENCES opportunities(id) ON DELETE CASCADE
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS funpay_categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_name TEXT NOT NULL,
                subcategory_name TEXT,
                url TEXT NOT NULL UNIQUE,
                raw_type TEXT,
                normalized_type TEXT,
                min_price TEXT,
                median_price TEXT,
                currency TEXT,
                offers_count INTEGER,
                delivery_method TEXT,
                requires_login_password INTEGER NOT NULL DEFAULT 0,
                can_be_done_by_id INTEGER NOT NULL DEFAULT 0,
                is_code_or_key INTEGER NOT NULL DEFAULT 0,
                is_subscription INTEGER NOT NULL DEFAULT 0,
                is_service INTEGER NOT NULL DEFAULT 0,
                last_checked_at TEXT NOT NULL,
                parse_status TEXT NOT NULL,
                parse_error TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS kwork_categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_name TEXT NOT NULL,
                subcategory_name TEXT,
                url TEXT NOT NULL UNIQUE,
                average_price TEXT,
                min_price TEXT,
                competitors_count INTEGER,
                keywords TEXT NOT NULL DEFAULT '[]',
                last_checked_at TEXT NOT NULL,
                parse_status TEXT NOT NULL,
                parse_error TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS run_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                command TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                processed_count INTEGER NOT NULL DEFAULT 0,
                error TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS parse_errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                url TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS cached_pages (
                url TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                html TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                status_code INTEGER
            )
            """
        )

        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_scraped_items_source ON scraped_items(source)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_analysis_results_item_id ON analysis_results(item_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_opportunities_score ON opportunities(opportunity_score DESC)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_parse_errors_source ON parse_errors(source)"
        )


def _ensure_column(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_definition: str,
) -> None:
    columns = {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name not in columns:
        connection.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
        )


def _ensure_scraped_items_unique_constraint(connection: sqlite3.Connection) -> None:
    """Проверяет, что старая SQLite-база не осталась со старым UNIQUE."""

    unique_indexes = connection.execute("PRAGMA index_list(scraped_items)").fetchall()
    for index in unique_indexes:
        if not index["unique"]:
            continue

        columns = tuple(
            row["name"]
            for row in connection.execute(
                f"PRAGMA index_info({index['name']})"
            ).fetchall()
        )
        if columns == SCRAPED_ITEMS_UNIQUE_COLUMNS:
            return

    raise RuntimeError(
        "Таблица scraped_items создана со старым UNIQUE-ограничением. "
        "SQLite не умеет безопасно менять UNIQUE через ALTER TABLE: "
        "удалите data/processed/analyzer.sqlite и запустите инициализацию заново."
    )


def _ensure_opportunities_unique_index(connection: sqlite3.Connection) -> None:
    duplicates = connection.execute(
        """
        SELECT source_url, COUNT(*) AS count
        FROM opportunities
        GROUP BY source_url
        HAVING COUNT(*) > 1
        LIMIT 1
        """
    ).fetchone()
    if duplicates is not None:
        raise RuntimeError(
            "В таблице opportunities уже есть дубли по source_url. "
            "Для применения уникального индекса удалите дубли или пересоздайте "
            "data/processed/analyzer.sqlite."
        )

    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_opportunities_source_url
        ON opportunities(source_url)
        """
    )


def save_scraped_item(item: ScrapedItem, db_path: Path | None = None) -> int:
    """Сохраняет собранную запись и возвращает ее id."""

    with get_connection(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO scraped_items (
                source, url, title, price, currency, description, category,
                category_id, subcategory, requires_login_password, can_be_done_by_id,
                is_code_or_key, is_subscription, is_service, parse_status,
                parse_error, scraped_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, url) DO UPDATE SET
                title = excluded.title,
                price = excluded.price,
                currency = excluded.currency,
                description = excluded.description,
                category = excluded.category,
                category_id = excluded.category_id,
                subcategory = excluded.subcategory,
                requires_login_password = excluded.requires_login_password,
                can_be_done_by_id = excluded.can_be_done_by_id,
                is_code_or_key = excluded.is_code_or_key,
                is_subscription = excluded.is_subscription,
                is_service = excluded.is_service,
                parse_status = excluded.parse_status,
                parse_error = excluded.parse_error,
                scraped_at = excluded.scraped_at
            """,
            (
                item.source,
                item.url,
                item.title,
                str(item.price) if item.price is not None else None,
                item.currency,
                item.description,
                item.category,
                item.category_id,
                item.subcategory,
                int(item.requires_login_password),
                int(item.can_be_done_by_id),
                int(item.is_code_or_key),
                int(item.is_subscription),
                int(item.is_service),
                item.parse_status,
                item.parse_error,
                item.scraped_at.isoformat(),
            ),
        )
        if cursor.lastrowid:
            return int(cursor.lastrowid)

        row = connection.execute(
            """
            SELECT id FROM scraped_items
            WHERE source = ? AND url = ?
            """,
            (item.source, item.url),
        ).fetchone()
        if row is None:
            raise RuntimeError("Не удалось сохранить собранную запись")
        return int(row["id"])


def save_analysis_result(
    result: AnalysisResult, db_path: Path | None = None
) -> int:
    """Сохраняет старый компактный результат анализа для обратной совместимости."""

    with get_connection(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO analysis_results (
                item_id, risk_level, score, verdict, is_recommended,
                risk_reason, recommendation, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.item_id,
                result.risk_level,
                result.score,
                result.verdict,
                1 if result.is_recommended else 0,
                result.risk_reason,
                result.recommendation,
                result.created_at.isoformat(),
            ),
        )
        return int(cursor.lastrowid)


def save_opportunity(opportunity: Opportunity, db_path: Path | None = None) -> int:
    """Сохраняет полный Opportunity для отчетов и повторного анализа."""

    with get_connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO opportunities (
                funpay_category_id, source_category, source_subcategory, source_url,
                normalized_type, possible_kwork_service_title, possible_kwork_category,
                buy_price, sell_price, estimated_margin_percent, risk_level, risk_reason,
                moderation_risk, dispute_risk, demand_weight, margin_weight, risk_weight,
                opportunity_score, verdict, processing_status, recommendation, forbidden_words, safe_wording,
                buyer_requirements, forbidden_buyer_requests, report_format, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_url) DO UPDATE SET
                funpay_category_id = excluded.funpay_category_id,
                source_category = excluded.source_category,
                source_subcategory = excluded.source_subcategory,
                normalized_type = excluded.normalized_type,
                possible_kwork_service_title = excluded.possible_kwork_service_title,
                possible_kwork_category = excluded.possible_kwork_category,
                buy_price = excluded.buy_price,
                sell_price = excluded.sell_price,
                estimated_margin_percent = excluded.estimated_margin_percent,
                risk_level = excluded.risk_level,
                risk_reason = excluded.risk_reason,
                moderation_risk = excluded.moderation_risk,
                dispute_risk = excluded.dispute_risk,
                demand_weight = excluded.demand_weight,
                margin_weight = excluded.margin_weight,
                risk_weight = excluded.risk_weight,
                opportunity_score = excluded.opportunity_score,
                verdict = excluded.verdict,
                processing_status = COALESCE(opportunities.processing_status, excluded.processing_status),
                recommendation = excluded.recommendation,
                forbidden_words = excluded.forbidden_words,
                safe_wording = excluded.safe_wording,
                buyer_requirements = excluded.buyer_requirements,
                forbidden_buyer_requests = excluded.forbidden_buyer_requests,
                report_format = excluded.report_format,
                created_at = excluded.created_at
            """,
            (
                opportunity.funpay_category_id,
                opportunity.source_category,
                opportunity.source_subcategory,
                opportunity.source_url,
                opportunity.normalized_type,
                opportunity.possible_kwork_service_title,
                opportunity.possible_kwork_category,
                str(opportunity.buy_price) if opportunity.buy_price is not None else None,
                str(opportunity.sell_price) if opportunity.sell_price is not None else None,
                opportunity.estimated_margin_percent,
                opportunity.risk_level,
                opportunity.risk_reason,
                opportunity.moderation_risk,
                opportunity.dispute_risk,
                opportunity.demand_weight,
                opportunity.margin_weight,
                opportunity.risk_weight,
                opportunity.opportunity_score,
                opportunity.verdict,
                opportunity.processing_status,
                opportunity.recommendation,
                json.dumps(opportunity.forbidden_words, ensure_ascii=False),
                opportunity.safe_wording,
                opportunity.buyer_requirements,
                opportunity.forbidden_buyer_requests,
                opportunity.report_format,
                opportunity.created_at.isoformat(),
            ),
        )
        row = connection.execute(
            "SELECT id FROM opportunities WHERE source_url = ?",
            (opportunity.source_url,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"Не удалось сохранить opportunity: {opportunity.source_url}")
        return int(row["id"])


def save_adapted_kwork_card(card: AdaptedKworkCard, db_path: Path | None = None) -> int:
    with get_connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO adapted_kwork_cards (
                opportunity_id, title, kwork_category, image_prompt, description,
                buyer_requirements, base_price, extra_options, faq, risk_warnings,
                source_funpay_url, compliance_status, compliance_markers, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(opportunity_id) DO UPDATE SET
                title = excluded.title,
                kwork_category = excluded.kwork_category,
                image_prompt = excluded.image_prompt,
                description = excluded.description,
                buyer_requirements = excluded.buyer_requirements,
                base_price = excluded.base_price,
                extra_options = excluded.extra_options,
                faq = excluded.faq,
                risk_warnings = excluded.risk_warnings,
                source_funpay_url = excluded.source_funpay_url,
                compliance_status = excluded.compliance_status,
                compliance_markers = excluded.compliance_markers,
                created_at = excluded.created_at
            """,
            (
                card.opportunity_id,
                card.title,
                card.kwork_category,
                card.image_prompt,
                card.description,
                card.buyer_requirements,
                str(card.base_price) if card.base_price is not None else None,
                json.dumps(card.extra_options, ensure_ascii=False),
                json.dumps(card.faq, ensure_ascii=False),
                card.risk_warnings,
                card.source_funpay_url,
                card.compliance_status,
                json.dumps(card.compliance_markers, ensure_ascii=False),
                card.created_at.isoformat(),
            ),
        )
        row = connection.execute(
            "SELECT id FROM adapted_kwork_cards WHERE opportunity_id = ?",
            (card.opportunity_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"Не удалось сохранить адаптированную карточку: {card.opportunity_id}")
        return int(row["id"])


def fetch_adapted_kwork_card(
    opportunity_id: int,
    db_path: Path | None = None,
) -> sqlite3.Row | None:
    with get_connection(db_path) as connection:
        return connection.execute(
            """
            SELECT *
            FROM adapted_kwork_cards
            WHERE opportunity_id = ?
            """,
            (opportunity_id,),
        ).fetchone()


def update_opportunity_status(
    opportunity_id: int,
    processing_status: str,
    db_path: Path | None = None,
) -> None:
    allowed = {"new", "interesting", "in_progress", "rejected", "kwork_created"}
    if processing_status not in allowed:
        raise ValueError(f"Неизвестный статус обработки: {processing_status}")

    with get_connection(db_path) as connection:
        cursor = connection.execute(
            """
            UPDATE opportunities
            SET processing_status = ?
            WHERE id = ?
            """,
            (processing_status, opportunity_id),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"Opportunity не найден: {opportunity_id}")


def save_funpay_category(category: FunPayCategory, db_path: Path | None = None) -> int:
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO funpay_categories (
                category_name, subcategory_name, url, raw_type, normalized_type,
                min_price, median_price, currency, offers_count, delivery_method,
                requires_login_password, can_be_done_by_id, is_code_or_key,
                is_subscription, is_service, last_checked_at, parse_status, parse_error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                category_name = excluded.category_name,
                subcategory_name = excluded.subcategory_name,
                raw_type = excluded.raw_type,
                normalized_type = excluded.normalized_type,
                min_price = excluded.min_price,
                median_price = excluded.median_price,
                currency = excluded.currency,
                offers_count = excluded.offers_count,
                delivery_method = excluded.delivery_method,
                requires_login_password = excluded.requires_login_password,
                can_be_done_by_id = excluded.can_be_done_by_id,
                is_code_or_key = excluded.is_code_or_key,
                is_subscription = excluded.is_subscription,
                is_service = excluded.is_service,
                last_checked_at = excluded.last_checked_at,
                parse_status = excluded.parse_status,
                parse_error = excluded.parse_error
            """,
            (
                category.category_name,
                category.subcategory_name,
                category.url,
                category.raw_type,
                category.normalized_type,
                str(category.min_price) if category.min_price is not None else None,
                str(category.median_price) if category.median_price is not None else None,
                category.currency,
                category.offers_count,
                category.delivery_method,
                int(category.requires_login_password),
                int(category.can_be_done_by_id),
                int(category.is_code_or_key),
                int(category.is_subscription),
                int(category.is_service),
                category.last_checked_at.isoformat(),
                category.parse_status,
                category.parse_error,
            ),
        )
        return _last_id_or_existing(connection, cursor, "funpay_categories", category.url)


def save_kwork_category(category: KworkCategory, db_path: Path | None = None) -> int:
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO kwork_categories (
                category_name, subcategory_name, url, average_price, min_price,
                competitors_count, keywords, last_checked_at, parse_status, parse_error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                category_name = excluded.category_name,
                subcategory_name = excluded.subcategory_name,
                average_price = excluded.average_price,
                min_price = excluded.min_price,
                competitors_count = excluded.competitors_count,
                keywords = excluded.keywords,
                last_checked_at = excluded.last_checked_at,
                parse_status = excluded.parse_status,
                parse_error = excluded.parse_error
            """,
            (
                category.category_name,
                category.subcategory_name,
                category.url,
                str(category.average_price) if category.average_price is not None else None,
                str(category.min_price) if category.min_price is not None else None,
                category.competitors_count,
                json.dumps(category.keywords, ensure_ascii=False),
                category.last_checked_at.isoformat(),
                category.parse_status,
                category.parse_error,
            ),
        )
        return _last_id_or_existing(connection, cursor, "kwork_categories", category.url)


def _last_id_or_existing(
    connection: sqlite3.Connection,
    cursor: sqlite3.Cursor,
    table_name: str,
    url: str,
) -> int:
    if cursor.lastrowid:
        return int(cursor.lastrowid)
    row = connection.execute(
        f"SELECT id FROM {table_name} WHERE url = ?",
        (url,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"Не удалось сохранить запись в {table_name}: {url}")
    return int(row["id"])


def save_parse_error(error: ParseError, db_path: Path | None = None) -> int:
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO parse_errors (source, url, status, error, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                error.source,
                error.url,
                error.status,
                error.error,
                error.created_at.isoformat(),
            ),
        )
        return int(cursor.lastrowid)


def save_run_history(run: RunHistory, db_path: Path | None = None) -> int:
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO run_history (
                command, status, started_at, finished_at, processed_count, error
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run.command,
                run.status,
                run.started_at.isoformat(),
                run.finished_at.isoformat(),
                run.processed_count,
                run.error,
            ),
        )
        return int(cursor.lastrowid)


def cache_page(
    url: str,
    source: str,
    html: str,
    status_code: int | None = None,
    db_path: Path | None = None,
) -> None:
    with get_connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO cached_pages (url, source, html, fetched_at, status_code)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                source = excluded.source,
                html = excluded.html,
                fetched_at = excluded.fetched_at,
                status_code = excluded.status_code
            """,
            (url, source, html, datetime.now().isoformat(), status_code),
        )


def get_cached_page(url: str, db_path: Path | None = None) -> sqlite3.Row | None:
    with get_connection(db_path) as connection:
        return connection.execute(
            "SELECT * FROM cached_pages WHERE url = ?",
            (url,),
        ).fetchone()


def fetch_scraped_items(
    db_path: Path | None = None,
    *,
    source: str | None = None,
    limit: int | None = None,
) -> list[sqlite3.Row]:
    if limit is not None and limit < 0:
        raise ValueError("Лимит записей не может быть отрицательным")

    query = """
        SELECT *
        FROM scraped_items
    """
    params: list[object] = []
    if source is not None:
        query += " WHERE source = ?"
        params.append(source)
    query += " ORDER BY scraped_at DESC, id DESC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    with get_connection(db_path) as connection:
        return list(connection.execute(query, tuple(params)).fetchall())


def clean_funpay_table_by_current_filters(
    db_path: Path | None = None,
    progress_callback: ProgressCallback | None = None,
) -> CleanReport:
    with get_connection(db_path) as connection:
        rows = connection.execute(
            "SELECT * FROM scraped_items WHERE source = 'funpay'"
        ).fetchall()
        total_rows = max(1, len(rows))
        urls_to_delete: list[str] = []
        ids_to_delete: list[int] = []
        reason_counts: dict[str, int] = {}
        for index, row in enumerate(rows, start=1):
            item = _scraped_item_from_row(row)
            item_filter_url = _funpay_filter_url(row, item.url)
            reasons = classify_game_item_reasons(item)
            if not reasons and item_filter_url != item.url:
                reasons = classify_game_category_reasons(
                    name=item.category,
                    text=" ".join(filter(None, (item.title, item.description, item.category, item.subcategory))),
                    url=item_filter_url,
                )
            if reasons:
                ids_to_delete.append(int(row["id"]))
                urls_to_delete.append(str(row["url"]))
                _add_reasons(reason_counts, reasons)
            _report_clean_progress(
                progress_callback,
                index,
                total_rows,
                f"Проверяю лоты FunPay {index}/{total_rows}. К удалению: {len(ids_to_delete)}.",
            )

        category_rows = connection.execute("SELECT * FROM funpay_categories").fetchall()
        category_urls_to_delete: list[str] = []
        for row in category_rows:
            reasons = classify_game_category_reasons(
                name=row["category_name"],
                text=" ".join(
                    filter(
                        None,
                        (
                            row["category_name"],
                            row["subcategory_name"],
                            row["raw_type"],
                            row["normalized_type"],
                            row["delivery_method"],
                        ),
                    )
                ),
                url=row["url"],
            )
            if reasons:
                category_urls_to_delete.append(str(row["url"]))
                _add_reasons(reason_counts, reasons)

        _report_clean_progress(
            progress_callback,
            total_rows,
            total_rows,
            "Удаляю строки FunPay, которые не прошли актуальные фильтры...",
        )
        _delete_by_ids(connection, "scraped_items", ids_to_delete)
        _delete_by_urls(connection, "funpay_categories", category_urls_to_delete)
        _delete_by_source_urls(connection, "opportunities", urls_to_delete)
        return {"deleted": len(ids_to_delete), "reasons": reason_counts}


def clean_kwork_table_by_current_filters(
    db_path: Path | None = None,
    progress_callback: ProgressCallback | None = None,
) -> CleanReport:
    with get_connection(db_path) as connection:
        rows = connection.execute(
            "SELECT * FROM scraped_items WHERE source = 'kwork'"
        ).fetchall()
        total_rows = max(1, len(rows))
        ids_to_delete: list[int] = []
        for index, row in enumerate(rows, start=1):
            if not _is_valid_saved_kwork_row(row):
                ids_to_delete.append(int(row["id"]))
            _report_clean_progress(
                progress_callback,
                index,
                total_rows,
                f"Проверяю услуги Kwork {index}/{total_rows}. К удалению: {len(ids_to_delete)}.",
            )
        _delete_by_ids(connection, "scraped_items", ids_to_delete)
        reasons = {"некорректная запись Kwork": len(ids_to_delete)} if ids_to_delete else {}
        return {"deleted": len(ids_to_delete), "reasons": reasons}


def clean_opportunities_table_by_current_filters(
    db_path: Path | None = None,
    progress_callback: ProgressCallback | None = None,
) -> CleanReport:
    with get_connection(db_path) as connection:
        funpay_urls = {
            str(row["url"])
            for row in connection.execute(
                "SELECT url FROM scraped_items WHERE source = 'funpay'"
            ).fetchall()
        }
        rows = connection.execute("SELECT * FROM opportunities").fetchall()
        total_rows = max(1, len(rows))
        ids_to_delete: list[int] = []
        reason_counts: dict[str, int] = {}
        for index, row in enumerate(rows, start=1):
            reasons = _opportunity_filter_reasons(row, funpay_urls)
            if reasons:
                ids_to_delete.append(int(row["id"]))
                _add_reasons(reason_counts, reasons)
            _report_clean_progress(
                progress_callback,
                index,
                total_rows,
                f"Проверяю выводы {index}/{total_rows}. К удалению: {len(ids_to_delete)}.",
            )
        _delete_by_ids(connection, "opportunities", ids_to_delete)
        return {"deleted": len(ids_to_delete), "reasons": reason_counts}


def clear_funpay_work_table(db_path: Path | None = None) -> dict[str, int]:
    with get_connection(db_path) as connection:
        funpay_items = connection.execute(
            "DELETE FROM scraped_items WHERE source = 'funpay'"
        ).rowcount
        funpay_categories = connection.execute("DELETE FROM funpay_categories").rowcount
        opportunities = connection.execute("DELETE FROM opportunities").rowcount
        analysis_results = connection.execute("DELETE FROM analysis_results").rowcount
        adapted_cards = connection.execute("DELETE FROM adapted_kwork_cards").rowcount
    return {
        "FunPay лоты": int(funpay_items),
        "FunPay категории": int(funpay_categories),
        "Связанные выводы": int(opportunities),
        "Связанные карточки": int(adapted_cards),
        "Результаты анализа": int(analysis_results),
    }


def clear_kwork_work_table(db_path: Path | None = None) -> dict[str, int]:
    with get_connection(db_path) as connection:
        kwork_items = connection.execute(
            "DELETE FROM scraped_items WHERE source = 'kwork'"
        ).rowcount
        kwork_categories = connection.execute("DELETE FROM kwork_categories").rowcount
    return {
        "Kwork услуги": int(kwork_items),
        "Kwork категории": int(kwork_categories),
    }


def clear_opportunities_work_table(db_path: Path | None = None) -> dict[str, int]:
    with get_connection(db_path) as connection:
        adapted_cards = connection.execute("DELETE FROM adapted_kwork_cards").rowcount
        opportunities = connection.execute("DELETE FROM opportunities").rowcount
        analysis_results = connection.execute("DELETE FROM analysis_results").rowcount
    return {
        "Выводы сравнения": int(opportunities),
        "Адаптированные карточки": int(adapted_cards),
        "Результаты анализа": int(analysis_results),
    }


def _add_reasons(reason_counts: dict[str, int], reasons: list[str]) -> None:
    for reason in reasons:
        reason_counts[reason] = reason_counts.get(reason, 0) + 1


def _funpay_filter_url(row: sqlite3.Row, fallback_url: str) -> str:
    keys = row.keys()
    category_id = str(row["category_id"] or "").strip() if "category_id" in keys else ""
    if not category_id:
        category_id = _extract_legacy_funpay_category_id(row)
    if not category_id:
        return fallback_url
    return f"https://funpay.com/lots/{category_id}/"


def _extract_legacy_funpay_category_id(row: sqlite3.Row) -> str:
    values = []
    for column in ("category", "subcategory"):
        try:
            values.append(str(row[column] or ""))
        except (IndexError, KeyError):
            continue
    for value in values:
        normalized = value.strip()
        if normalized.isdigit():
            return normalized
        match = re.fullmatch(r"FunPay\s+(\d+)", normalized, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def _report_clean_progress(
    progress_callback: ProgressCallback | None,
    current: int,
    total: int,
    message: str,
) -> None:
    if progress_callback is not None and (current == total or current % 250 == 0 or current == 1):
        progress_callback(current, total, message)


def _scraped_item_from_row(row: sqlite3.Row) -> ScrapedItem:
    return ScrapedItem(
        id=row["id"],
        source=row["source"],
        url=row["url"],
        title=row["title"],
        price=row["price"],
        currency=row["currency"],
        description=row["description"],
        category=row["category"],
        category_id=row["category_id"] if "category_id" in row.keys() else None,
        subcategory=row["subcategory"],
        requires_login_password=bool(row["requires_login_password"]),
        can_be_done_by_id=bool(row["can_be_done_by_id"]),
        is_code_or_key=bool(row["is_code_or_key"]),
        is_subscription=bool(row["is_subscription"]),
        is_service=bool(row["is_service"]),
        parse_status=row["parse_status"],
        parse_error=row["parse_error"],
        scraped_at=datetime.fromisoformat(row["scraped_at"]),
    )


def _is_valid_saved_kwork_row(row: sqlite3.Row) -> bool:
    return bool(
        row["source"] == "kwork"
        and row["parse_status"] == "success"
        and row["title"]
        and str(row["url"]).startswith(("http://", "https://"))
    )


def _opportunity_fails_current_filters(
    row: sqlite3.Row,
    valid_funpay_urls: set[str],
) -> bool:
    return bool(_opportunity_filter_reasons(row, valid_funpay_urls))


def _opportunity_filter_reasons(
    row: sqlite3.Row,
    valid_funpay_urls: set[str],
) -> list[str]:
    source_url = str(row["source_url"])
    if source_url not in valid_funpay_urls:
        return ["исходный FunPay-лот удален фильтрами"]
    return classify_game_category_reasons(
        name=row["source_category"],
        text=" ".join(
            filter(
                None,
                (
                    row["source_category"],
                    row["source_subcategory"],
                    row["possible_kwork_service_title"],
                    row["risk_reason"],
                    row["safe_wording"],
                ),
            )
        ),
        url=source_url,
    )


def _delete_by_ids(
    connection: sqlite3.Connection,
    table_name: str,
    ids: list[int],
) -> None:
    for chunk in _chunks(ids, SQLITE_DELETE_BATCH_SIZE):
        placeholders = ",".join("?" for _ in chunk)
        connection.execute(
            f"DELETE FROM {table_name} WHERE id IN ({placeholders})",
            chunk,
        )


def _delete_by_urls(
    connection: sqlite3.Connection,
    table_name: str,
    urls: list[str],
) -> None:
    for chunk in _chunks(urls, SQLITE_DELETE_BATCH_SIZE):
        placeholders = ",".join("?" for _ in chunk)
        connection.execute(
            f"DELETE FROM {table_name} WHERE url IN ({placeholders})",
            chunk,
        )


def _delete_by_source_urls(
    connection: sqlite3.Connection,
    table_name: str,
    urls: list[str],
) -> None:
    for chunk in _chunks(urls, SQLITE_DELETE_BATCH_SIZE):
        placeholders = ",".join("?" for _ in chunk)
        connection.execute(
            f"DELETE FROM {table_name} WHERE source_url IN ({placeholders})",
            chunk,
        )


def _chunks(values: list[T], size: int) -> Iterator[list[T]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def fetch_analysis_results(db_path: Path | None = None) -> list[sqlite3.Row]:
    with get_connection(db_path) as connection:
        return list(
            connection.execute(
                """
                SELECT *
                FROM analysis_results
                ORDER BY created_at DESC, id DESC
                """
            ).fetchall()
        )


def fetch_opportunities(db_path: Path | None = None) -> list[sqlite3.Row]:
    with get_connection(db_path) as connection:
        return list(
            connection.execute(
                """
                SELECT *
                FROM opportunities
                ORDER BY opportunity_score DESC, created_at DESC, id DESC
                """
            ).fetchall()
        )


def fetch_kwork_categories(db_path: Path | None = None) -> list[sqlite3.Row]:
    with get_connection(db_path) as connection:
        return list(
            connection.execute(
                """
                SELECT *
                FROM kwork_categories
                ORDER BY competitors_count DESC, last_checked_at DESC
                """
            ).fetchall()
        )


def fetch_run_history(db_path: Path | None = None) -> list[sqlite3.Row]:
    with get_connection(db_path) as connection:
        return list(
            connection.execute(
                """
                SELECT *
                FROM run_history
                ORDER BY started_at DESC, id DESC
                """
            ).fetchall()
        )


def fetch_parse_errors(db_path: Path | None = None) -> list[sqlite3.Row]:
    with get_connection(db_path) as connection:
        return list(
            connection.execute(
                """
                SELECT *
                FROM parse_errors
                ORDER BY created_at DESC, id DESC
                """
            ).fetchall()
        )
