"""Минимальный SQLite-слой для MVP."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import config
from app.models import AnalysisResult, ScrapedItem

SCRAPED_ITEMS_UNIQUE_COLUMNS = ("source", "url")


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
    """Создает таблицы scraped_items и analysis_results."""

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
                subcategory TEXT,
                parse_status TEXT NOT NULL,
                parse_error TEXT,
                scraped_at TEXT NOT NULL,
                UNIQUE(source, url)
            )
            """
        )
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
            "CREATE INDEX IF NOT EXISTS idx_scraped_items_source ON scraped_items(source)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_analysis_results_item_id ON analysis_results(item_id)"
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


def save_scraped_item(item: ScrapedItem, db_path: Path | None = None) -> int:
    """Сохраняет собранную запись и возвращает ее id."""

    with get_connection(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO scraped_items (
                source, url, title, price, currency, description, category,
                subcategory, parse_status, parse_error, scraped_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, url) DO UPDATE SET
                title = excluded.title,
                price = excluded.price,
                currency = excluded.currency,
                description = excluded.description,
                category = excluded.category,
                subcategory = excluded.subcategory,
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
                item.subcategory,
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
    """Сохраняет результат анализа и возвращает его id."""

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


def fetch_scraped_items(db_path: Path | None = None) -> list[sqlite3.Row]:
    with get_connection(db_path) as connection:
        return list(
            connection.execute(
                """
                SELECT *
                FROM scraped_items
                ORDER BY scraped_at DESC, id DESC
                """
            ).fetchall()
        )


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
