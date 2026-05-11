"""Streamlit-интерфейс FunOrk."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app.collectors.http_client import AntiBanError, HttpClientError
from app.config import config
from app.db import fetch_parse_errors, fetch_scraped_items, init_db
from app.reports.export_html import export_to_html
from app.services.kwork_collection_service import collect_kwork_with_summary


st.set_page_config(page_title="FunOrk", layout="wide")


def main() -> None:
    init_db()

    st.title("FunOrk - анализ FunPay и Kwork")
    _render_kwork_collection()
    st.divider()
    _render_kwork_services_table()
    st.divider()
    _render_kwork_errors()
    st.divider()
    _render_export()


def _render_kwork_collection() -> None:
    st.subheader("Сбор каталога Kwork")

    force = st.checkbox("Обновить без кеша (--force)", value=False)
    if config.KWORK_COOKIE:
        st.success("KWORK_COOKIE найден")
    else:
        st.warning(
            "KWORK_COOKIE не задан. Kwork может вернуть пустую страницу или SmartCaptcha. "
            "В таком случае используйте Cookie или ручной импорт."
        )

    col_collect, col_refresh = st.columns([1, 1])
    with col_collect:
        collect_clicked = st.button("Собрать каталог Kwork", type="primary")
    with col_refresh:
        if st.button("Обновить таблицу Kwork"):
            st.cache_data.clear()
            st.rerun()

    if not collect_clicked:
        return

    with st.spinner("Собираю каталог и услуги Kwork..."):
        try:
            summary = collect_kwork_with_summary(force=force)
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


def _render_kwork_services_table() -> None:
    st.subheader("Последние собранные услуги Kwork")
    rows = _load_kwork_services()
    if rows.empty:
        st.info("Услуги Kwork пока не сохранены. Запустите сбор каталога Kwork.")
        return

    columns = ["title", "price", "currency", "category", "subcategory", "url", "scraped_at", "parse_status"]
    view = _select_columns(rows, columns).rename(
        columns={
            "title": "Название",
            "price": "Цена",
            "currency": "Валюта",
            "category": "Категория",
            "subcategory": "Подкатегория",
            "url": "Ссылка",
            "scraped_at": "Собрано",
            "parse_status": "Статус парсинга",
        }
    )
    st.dataframe(
        view,
        hide_index=True,
        width="stretch",
        column_config={
            "Ссылка": st.column_config.LinkColumn("Ссылка"),
        },
    )


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
    st.dataframe(
        view,
        hide_index=True,
        width="stretch",
        column_config={
            "Ссылка": st.column_config.LinkColumn("Ссылка"),
        },
    )


def _render_export() -> None:
    st.subheader("Отчет")
    if st.button("Сформировать отчет"):
        try:
            report_path = export_to_html()
        except Exception as error:
            st.error(f"Не удалось сформировать HTML-отчет: {error}")
            return
        st.success(f"HTML-отчет сформирован: {report_path}")


@st.cache_data(ttl=30)
def _load_kwork_services() -> pd.DataFrame:
    rows = fetch_scraped_items(source="kwork", limit=50)
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
