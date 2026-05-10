"""Локальный веб-интерфейс FunOrk на Streamlit."""

from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.cli import analyze_saved_items, collect_funpay, collect_kwork, export_report
from app.db import (
    fetch_kwork_categories,
    fetch_opportunities,
    fetch_parse_errors,
    fetch_run_history,
    fetch_scraped_items,
    init_db,
    save_run_history,
)
from app.models import RunHistory


st.set_page_config(
    page_title="FunOrk Дашборд",
    layout="wide",
)


def main() -> None:
    init_db()
    _render_header()
    _render_sidebar()

    findings_tab, kwork_cat_tab, kwork_lots_tab, history_tab = st.tabs(
        [
            "Находки (Связки)",
            "Категории Kwork",
            "Лоты Kwork",
            "История и Логи",
        ]
    )

    with findings_tab:
        _render_findings()
    with kwork_cat_tab:
        _render_kwork_categories()
    with kwork_lots_tab:
        _render_kwork_lots()
    with history_tab:
        _render_history()


def _render_header() -> None:
    st.title("FunOrk Дашборд")
    st.caption(
        "Анализатор связок FunPay -> Kwork: парсинг, оценка риска и поиск рабочих направлений."
    )


def _render_sidebar() -> None:
    st.sidebar.header("Управление парсингом")
    st.sidebar.caption("Запуски выполняются локально и могут занять несколько минут.")

    if st.sidebar.button("Собрать каталог FunPay", width="stretch"):
        _run_action(
            command="collect-funpay",
            label="Каталог FunPay собран",
            action=collect_funpay,
        )

    if st.sidebar.button("Собрать каталог Kwork", width="stretch"):
        _run_action(
            command="collect-kwork",
            label="Каталог Kwork собран",
            action=collect_kwork,
        )

    if st.sidebar.button("Запустить анализ", width="stretch"):
        _run_action(
            command="analyze",
            label="Анализ завершен",
            action=analyze_saved_items,
        )

    if st.sidebar.button("Сгенерировать XLSX", width="stretch"):
        _run_action(
            command="export-xlsx",
            label="XLSX-отчет готов",
            action=lambda: export_report("xlsx"),
        )

    st.sidebar.divider()
    if st.sidebar.button("Обновить таблицы", width="stretch"):
        st.cache_data.clear()
        st.rerun()


def _run_action(command: str, label: str, action: Callable[[], Any]) -> None:
    started_at = datetime.now(timezone.utc)
    try:
        with st.spinner("Идет сбор данных, это может занять несколько минут..."):
            result = action()
        _save_ui_run(command, "success", started_at, result=result)
        st.cache_data.clear()
        st.success(_success_message(label, result))
    except Exception as error:
        _save_ui_run(command, "failed", started_at, error=str(error))
        st.cache_data.clear()
        st.error(f"Команда завершилась с ошибкой: {error}")


def _save_ui_run(
    command: str,
    status: str,
    started_at: datetime,
    *,
    result: object | None = None,
    error: str | None = None,
) -> None:
    processed_count = int(result) if isinstance(result, int) else 1 if result else 0
    save_run_history(
        RunHistory(
            command=f"ui:{command}",
            status=status,  # type: ignore[arg-type]
            started_at=started_at,
            processed_count=processed_count,
            error=error,
        )
    )


def _success_message(label: str, result: object) -> str:
    if isinstance(result, Path):
        return f"{label}: {result}"
    if isinstance(result, int):
        return f"{label}: обработано {result} записей."
    return label


def _render_findings() -> None:
    st.subheader("Связки")
    dataframe = _load_opportunities()
    if dataframe.empty:
        st.info("Связки пока не найдены. Запустите анализ после сбора данных.")
        return

    view = _rename_existing_columns(
        dataframe,
        {
            "possible_kwork_service_title": "Название",
            "possible_kwork_category": "Категория Kwork",
            "source_url": "Ссылка FunPay",
            "buy_price": "Цена закупки",
            "sell_price": "Цена продажи",
            "estimated_margin_percent": "Маржа %",
            "risk_level": "Риск",
            "verdict": "Вердикт",
        },
    )
    _show_findings_metrics(dataframe)
    st.dataframe(
        view,
        width="stretch",
        hide_index=True,
        column_config={
            "Ссылка FunPay": st.column_config.LinkColumn("Ссылка FunPay"),
        },
    )


def _render_kwork_categories() -> None:
    st.subheader("Перегретость ниш Kwork")
    dataframe = _load_kwork_categories()
    if dataframe.empty:
        st.info("Категории Kwork пока не собраны.")
        return

    view = _rename_existing_columns(
        dataframe,
        {
            "category_name": "Категория",
            "subcategory_name": "Подкатегория",
            "average_price": "Средняя цена",
            "min_price": "Минимальная цена",
            "competitors_count": "Конкуренты",
            "keywords": "Ключевые слова",
            "parse_status": "Статус парсинга",
            "parse_error": "Ошибка",
            "last_checked_at": "Проверено",
            "url": "Ссылка",
        },
    )
    st.dataframe(
        view,
        width="stretch",
        hide_index=True,
        column_config={
            "Ссылка": st.column_config.LinkColumn("Ссылка"),
        },
    )


def _render_kwork_lots() -> None:
    st.subheader("Лоты Kwork")
    dataframe = _load_kwork_lots()
    if dataframe.empty:
        st.info("Лоты Kwork пока не собраны. Запустите сбор каталога Kwork.")
        return

    view = _rename_existing_columns(
        dataframe,
        {
            "title": "Название",
            "url": "Ссылка",
            "price": "Цена",
            "currency": "Валюта",
            "category": "Категория",
            "subcategory": "Подкатегория",
            "parse_status": "Статус парсинга",
            "parse_error": "Ошибка парсинга",
            "scraped_at": "Собрано",
        },
    )
    st.dataframe(
        view,
        width="stretch",
        hide_index=True,
        column_config={
            "Ссылка": st.column_config.LinkColumn("Ссылка"),
        },
    )


def _render_history() -> None:
    st.subheader("История запусков")
    history = _load_run_history()
    if history.empty:
        st.info("История запусков пока пуста.")
    else:
        view = _rename_existing_columns(
            history,
            {
                "command": "Команда",
                "status": "Статус",
                "started_at": "Начало",
                "finished_at": "Завершение",
                "processed_count": "Обработано",
                "error": "Ошибка",
            },
        )
        st.dataframe(view, width="stretch", hide_index=True)

    st.subheader("Ошибки парсинга")
    errors = _load_parse_errors()
    if errors.empty:
        st.success("Ошибок парсинга не найдено.")
        return

    view = _rename_existing_columns(
        errors,
        {
            "source": "Источник",
            "url": "Ссылка",
            "status": "Статус",
            "error": "Ошибка",
            "created_at": "Когда",
        },
    )
    st.dataframe(
        view,
        width="stretch",
        hide_index=True,
        column_config={
            "Ссылка": st.column_config.LinkColumn("Ссылка"),
        },
    )


@st.cache_data(ttl=30)
def _load_opportunities() -> pd.DataFrame:
    return _rows_to_dataframe(fetch_opportunities())


@st.cache_data(ttl=30)
def _load_kwork_categories() -> pd.DataFrame:
    return _rows_to_dataframe(fetch_kwork_categories())


@st.cache_data(ttl=30)
def _load_kwork_lots() -> pd.DataFrame:
    return _rows_to_dataframe(fetch_scraped_items(source="kwork", limit=1000))


@st.cache_data(ttl=30)
def _load_run_history() -> pd.DataFrame:
    return _rows_to_dataframe(fetch_run_history())


@st.cache_data(ttl=30)
def _load_parse_errors() -> pd.DataFrame:
    return _rows_to_dataframe(fetch_parse_errors())


def _rows_to_dataframe(rows: list[Any]) -> pd.DataFrame:
    return pd.DataFrame([dict(row) for row in rows])


def _rename_existing_columns(
    dataframe: pd.DataFrame,
    columns: dict[str, str],
) -> pd.DataFrame:
    existing = [column for column in columns if column in dataframe.columns]
    view = dataframe[existing].rename(columns=columns)
    object_columns = view.select_dtypes(include="object").columns
    view.loc[:, object_columns] = view.loc[:, object_columns].fillna("")
    return view


def _show_findings_metrics(dataframe: pd.DataFrame) -> None:
    total = len(dataframe)
    recommended = 0
    if "verdict" in dataframe.columns:
        recommended = int(dataframe["verdict"].astype(str).str.contains("тест").sum())
    columns = st.columns(3)
    columns[0].metric("Связок в базе", total)
    columns[1].metric("Кандидатов в тест", recommended)
    score_column = "opportunity_score"
    if score_column in dataframe.columns:
        score = pd.to_numeric(dataframe[score_column], errors="coerce").max()
        columns[2].metric("Лучший скор", f"{score:.1f}" if pd.notna(score) else "нет")
    else:
        columns[2].metric("Лучший скор", "нет")


if __name__ == "__main__":
    main()
