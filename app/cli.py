"""Консольный интерфейс FunOrk."""

from __future__ import annotations

import argparse
import logging
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import TypedDict

from app.analyzers.kwork_mapper import map_to_kwork
from app.analyzers.kwork_matcher import (
    build_kwork_index,
    calculate_demand_from_matches,
    load_kwork_services,
    match_funpay_to_kwork,
)
from app.analyzers.opportunity_scorer import score_opportunity
from app.analyzers.risk_classifier import analyze_risk
from app.collectors.funpay import collect_catalog as collect_funpay_catalog
from app.collectors.funpay import parse_category
from app.collectors.http_client import AntiBanError, HttpClientError
from app.collectors.kwork import collect_catalog as collect_kwork_catalog
from app.config import config
from app.db import (
    fetch_scraped_items,
    init_db,
    save_analysis_result,
    save_funpay_category,
    save_kwork_category,
    save_opportunity,
    save_parse_error,
    save_run_history,
    save_scraped_item,
)
from app.importers import import_funpay_file, import_kwork_file
from app.models import (
    AnalysisResult,
    FunPayCategory,
    Opportunity,
    ParseError,
    RunHistory,
    ScrapedItem,
)
from app.reports.export_csv import export_to_csv
from app.reports.export_html import export_to_html
from app.reports.export_xlsx import export_to_xlsx


logger = logging.getLogger(__name__)


class PipelineSummary(TypedDict):
    scraped: int
    saved: int
    analyzed: int
    skipped: int


def run_pipeline(category_url: str) -> PipelineSummary:
    """Совместимый полный цикл для одной категории FunPay."""

    init_db()
    try:
        scraped_items = parse_category(category_url)
    except (AntiBanError, HttpClientError) as error:
        scraped_items = [_failed_scraped_item(category_url, str(error))]

    return _save_and_analyze_items(scraped_items)


def collect_funpay(force: bool = False) -> int:
    init_db()
    urls = collect_funpay_catalog(force=force, limit=config.MAX_PAGES_PER_RUN)
    saved = 0
    for url in urls:
        category = FunPayCategory(
            category_name=url.rstrip("/").split("/")[-1] or "FunPay",
            url=url,
            parse_status="pending",
        )
        save_funpay_category(category)
        try:
            items = parse_category(url, force=force)
        except (AntiBanError, HttpClientError) as error:
            items = [_failed_scraped_item(url, str(error))]
        for item in items:
            save_scraped_item(item)
            if item.parse_status != "success" and item.parse_error:
                save_parse_error(
                    ParseError(
                        source=item.source,
                        url=item.url,
                        status=item.parse_status,
                        error=item.parse_error,
                    )
                )
            saved += 1
    return saved


def collect_kwork(force: bool = False) -> int:
    init_db()
    categories, items = collect_kwork_catalog(
        force=force,
        limit=config.KWORK_MAX_CATEGORIES,
    )
    for category in categories:
        save_kwork_category(category)
        if category.parse_status != "success" and category.parse_error:
            save_parse_error(
                ParseError(
                    source="kwork",
                    url=category.url,
                    status=category.parse_status,
                    error=category.parse_error,
                )
            )
    for item in items:
        if item.source == "kwork" and item.parse_status == "success" and item.title:
            save_scraped_item(item)
        elif item.parse_status != "success" and item.parse_error:
            save_parse_error(
                ParseError(
                    source="kwork",
                    url=item.url,
                    status=item.parse_status,
                    error=item.parse_error,
                )
            )
    return len(categories)


def analyze_saved_items() -> int:
    init_db()
    analyzed = 0
    kwork_index = build_kwork_index(load_kwork_services())
    for row in fetch_scraped_items():
        item = _scraped_item_from_row(row)
        if item.source != "funpay":
            continue
        if item.parse_status != "success":
            continue
        opportunity = _analyze_item(item, kwork_index)
        _save_opportunity_compat(opportunity)
        save_analysis_result(_analysis_result_from_opportunity(item.id or 0, opportunity))
        analyzed += 1
    return analyzed


def export_report(format_name: str) -> Path:
    normalized = format_name.lower()
    if normalized == "csv":
        return export_to_csv()
    if normalized == "xlsx":
        return export_to_xlsx()
    if normalized == "html":
        return export_to_html()
    raise ValueError("Формат отчета должен быть csv, xlsx или html")


def dry_run() -> str:
    init_db()
    return (
        "Проверка FunOrk выполнена: конфигурация загружена, директории и SQLite-схема готовы."
    )


def _save_and_analyze_items(scraped_items: list[ScrapedItem]) -> PipelineSummary:
    summary: PipelineSummary = {
        "scraped": len(scraped_items),
        "saved": 0,
        "analyzed": 0,
        "skipped": 0,
    }
    kwork_index = build_kwork_index(load_kwork_services())

    for item in scraped_items:
        item_id = save_scraped_item(item)
        summary["saved"] += 1

        if item.parse_status != "success":
            summary["skipped"] += 1
            if item.parse_error:
                save_parse_error(
                    ParseError(
                        source=item.source,
                        url=item.url,
                        status=item.parse_status,
                        error=item.parse_error,
                    )
                )
            continue

        item.id = item_id
        opportunity = _analyze_item(item, kwork_index)
        _save_opportunity_compat(opportunity)
        save_analysis_result(_analysis_result_from_opportunity(item_id, opportunity))
        summary["analyzed"] += 1

    return summary


def _analyze_item(
    item: ScrapedItem,
    kwork_index: list[dict[str, object]] | None = None,
) -> Opportunity:
    risk_level, risk_reason = analyze_risk(item)
    mapping_data = map_to_kwork(item, risk_level)
    demand_score = None
    if kwork_index and risk_level != "RED":
        matches = match_funpay_to_kwork(item, kwork_index)
        demand = calculate_demand_from_matches(matches)
        demand_score = float(demand["demand_weight"])
        if mapping_data is None:
            mapping_data = {}
        if matches:
            first_service = matches[0]["service"]
            mapping_data["service_title"] = _adapt_funpay_title(item, first_service)
            mapping_data["kwork_category"] = first_service.category
            mapping_data["average_price"] = demand["avg_kwork_price"]
            mapping_data["min_price"] = demand["min_kwork_price"]
            mapping_data["safe_wording"] = (
                f"На Kwork найдено похожих услуг: {len(matches)}. "
                f"Ближайший ориентир: «{first_service.title}». "
                "Формулируйте предложение как помощь, настройку, продвижение или консультацию без запроса доступов."
            )
            mapping_data["recommendation"] = (
                "Проверить ближайшие услуги Kwork, взять цену и формулировку как ориентир, "
                "но не обещать результат, требующий доступа к аккаунту покупателя."
            )
    return score_opportunity(
        item=item,
        risk_level=risk_level,
        risk_reason=risk_reason,
        mapping_data=mapping_data,
        demand_score=demand_score,
    )


def _adapt_funpay_title(item: ScrapedItem, kwork_service: ScrapedItem) -> str:
    base = item.title.strip(" .")
    if item.subcategory:
        base = f"{base} ({item.subcategory})"
    if len(base) > 90:
        base = base[:87].rstrip() + "..."
    return f"Адаптировать FunPay-лот: {base}. Ориентир Kwork: {kwork_service.title}"


def _save_opportunity_compat(opportunity: Opportunity) -> None:
    try:
        save_opportunity(opportunity)
    except sqlite3.OperationalError as error:
        if "no such table: opportunities" not in str(error):
            raise


def _failed_scraped_item(category_url: str, error: str) -> ScrapedItem:
    return ScrapedItem(
        source="funpay",
        url=category_url,
        title="FunPay: ошибка сбора лотов",
        parse_status="request_failed",
        parse_error=error,
    )


def _analysis_result_from_opportunity(
    item_id: int,
    opportunity: Opportunity,
) -> AnalysisResult:
    return AnalysisResult(
        item_id=item_id,
        risk_level=opportunity.risk_level,
        score=opportunity.opportunity_score,
        verdict=opportunity.verdict,
        is_recommended=(
            opportunity.risk_level != "RED" and opportunity.opportunity_score >= 75
        ),
        risk_reason=opportunity.risk_reason,
        recommendation=opportunity.recommendation,
    )


def _scraped_item_from_row(row: object) -> ScrapedItem:
    return ScrapedItem(
        id=row["id"],
        source=row["source"],
        url=row["url"],
        title=row["title"],
        price=Decimal(row["price"]) if row["price"] else None,
        currency=row["currency"],
        description=row["description"],
        category=row["category"],
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FunOrk: поиск и анализ направлений FunPay -> Kwork.")
    parser.add_argument("--url", help="Совместимый режим: URL категории FunPay.")
    parser.add_argument("--export", action="store_true", help="Совместимый режим: выгрузить CSV.")

    subparsers = parser.add_subparsers(dest="command")

    funpay = subparsers.add_parser("collect-funpay", help="Собрать каталог FunPay.")
    funpay.add_argument("--force", action="store_true", help="Обновить страницы без кеша.")

    kwork = subparsers.add_parser("collect-kwork", help="Собрать каталог Kwork.")
    kwork.add_argument("--force", action="store_true", help="Обновить страницы без кеша.")

    subparsers.add_parser("analyze", help="Проанализировать сохраненные лоты.")

    export = subparsers.add_parser("export", help="Сформировать отчет.")
    export.add_argument("--format", choices=("csv", "xlsx", "html"), default=config.REPORT_SETTINGS.default_format)

    run_all = subparsers.add_parser("run-all", help="Запустить сбор, анализ и отчет.")
    run_all.add_argument("--force", action="store_true", help="Обновить страницы без кеша.")
    run_all.add_argument("--format", choices=("csv", "xlsx", "html"), default=config.REPORT_SETTINGS.default_format)

    subparsers.add_parser("dry-run", help="Проверить конфигурацию и схему БД.")

    import_funpay = subparsers.add_parser("import-funpay", help="Импортировать CSV/JSON FunPay.")
    import_funpay.add_argument("--file", required=True, help="Путь к CSV или JSON.")

    import_kwork = subparsers.add_parser("import-kwork", help="Импортировать CSV/JSON Kwork.")
    import_kwork.add_argument("--file", required=True, help="Путь к CSV или JSON.")

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command is None:
        _run_legacy_mode(parser, args)
        return

    init_db()
    started_at = datetime.now(timezone.utc)
    processed = 0
    try:
        if args.command == "collect-funpay":
            processed = collect_funpay(force=args.force)
            logger.info("Каталог FunPay сохранен: %s категорий.", processed)
        elif args.command == "collect-kwork":
            processed = collect_kwork(force=args.force)
            logger.info("Каталог Kwork сохранен: %s категорий.", processed)
        elif args.command == "analyze":
            processed = analyze_saved_items()
            logger.info("Анализ завершен: %s направлений.", processed)
        elif args.command == "export":
            path = export_report(args.format)
            processed = 1
            logger.info("Отчет сохранен: %s", path)
        elif args.command == "run-all":
            processed += collect_funpay(force=args.force)
            processed += collect_kwork(force=args.force)
            processed += analyze_saved_items()
            path = export_report(args.format)
            logger.info("Полный цикл завершен. Отчет сохранен: %s", path)
        elif args.command == "dry-run":
            processed = 1
            logger.info(dry_run())
        elif args.command == "import-funpay":
            processed = import_funpay_file(Path(args.file))
            logger.info("Импорт FunPay завершен: %s записей.", processed)
        elif args.command == "import-kwork":
            processed = import_kwork_file(Path(args.file))
            logger.info("Импорт Kwork завершен: %s записей.", processed)
        else:
            parser.error("Неизвестная команда")

        save_run_history(
            RunHistory(
                command=args.command,
                status="success",
                started_at=started_at,
                processed_count=processed,
            )
        )
    except Exception as error:
        save_run_history(
            RunHistory(
                command=args.command or "unknown",
                status="failed",
                started_at=started_at,
                processed_count=processed,
                error=str(error),
            )
        )
        logger.exception("Команда %s завершилась с ошибкой", args.command or "unknown")
        raise


def _run_legacy_mode(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if not args.url and not args.export:
        parser.error(
            "Необходимо указать команду или использовать совместимый режим --url/--export."
        )

    if args.url:
        summary = run_pipeline(args.url)
        logger.info(
            "Готово: "
            f"получено {summary['scraped']}, "
            f"сохранено {summary['saved']}, "
            f"проанализировано {summary['analyzed']}, "
            f"пропущено {summary['skipped']}."
        )

    if args.export:
        if not args.url:
            logger.info("Генерация отчета по накопленной базе...")
        report_path = export_to_csv()
        logger.info("Отчет сохранен: %s", report_path)


if __name__ == "__main__":
    main()
