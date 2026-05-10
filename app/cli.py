"""Консольная точка входа для запуска MVP-пайплайна анализа."""

from __future__ import annotations

import argparse
from typing import TypedDict

from app.analyzers.kwork_mapper import map_to_kwork
from app.analyzers.opportunity_scorer import score_opportunity
from app.analyzers.risk_classifier import analyze_risk
from app.collectors.funpay import parse_category
from app.collectors.http_client import AntiBanError, HttpClientError
from app.db import init_db, save_analysis_result, save_scraped_item
from app.models import AnalysisResult, Opportunity, ScrapedItem
from app.reports.exporter import export_to_csv


class PipelineSummary(TypedDict):
    scraped: int
    saved: int
    analyzed: int
    skipped: int


def run_pipeline(category_url: str) -> PipelineSummary:
    """Запускает полный цикл сбора, анализа и сохранения для одной категории FunPay."""

    init_db()
    try:
        scraped_items = parse_category(category_url)
    except (AntiBanError, HttpClientError) as error:
        scraped_items = [_failed_scraped_item(category_url, str(error))]

    summary: PipelineSummary = {
        "scraped": len(scraped_items),
        "saved": 0,
        "analyzed": 0,
        "skipped": 0,
    }

    for item in scraped_items:
        item_id = save_scraped_item(item)
        summary["saved"] += 1

        if item.parse_status != "success":
            summary["skipped"] += 1
            continue

        item.id = item_id
        risk_level, risk_reason = analyze_risk(item)
        mapping_data = map_to_kwork(item, risk_level)
        opportunity = score_opportunity(
            item=item,
            risk_level=risk_level,
            risk_reason=risk_reason,
            mapping_data=mapping_data,
        )
        save_analysis_result(_analysis_result_from_opportunity(item_id, opportunity))
        summary["analyzed"] += 1

    return summary


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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Запускает сбор лотов FunPay, анализ рисков и сохранение результата в SQLite."
    )
    parser.add_argument(
        "--url",
        help="URL категории FunPay для анализа.",
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help="Сгенерировать CSV отчет после анализа.",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if not args.url and not args.export:
        parser.error(
            "Необходимо указать --url для парсинга и/или --export для выгрузки отчета."
        )

    if args.url:
        summary = run_pipeline(args.url)
        print(
            "Готово: "
            f"получено {summary['scraped']}, "
            f"сохранено {summary['saved']}, "
            f"проанализировано {summary['analyzed']}, "
            f"пропущено {summary['skipped']}."
        )

    if args.export:
        if not args.url:
            print("Генерация отчета по накопленной базе...")
        report_path = export_to_csv()
        print(f"Отчет сохранен: {report_path}")


if __name__ == "__main__":
    main()
