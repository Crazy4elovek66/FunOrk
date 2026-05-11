"""Streamlit-интерфейс FunOrk."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, TypeVar

import pandas as pd
import streamlit as st

from app.cli import analyze_saved_items, collect_funpay
from app.collectors.http_client import AntiBanError, HttpClientError
from app.collectors.kwork import SessionValidationError
from app.config import config
from app.db import fetch_opportunities, fetch_parse_errors, fetch_scraped_items, init_db, save_run_history
from app.models import RunHistory
from app.reports.export_html import export_to_html
from app.services.kwork_collection_service import collect_kwork_with_summary


st.set_page_config(page_title="FunOrk", layout="wide")
T = TypeVar("T")


def main() -> None:
    init_db()

    st.title("FunOrk - анализ FunPay и Kwork")
    _render_controls()
    st.divider()
    _render_data_tabs()


def _render_data_tabs() -> None:
    funpay_tab, kwork_tab, comparison_tab = st.tabs(
        [
            "FunPay",
            "Kwork",
            "Сравнение и выводы",
        ]
    )

    with funpay_tab:
        _render_funpay_table()
    with kwork_tab:
        _render_kwork_services_table()
    with comparison_tab:
        _render_opportunities_table()


def _render_controls() -> None:
    st.subheader("Управление сбором и отчетом")

    force = st.checkbox("Обновить без кеша (--force)", value=False)
    if config.KWORK_COOKIE:
        st.success("KWORK_COOKIE найден")
    else:
        st.warning(
            "KWORK_COOKIE не задан. Kwork может вернуть пустую страницу или SmartCaptcha. "
            "В таком случае используйте Cookie или ручной импорт."
        )

    col_funpay, col_kwork, col_analyze, col_refresh, col_export = st.columns([1, 1, 1, 1, 1])
    with col_funpay:
        funpay_clicked = st.button("Парсить FunPay")
    with col_kwork:
        kwork_clicked = st.button("Парсить Kwork")
    with col_analyze:
        analyze_clicked = st.button("Произвести анализ", type="primary")
    with col_refresh:
        if st.button("Обновить таблицы"):
            st.cache_data.clear()
            st.rerun()
    with col_export:
        export_clicked = st.button("Сформировать HTML-отчет")

    if export_clicked:
        try:
            report_path = export_to_html()
        except Exception as error:
            st.error(f"Не удалось сформировать HTML-отчет: {error}")
        else:
            st.success(f"HTML-отчет сформирован: {report_path}")

    if funpay_clicked:
        with st.spinner("Собираю категории и лоты FunPay..."):
            try:
                processed = _run_with_history(
                    "ui:collect-funpay",
                    lambda: collect_funpay(force=force),
                )
            except Exception as error:
                st.error(f"Ошибка при сборе FunPay: {error}")
            else:
                st.cache_data.clear()
                st.success(f"Сбор FunPay завершен: сохранено {processed} лотов.")

    if kwork_clicked:
        with st.spinner("Собираю каталог и услуги Kwork..."):
            try:
                summary = collect_kwork_with_summary(force=force)
            except SessionValidationError as error:
                st.error(f"⚠️ {error}")
                return
            except AntiBanError as error:
                st.error(f"Kwork ограничил доступ или вернул SmartCaptcha: {error}")
                return
            except HttpClientError as error:
                st.error(f"Не удалось загрузить страницы Kwork: {error}")
                return
            except Exception as error:
                st.error(f"Ошибка при сборе Kwork: {error}")
                return
        st.cache_data.clear()
        st.success("Сбор Kwork завершен")
        _render_summary(summary)

    if analyze_clicked:
        with st.spinner("Сравниваю FunPay с Kwork и обновляю выводы..."):
            try:
                processed = _run_with_history("ui:analyze", analyze_saved_items)
            except Exception as error:
                st.error(f"Не удалось выполнить анализ: {error}")
            else:
                st.cache_data.clear()
                st.success(f"Анализ завершен: обработано {processed} лотов FunPay.")


def _run_with_history(command: str, action: Callable[[], int]) -> int:
    started_at = datetime.now(timezone.utc)
    processed = 0
    try:
        processed = action()
        save_run_history(
            RunHistory(
                command=command,
                status="success",
                started_at=started_at,
                processed_count=processed,
            )
        )
        return processed
    except Exception as error:
        save_run_history(
            RunHistory(
                command=command,
                status="failed",
                started_at=started_at,
                processed_count=processed,
                error=str(error),
            )
        )
        raise


def _render_summary(summary: dict[str, object]) -> None:
    columns = st.columns(3)
    columns[0].metric("Категорий сохранено", int(summary.get("categories_saved") or 0))
    columns[1].metric("Услуг сохранено", int(summary.get("services_saved") or 0))
    columns[2].metric("Ошибок парсинга", int(summary.get("parse_errors") or 0))

    if int(summary.get("services_saved") or 0) == 0:
        st.warning("Услуги Kwork не найдены. Проверьте KWORK_COOKIE, debug HTML и селекторы.")

    debug_files = list(summary.get("debug_files") or [])
    if debug_files:
        st.info("Найдены debug-файлы Kwork:")
        for file_path in debug_files[-10:]:
            st.code(str(file_path))


def _render_funpay_table() -> None:
    st.subheader("FunPay: собранные лоты")
    rows = _load_funpay_items()
    if rows.empty:
        st.info("Лоты FunPay пока не сохранены. Запустите сбор FunPay или импортируйте данные.")
        return

    columns = [
        "title",
        "description",
        "price",
        "currency",
        "category",
        "subcategory",
        "url",
        "scraped_at",
        "parse_status",
    ]
    view = _select_columns(rows, columns).rename(
        columns={
            "title": "Название",
            "description": "Описание",
            "price": "Цена",
            "currency": "Валюта",
            "category": "Категория",
            "subcategory": "Подкатегория",
            "url": "Ссылка",
            "scraped_at": "Собрано",
            "parse_status": "Статус парсинга",
        }
    )
    _render_dataframe(view)


def _render_kwork_services_table() -> None:
    st.subheader("Kwork: собранные услуги")
    rows = _load_kwork_services()
    if rows.empty:
        st.info("Услуги Kwork пока не сохранены. Запустите сбор каталога Kwork.")
        return

    columns = ["title", "description", "price", "currency", "category", "subcategory", "url", "scraped_at", "parse_status"]
    view = _select_columns(rows, columns).rename(
        columns={
            "title": "Название",
            "description": "Описание",
            "price": "Цена",
            "currency": "Валюта",
            "category": "Категория",
            "subcategory": "Подкатегория",
            "url": "Ссылка",
            "scraped_at": "Собрано",
            "parse_status": "Статус парсинга",
        }
    )
    _render_dataframe(view)


def _render_opportunities_table() -> None:
    st.subheader("Сравнение: что можно адаптировать с FunPay на Kwork")
    rows = _load_opportunities()
    if rows.empty:
        st.info("Готовых выводов пока нет. Запустите анализ после сбора FunPay и Kwork.")
        return

    columns = [
        "source_category",
        "source_subcategory",
        "possible_kwork_service_title",
        "possible_kwork_category",
        "buy_price",
        "sell_price",
        "estimated_margin_percent",
        "risk_level",
        "opportunity_score",
        "verdict",
        "source_url",
        "created_at",
        "recommendation",
        "safe_wording",
    ]
    view = _select_columns(rows, columns).rename(
        columns={
            "source_category": "Категория FunPay",
            "source_subcategory": "Подкатегория FunPay",
            "possible_kwork_service_title": "Что адаптировать под Kwork",
            "possible_kwork_category": "Категория Kwork",
            "buy_price": "Цена закупки",
            "sell_price": "Цена продажи",
            "estimated_margin_percent": "Маржа, %",
            "risk_level": "Риск",
            "opportunity_score": "Балл",
            "verdict": "Вывод",
            "recommendation": "Рекомендация",
            "safe_wording": "Безопасная формулировка",
            "source_url": "Источник FunPay",
            "created_at": "Дата анализа",
        }
    )
    _render_dataframe(view)


def _render_kwork_errors() -> None:
    st.subheader("Ошибки парсинга Kwork")
    rows = _load_kwork_parse_errors()
    if rows.empty:
        st.success("Ошибок парсинга Kwork пока нет.")
        return

    columns = ["url", "status", "error", "created_at"]
    view = _select_columns(rows, columns).head(20).rename(
        columns={
            "url": "Ссылка",
            "status": "Статус",
            "error": "Ошибка",
            "created_at": "Добавлено",
        }
    )
    _render_dataframe(view)


def _render_dataframe(view: pd.DataFrame) -> None:
    column_config = {}
    for column in ("Ссылка", "Источник FunPay"):
        if column in view.columns:
            column_config[column] = st.column_config.LinkColumn(column)

    st.dataframe(
        view,
        hide_index=True,
        height=650,
        width="stretch",
        column_config=column_config,
    )


@st.cache_data(ttl=30)
def _load_funpay_items() -> pd.DataFrame:
    rows = fetch_scraped_items(source="funpay", limit=200)
    return pd.DataFrame([dict(row) for row in rows])


@st.cache_data(ttl=30)
def _load_kwork_services() -> pd.DataFrame:
    rows = fetch_scraped_items(source="kwork", limit=200)
    return pd.DataFrame([dict(row) for row in rows])


@st.cache_data(ttl=30)
def _load_opportunities() -> pd.DataFrame:
    rows = fetch_opportunities()
    return pd.DataFrame([dict(row) for row in rows])


@st.cache_data(ttl=30)
def _load_kwork_parse_errors() -> pd.DataFrame:
    rows = [dict(row) for row in fetch_parse_errors()]
    kwork_rows = [row for row in rows if row.get("source") == "kwork"]
    return pd.DataFrame(kwork_rows[:20])


def _select_columns(dataframe: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    existing = [column for column in columns if column in dataframe.columns]
    view = dataframe.loc[:, existing].copy()
    object_columns = view.select_dtypes(include="object").columns
    view.loc[:, object_columns] = view.loc[:, object_columns].fillna("")
    return view


if __name__ == "__main__":
    main()
