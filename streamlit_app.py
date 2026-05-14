"""Streamlit-интерфейс FunOrk."""

from __future__ import annotations

import html
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TypeVar

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from app.adapters.kwork_card_generator import generate_adapted_kwork_card
from app.cli import analyze_saved_items, collect_funpay
from app.collectors.funpay import collect_catalog_id_groups
from app.collectors.http_client import AntiBanError, HttpClientError
from app.collectors.kwork import SessionValidationError
from app.config import config
from app.db import (
    clear_funpay_work_table,
    clear_kwork_work_table,
    clear_opportunities_work_table,
    clean_funpay_table_by_current_filters,
    clean_kwork_table_by_current_filters,
    clean_opportunities_table_by_current_filters,
    fetch_adapted_kwork_card,
    fetch_opportunities,
    fetch_parse_errors,
    fetch_run_history,
    fetch_scraped_items,
    init_db,
    save_adapted_kwork_card,
    save_run_history,
    update_opportunity_status,
)
from app.importers import import_funpay_file, import_kwork_file
from app.models import RunHistory
from app.reports.export_html import export_to_html
from app.rules.loader import (
    load_funpay_stop_categories,
    normalize_rule_text_list,
    save_funpay_stop_categories,
)
from app.services.kwork_collection_service import collect_kwork_with_summary


st.set_page_config(page_title="FunOrk", layout="wide")
T = TypeVar("T")

STATUS_LABELS = {
    "new": "новый",
    "interesting": "интересно",
    "in_progress": "в работу",
    "rejected": "отклонено",
    "kwork_created": "создан кворк",
}
STATUS_BY_LABEL = {label: key for key, label in STATUS_LABELS.items()}
COMPLIANCE_LABELS = {
    "ready": "готова к ручной публикации",
    "needs_manual_review": "нужна ручная проверка",
    "blocked": "заблокировано",
}


def main() -> None:
    init_db()

    st.title("FunOrk - анализ FunPay и Kwork")
    _render_controls()
    st.divider()
    _render_data_tabs()


def _render_data_tabs() -> None:
    funpay_tab, kwork_tab, comparison_tab, logs_tab = st.tabs(
        [
            "FunPay",
            "Kwork",
            "Сравнение и выводы",
            "Логи и ошибки",
        ]
    )

    with funpay_tab:
        _render_funpay_table()
    with kwork_tab:
        _render_kwork_services_table()
    with comparison_tab:
        _render_opportunities_table()
    with logs_tab:
        _render_logs_and_errors()


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
            report_path = _run_task_with_progress(
                "Формирую HTML-отчет",
                lambda progress_callback: export_to_html(),
                running_stage="Собираю данные и создаю файл отчета...",
            )
        except Exception as error:
            st.error(f"Не удалось сформировать HTML-отчет: {error}")
        else:
            st.success(f"HTML-отчет сформирован: {report_path}")

    if funpay_clicked:
        try:
            processed = _run_task_with_progress(
                "Парсинг FunPay",
                lambda progress_callback: _run_with_history(
                    "ui:collect-funpay",
                    lambda: collect_funpay(force=force, progress_callback=progress_callback),
                ),
                running_stage="Собираю категории, лоты и цены FunPay...",
            )
        except Exception as error:
            st.error(f"Ошибка при сборе FunPay: {error}")
        else:
            st.cache_data.clear()
            st.success(f"Сбор FunPay завершен: сохранено {processed} лотов.")

    if kwork_clicked:
        try:
            summary = _run_task_with_progress(
                "Парсинг Kwork",
                lambda progress_callback: collect_kwork_with_summary(
                    force=force,
                    progress_callback=progress_callback,
                ),
                running_stage="Собираю каталог, услуги и debug-данные Kwork...",
            )
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
        try:
            processed = _run_task_with_progress(
                "Анализ возможностей",
                lambda progress_callback: _run_with_history(
                    "ui:analyze",
                    lambda: analyze_saved_items(progress_callback=progress_callback),
                ),
                running_stage="Сравниваю FunPay с Kwork, считаю маржу и риски...",
            )
        except Exception as error:
            st.error(f"Не удалось выполнить анализ: {error}")
        else:
            st.cache_data.clear()
            st.success(f"Анализ завершен: обработано {processed} лотов FunPay.")

    _render_import_controls()
    _render_stop_categories_controls()


def _render_import_controls() -> None:
    with st.expander("Ручной импорт CSV/JSON", expanded=False):
        source = st.radio(
            "Источник данных",
            ["FunPay", "Kwork"],
            horizontal=True,
        )
        uploaded_file = st.file_uploader(
            "Загрузите CSV или JSON",
            type=["csv", "json"],
            key="manual_import_file",
        )
        if st.button("Импортировать файл", disabled=uploaded_file is None):
            if uploaded_file is None:
                st.warning("Сначала выберите файл для импорта.")
                return
            suffix = Path(uploaded_file.name).suffix.lower()
            if suffix not in {".csv", ".json"}:
                st.error("Поддерживаются только файлы CSV и JSON.")
                return
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
                temp_file.write(uploaded_file.getbuffer())
                temp_path = Path(temp_file.name)
            try:
                if source == "FunPay":
                    imported = _run_task_with_progress(
                        "Импорт FunPay",
                        lambda progress_callback: _run_with_history(
                            "ui:import-funpay",
                            lambda: import_funpay_file(temp_path),
                        ),
                        running_stage="Читаю файл и сохраняю лоты FunPay...",
                    )
                else:
                    imported = _run_task_with_progress(
                        "Импорт Kwork",
                        lambda progress_callback: _run_with_history(
                            "ui:import-kwork",
                            lambda: import_kwork_file(temp_path),
                        ),
                        running_stage="Читаю файл и сохраняю данные Kwork...",
                    )
            except Exception as error:
                st.error(f"Не удалось импортировать файл: {error}")
            else:
                st.cache_data.clear()
                st.success(f"Импорт завершен: сохранено записей {imported}.")
            finally:
                temp_path.unlink(missing_ok=True)


def _render_stop_categories_controls() -> None:
    with st.expander("Стоп-категории FunPay", expanded=False):
        payload = load_funpay_stop_categories()
        categories = normalize_rule_text_list(payload.get("categories", []))
        disabled = normalize_rule_text_list(payload.get("disabled_categories", []))
        category_ids = _normalize_category_ids(payload.get("category_ids", {}))
        enabled = bool(payload.get("enabled", True))

        col_enabled, col_count = st.columns([1, 3])
        with col_enabled:
            next_enabled = st.checkbox(
                "Фильтр включен",
                value=enabled,
                key="stop_categories_enabled",
            )
        with col_count:
            st.caption(
                f"Всего стоп-категорий: {len(categories)}. "
                f"Временно отключено: {len(disabled)}. "
                f"ID FunPay найдено: {len(category_ids)}."
            )

        search = st.text_input(
            "Поиск по стоп-категориям",
            key="stop_categories_search",
            placeholder="Например: Roblox, Dota, World of Warcraft",
        )
        view_categories = categories
        if search.strip():
            query = search.casefold().strip()
            view_categories = [category for category in categories if query in category.casefold()]
        st.dataframe(
            pd.DataFrame(
                {
                    "Стоп-категория": view_categories[:300],
                    "ID FunPay": [
                        ", ".join(category_ids.get(category, []))
                        for category in view_categories[:300]
                    ],
                    "Статус": [
                        "отключена" if category in disabled else "активна"
                        for category in view_categories[:300]
                    ],
                }
            ),
            hide_index=True,
            height=260,
            width="stretch",
        )

        next_disabled = st.multiselect(
            "Временно не применять стоп-фильтр к выбранным категориям",
            options=categories,
            default=[category for category in disabled if category in categories],
            key="stop_categories_disabled",
        )
        new_category = st.text_input(
            "Добавить новую стоп-категорию",
            key="stop_categories_new",
            placeholder="Название категории FunPay",
        )

        col_save, col_reset = st.columns([1, 4])
        with col_save:
            save_clicked = st.button("Сохранить стоп-лист", key="save_stop_categories")
        with col_reset:
            st.caption("Изменения применятся к следующему парсингу и к кнопке перепроверки по фильтрам.")

        col_sync, col_sync_note = st.columns([1, 4])
        with col_sync:
            sync_clicked = st.button("Обновить ID", key="sync_stop_category_ids")
        with col_sync_note:
            st.caption("ID берутся с главной страницы FunPay: основная игра и все ее разделы.")

        if save_clicked:
            updated_categories = list(dict.fromkeys([*categories, new_category.strip()] if new_category.strip() else categories))
            updated_category_ids = {
                category: category_ids[category]
                for category in updated_categories
                if category in category_ids
            }
            save_funpay_stop_categories(
                {
                    "enabled": next_enabled,
                    "categories": updated_categories,
                    "disabled_categories": next_disabled,
                    "category_ids": updated_category_ids,
                }
            )
            st.cache_data.clear()
            st.success("Стоп-категории FunPay сохранены.")
            st.rerun()

        if sync_clicked:
            try:
                sync_report = _run_task_with_progress(
                    "Обновление ID стоп-категорий",
                    lambda progress_callback: _sync_funpay_stop_category_ids(
                        categories,
                        payload,
                        progress_callback=progress_callback,
                    ),
                    running_stage="Читаю каталог FunPay и сверяю его со стоп-листом...",
                )
            except Exception as error:
                st.error(f"Не удалось обновить ID стоп-категорий: {error}")
            else:
                st.cache_data.clear()
                st.success(
                    f"ID обновлены: найдено {sync_report['found']}, "
                    f"не найдено {sync_report['missing']}."
                )
                if sync_report["missing_names"]:
                    st.warning(
                        "Не нашел ID для части категорий: "
                        + ", ".join(sync_report["missing_names"][:20])
                    )
                st.rerun()


def _normalize_category_ids(value: object) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, list[str]] = {}
    for category, raw_ids in value.items():
        category_name = str(category).strip()
        category_ids = _normalize_category_id_list(raw_ids)
        if category_name and category_ids:
            normalized[category_name] = category_ids
    return normalized


def _normalize_category_id_list(value: object) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = [value]
    normalized: list[str] = []
    for item in values:
        id_text = str(item).strip()
        if id_text and id_text not in normalized:
            normalized.append(id_text)
    return normalized


def _sync_funpay_stop_category_ids(
    categories: list[str],
    payload: dict[str, object],
    *,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> dict[str, object]:
    _report_streamlit_progress(progress_callback, 1, 4, "Загружаю основной каталог FunPay...")
    groups = collect_catalog_id_groups(force=True, limit=10000)
    groups_by_name = {
        _category_match_key(group.name): group
        for group in groups
        if group.category_ids
    }

    category_ids: dict[str, list[str]] = {}
    missing_names: list[str] = []
    total = max(1, len(categories))
    for index, category in enumerate(categories, start=1):
        group = groups_by_name.get(_category_match_key(category))
        if group and group.category_ids:
            category_ids[category] = list(group.category_ids)
        else:
            missing_names.append(category)
        if index % 50 == 0 or index == total:
            _report_streamlit_progress(
                progress_callback,
                index,
                total,
                f"Сверяю стоп-категории {index}/{total}. Найдено ID: {len(category_ids)}.",
            )

    save_funpay_stop_categories(
        {
            **payload,
            "categories": categories,
            "disabled_categories": normalize_rule_text_list(payload.get("disabled_categories", [])),
            "category_ids": category_ids,
        }
    )
    return {
        "found": sum(len(ids) for ids in category_ids.values()),
        "matched_categories": len(category_ids),
        "missing": len(missing_names),
        "missing_names": missing_names,
    }


def _category_match_key(value: str) -> str:
    return " ".join(
        value.replace("’", "'")
        .replace("`", "'")
        .replace("ё", "е")
        .casefold()
        .split()
    )


def _report_streamlit_progress(
    progress_callback: Callable[[int, int, str], None] | None,
    current: int,
    total: int,
    message: str,
) -> None:
    if progress_callback is not None:
        progress_callback(current, total, message)


def _run_task_with_progress(
    title: str,
    action: Callable[[Callable[[int, int, str], None]], T],
    *,
    running_stage: str,
) -> T:
    status = st.status(title, state="running", expanded=True)
    progress = st.progress(0.0, text="Готовлю задачу...")
    current_stage = st.empty()
    log_placeholder = st.empty()
    log_messages: list[str] = []
    last_progress = 0.0

    def progress_callback(current: int, total: int, message: str) -> None:
        nonlocal last_progress
        safe_total = max(1, int(total or 1))
        safe_current = max(0, min(int(current or 0), safe_total))
        progress_value = 0.1 + ((safe_current / safe_total) * 0.8)
        progress_value = max(last_progress, min(0.95, progress_value))
        last_progress = progress_value
        progress.progress(progress_value, text=message)
        current_stage.info(message)
        _append_progress_log(log_placeholder, log_messages, message)

    _append_progress_log(log_placeholder, log_messages, "Готовлю задачу...")
    try:
        progress.progress(0.2, text="Проверяю входные данные...")
        last_progress = 0.2
        current_stage.info("Проверяю входные данные...")
        _append_progress_log(log_placeholder, log_messages, "Проверяю входные данные...")
        progress_callback(1, 20, running_stage)
        result = action(progress_callback)
        progress.progress(0.95, text="Обновляю данные интерфейса...")
        current_stage.info("Обновляю данные интерфейса...")
        _append_progress_log(log_placeholder, log_messages, "Обновляю данные интерфейса...")
        progress.progress(1.0, text="Задача завершена.")
        current_stage.success("Задача завершена.")
        _append_progress_log(log_placeholder, log_messages, "Задача завершена.")
        status.update(label=f"{title}: завершено", state="complete", expanded=False)
        return result
    except Exception:
        progress.progress(1.0, text="Задача остановлена из-за ошибки.")
        current_stage.error("Задача остановлена из-за ошибки.")
        _append_progress_log(log_placeholder, log_messages, "Задача остановлена из-за ошибки.")
        status.update(label=f"{title}: ошибка", state="error", expanded=True)
        raise


def _append_progress_log(
    placeholder: object,
    messages: list[str],
    message: str,
    *,
    limit: int = 200,
) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    messages.append(f"{timestamp}  {message}")
    del messages[:-limit]
    lines = "<br>".join(html.escape(line) for line in messages)
    placeholder.markdown(
        f"""
        <div style="
            height: 220px;
            overflow-y: auto;
            border: 1px solid #d0d7de;
            border-radius: 6px;
            padding: 10px 12px;
            background: #0f172a;
            color: #e5e7eb;
            font-family: ui-monospace, SFMono-Regular, Consolas, 'Liberation Mono', monospace;
            font-size: 13px;
            line-height: 1.45;
            white-space: normal;
        ">
            {lines}
        </div>
        """,
        unsafe_allow_html=True,
    )


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
    _render_full_clear_current_table_controls(
        table_key="funpay",
        label="таблицу FunPay",
        action=clear_funpay_work_table,
    )
    _render_clear_table_controls(
        table_key="funpay",
        label="таблицу FunPay",
        action=lambda progress_callback: clean_funpay_table_by_current_filters(
            progress_callback=progress_callback
        ),
    )
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
        "category_id",
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
            "category_id": "ID",
            "url": "Ссылка",
            "scraped_at": "Собрано",
            "parse_status": "Статус парсинга",
        }
    )
    _render_dataframe(view)


def _render_kwork_services_table() -> None:
    st.subheader("Kwork: собранные услуги")
    _render_full_clear_current_table_controls(
        table_key="kwork",
        label="таблицу Kwork",
        action=clear_kwork_work_table,
    )
    _render_clear_table_controls(
        table_key="kwork",
        label="таблицу Kwork",
        action=lambda progress_callback: clean_kwork_table_by_current_filters(
            progress_callback=progress_callback
        ),
    )
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
    _render_full_clear_current_table_controls(
        table_key="opportunities",
        label="таблицу сравнения",
        action=clear_opportunities_work_table,
    )
    _render_clear_table_controls(
        table_key="opportunities",
        label="таблицу сравнения и карточки Kwork",
        action=lambda progress_callback: clean_opportunities_table_by_current_filters(
            progress_callback=progress_callback
        ),
    )
    rows = _load_opportunities()
    if rows.empty:
        st.info("Готовых выводов пока нет. Запустите анализ после сбора FunPay и Kwork.")
        return

    rows = _prepare_opportunities(rows)
    rows = _filter_opportunities(rows)

    columns = [
        "id",
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
        "processing_status_label",
        "is_recommended_label",
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
            "processing_status_label": "Статус обработки",
            "is_recommended_label": "Рекомендовано",
            "recommendation": "Рекомендация",
            "safe_wording": "Безопасная формулировка",
            "source_url": "Источник FunPay",
            "created_at": "Дата анализа",
        }
    )
    _render_dataframe(view, height=420)
    _render_opportunity_actions(rows)


def _render_clear_table_controls(
    *,
    table_key: str,
    label: str,
    action: Callable[[Callable[[int, int, str], None]], dict[str, object]],
) -> None:
    with st.expander(f"Перепроверка по фильтрам: {label}", expanded=False):
        stored_report = st.session_state.get(f"clean_report_{table_key}")
        if isinstance(stored_report, dict):
            _render_clean_report(stored_report)
        st.info(
            f"Кнопка заново прогонит уже сохраненные строки через актуальные фильтры "
            f"и удалит только неподходящие записи из раздела: {label}. "
            "Полная очистка таблицы не выполняется."
        )
        confirm = st.checkbox(
            f"Я понимаю, что нужно удалить из {label} только строки, не проходящие текущие фильтры",
            key=f"confirm_clear_{table_key}",
        )
        if st.button(
            "Почистить по текущим фильтрам",
            key=f"clear_{table_key}",
            disabled=not confirm,
        ):
            try:
                report = _run_task_with_progress(
                    f"Перепроверка: {label}",
                    lambda progress_callback: action(progress_callback),
                    running_stage="Проверяю сохраненные строки по актуальным фильтрам...",
                )
            except Exception as error:
                st.error(f"Не удалось перепроверить {label}: {error}")
            else:
                st.cache_data.clear()
                deleted = int(report.get("deleted") or 0)
                st.session_state[f"clean_report_{table_key}"] = report
                st.success(f"Готово: удалено неподходящих записей {deleted}.")
                st.rerun()


def _render_full_clear_current_table_controls(
    *,
    table_key: str,
    label: str,
    action: Callable[[], dict[str, int]],
) -> None:
    with st.expander(f"Полная очистка: {label}", expanded=False):
        stored_report = st.session_state.get(f"full_clear_report_{table_key}")
        if isinstance(stored_report, dict):
            _render_full_clear_report(stored_report)
        st.warning(
            f"Это полностью удалит данные только из текущего раздела: {label}. "
            "Остальные вкладки, .env, логи и HTML-отчеты не трогаются."
        )
        confirm = st.checkbox(
            f"Я понимаю, что нужно полностью очистить {label}",
            key=f"confirm_full_clear_{table_key}",
        )
        col_button, col_hint = st.columns([1, 5])
        with col_button:
            clear_clicked = st.button(
                "Очистить",
                key=f"full_clear_{table_key}",
                disabled=not confirm,
            )
        with col_hint:
            st.caption("Компактная полная очистка только этой вкладки.")

        if clear_clicked:
            try:
                report = _run_task_with_progress(
                    f"Полная очистка: {label}",
                    lambda progress_callback: action(),
                    running_stage="Удаляю данные текущей таблицы...",
                )
            except Exception as error:
                st.error(f"Не удалось полностью очистить {label}: {error}")
            else:
                st.cache_data.clear()
                st.session_state[f"full_clear_report_{table_key}"] = report
                total_deleted = sum(int(value) for value in report.values())
                st.success(f"Полная очистка завершена: удалено записей {total_deleted}.")
                st.rerun()


def _render_clean_report(report: dict[str, object]) -> None:
    reasons = report.get("reasons")
    if not isinstance(reasons, dict) or not reasons:
        st.info("Удалений по конкретным причинам нет.")
        return
    rows = [
        {"Причина": str(reason), "Удалено": int(count)}
        for reason, count in sorted(reasons.items(), key=lambda item: int(item[1]), reverse=True)
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=260)


def _render_full_clear_report(report: dict[str, object]) -> None:
    rows = [
        {"Раздел": str(section), "Удалено": int(count)}
        for section, count in report.items()
        if int(count) > 0
    ]
    if not rows:
        st.info("Рабочие таблицы уже были пустыми.")
        return
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=260)


def _prepare_opportunities(rows: pd.DataFrame) -> pd.DataFrame:
    prepared = rows.copy()
    if "processing_status" not in prepared.columns:
        prepared["processing_status"] = "new"
    prepared["processing_status"] = prepared["processing_status"].fillna("new")
    prepared["processing_status_label"] = prepared["processing_status"].map(STATUS_LABELS).fillna("новый")
    if "estimated_margin_percent" not in prepared.columns:
        prepared["estimated_margin_percent"] = None
    prepared["estimated_margin_percent"] = pd.to_numeric(prepared["estimated_margin_percent"], errors="coerce")
    if "buy_price" not in prepared.columns:
        prepared["buy_price"] = None
    if "sell_price" not in prepared.columns:
        prepared["sell_price"] = None
    prepared["buy_price_numeric"] = pd.to_numeric(
        prepared["buy_price"],
        errors="coerce",
    )
    prepared["sell_price_numeric"] = pd.to_numeric(
        prepared["sell_price"],
        errors="coerce",
    )
    if "opportunity_score" not in prepared.columns:
        prepared["opportunity_score"] = 0
    prepared["opportunity_score"] = pd.to_numeric(
        prepared["opportunity_score"],
        errors="coerce",
    ).fillna(0)
    if "risk_level" not in prepared.columns:
        prepared["risk_level"] = ""
    if "verdict" not in prepared.columns:
        prepared["verdict"] = ""
    prepared["is_recommended"] = (
        (prepared["risk_level"] != "RED")
        & (prepared["opportunity_score"] >= 60)
        & (~prepared["verdict"].fillna("").str.contains("не брать", case=False, regex=False))
    )
    prepared["is_recommended_label"] = prepared["is_recommended"].map({True: "да", False: "нет"})
    return prepared


def _filter_opportunities(rows: pd.DataFrame) -> pd.DataFrame:
    with st.expander("Фильтры", expanded=True):
        col_a, col_b, col_c, col_d = st.columns(4)
        with col_a:
            selected_risks = st.multiselect("Риск", sorted(rows["risk_level"].dropna().unique()))
            only_recommended = st.checkbox("Только рекомендованные")
        with col_b:
            selected_funpay = st.multiselect("Категория FunPay", _sorted_options(rows, "source_category"))
            selected_kwork = st.multiselect("Категория Kwork", _sorted_options(rows, "possible_kwork_category"))
        with col_c:
            selected_verdicts = st.multiselect("Вывод", _sorted_options(rows, "verdict"))
            margin_min, margin_max = _number_range(rows["estimated_margin_percent"], fallback=(0.0, 300.0))
            margin_range = st.slider("Маржа, %", margin_min, margin_max, (margin_min, margin_max))
        with col_d:
            buy_min, buy_max = _number_range(rows["buy_price_numeric"], fallback=(0.0, 10000.0))
            sell_min, sell_max = _number_range(rows["sell_price_numeric"], fallback=(0.0, 30000.0))
            buy_range = st.slider("Цена закупки", buy_min, buy_max, (buy_min, buy_max))
            sell_range = st.slider("Цена продажи", sell_min, sell_max, (sell_min, sell_max))

    filtered = rows.copy()
    if selected_risks:
        filtered = filtered[filtered["risk_level"].isin(selected_risks)]
    if selected_funpay:
        filtered = filtered[filtered["source_category"].isin(selected_funpay)]
    if selected_kwork:
        filtered = filtered[filtered["possible_kwork_category"].isin(selected_kwork)]
    if selected_verdicts:
        filtered = filtered[filtered["verdict"].isin(selected_verdicts)]
    if only_recommended:
        filtered = filtered[filtered["is_recommended"]]

    filtered = filtered[
        filtered["estimated_margin_percent"].fillna(margin_range[0]).between(*margin_range)
        & filtered["buy_price_numeric"].fillna(buy_range[0]).between(*buy_range)
        & filtered["sell_price_numeric"].fillna(sell_range[0]).between(*sell_range)
    ]
    if filtered.empty:
        st.warning("По выбранным фильтрам ничего не найдено.")
    return filtered


def _render_opportunity_actions(rows: pd.DataFrame) -> None:
    if rows.empty:
        return

    st.subheader("Адаптация под Kwork")
    options = {
        _opportunity_label(row): int(row["id"])
        for _, row in rows.iterrows()
        if pd.notna(row.get("id"))
    }
    selected_label = st.selectbox("Выберите opportunity", list(options.keys()))
    selected_id = options[selected_label]
    selected_row = rows[rows["id"] == selected_id].iloc[0].to_dict()

    col_status, col_action = st.columns([1, 2])
    with col_status:
        current_status = str(selected_row.get("processing_status") or "new")
        current_label = STATUS_LABELS.get(current_status, "новый")
        next_label = st.selectbox(
            "Статус обработки",
            list(STATUS_BY_LABEL.keys()),
            index=list(STATUS_BY_LABEL.keys()).index(current_label),
        )
        if st.button("Сохранить статус"):
            _run_task_with_progress(
                "Обновление статуса",
                lambda progress_callback: update_opportunity_status(selected_id, STATUS_BY_LABEL[next_label]),
                running_stage="Сохраняю новый статус opportunity...",
            )
            st.cache_data.clear()
            st.success("Статус обновлен.")
            st.rerun()

    with col_action:
        if st.button("Адаптировать", type="primary"):
            try:
                card_id = _run_task_with_progress(
                    "Адаптация карточки",
                    lambda progress_callback: _create_and_save_adapted_card(selected_row),
                    running_stage="Готовлю текст, проверяю риски и сохраняю карточку...",
                )
            except Exception as error:
                st.error(f"Не удалось подготовить карточку: {error}")
            else:
                st.session_state["adapted_card_opportunity_id"] = selected_id
                st.session_state["adapted_card_id"] = card_id
                st.cache_data.clear()
                st.success("Карточка подготовлена.")

    card_opportunity_id = st.session_state.get("adapted_card_opportunity_id")
    if card_opportunity_id:
        card_row = fetch_adapted_kwork_card(int(card_opportunity_id))
        if card_row is not None:
            _render_adapted_card(dict(card_row))


def _create_and_save_adapted_card(selected_row: dict[str, object]) -> int:
    card = generate_adapted_kwork_card(selected_row)
    return save_adapted_kwork_card(card)


def _render_adapted_card(card: dict[str, object]) -> None:
    st.divider()
    status = str(card.get("compliance_status") or "needs_manual_review")
    status_label = COMPLIANCE_LABELS.get(status, "нужна ручная проверка")
    markers = _json_list(card.get("compliance_markers"))
    if status == "ready":
        st.success(f"Статус карточки: {status_label}")
    else:
        marker_text = ", ".join(markers) if markers else "маркер не указан"
        st.warning(f"Статус карточки: {status_label}. Причина: {marker_text}.")

    if status == "ready":
        st.subheader("Готовая карточка Kwork")
    else:
        st.subheader("Черновик для ручной проверки")
    _render_copy_block("Название услуги", str(card.get("title") or ""), "title")
    _render_copy_block("Рубрика Kwork", str(card.get("kwork_category") or ""), "category")
    _render_copy_block("Промпт для картинки", str(card.get("image_prompt") or ""), "image")
    _render_copy_block("Описание", str(card.get("description") or ""), "description")
    _render_copy_block("Что нужно от покупателя", str(card.get("buyer_requirements") or ""), "requirements")
    _render_copy_block("Базовая стоимость", str(card.get("base_price") or ""), "price")
    _render_copy_block("Дополнительные опции", "\n".join(_json_list(card.get("extra_options"))), "options")
    _render_copy_block("FAQ", _format_faq(_json_list(card.get("faq"))), "faq")
    _render_copy_block("Риски и предупреждения", str(card.get("risk_warnings") or ""), "risks")
    _render_copy_block("Исходный FunPay-лот", str(card.get("source_funpay_url") or ""), "source")


def _render_copy_block(title: str, value: str, key: str) -> None:
    st.markdown(f"**{title}**")
    st.text_area(title, value=value, height=120, label_visibility="collapsed", key=f"card_{key}")
    _copy_button(value, f"copy_{key}")


def _copy_button(value: str, key: str) -> None:
    escaped_value = html.escape(value)
    js_value = json.dumps(value, ensure_ascii=False)
    components.html(
        f"""
        <button id="{key}" style="padding: 0.35rem 0.75rem; border: 1px solid #d0d7de; border-radius: 6px; background: #ffffff; cursor: pointer;">
            Скопировать
        </button>
        <span id="{key}_status" style="margin-left: 0.5rem; color: #2e7d32;"></span>
        <textarea id="{key}_fallback" style="position:absolute; left:-9999px;">{escaped_value}</textarea>
        <script>
        const button = document.getElementById("{key}");
        const status = document.getElementById("{key}_status");
        button.addEventListener("click", async () => {{
            const text = {js_value};
            try {{
                await navigator.clipboard.writeText(text);
                status.textContent = "Скопировано";
            }} catch (error) {{
                const fallback = document.getElementById("{key}_fallback");
                fallback.select();
                document.execCommand("copy");
                status.textContent = "Скопировано";
            }}
            setTimeout(() => status.textContent = "", 1800);
        }});
        </script>
        """,
        height=45,
    )


def _render_logs_and_errors() -> None:
    st.subheader("История запусков")
    run_rows = _load_run_history()
    if run_rows.empty:
        st.info("История запусков пока пуста.")
    else:
        run_view = _select_columns(
            run_rows.head(50),
            ["command", "status", "processed_count", "error", "started_at", "finished_at"],
        ).rename(
            columns={
                "command": "Команда",
                "status": "Статус",
                "processed_count": "Обработано",
                "error": "Ошибка",
                "started_at": "Начало",
                "finished_at": "Завершение",
            }
        )
        _render_dataframe(run_view, height=260)

    st.subheader("Ошибки парсинга")
    error_rows = _load_parse_errors()
    if error_rows.empty:
        st.success("Ошибок парсинга пока нет.")
    else:
        error_view = _select_columns(error_rows.head(100), ["source", "url", "status", "error", "created_at"]).rename(
            columns={
                "source": "Источник",
                "url": "Ссылка",
                "status": "Статус",
                "error": "Ошибка",
                "created_at": "Добавлено",
            }
        )
        _render_dataframe(error_view, height=320)

    st.subheader("Последние debug-файлы Kwork")
    debug_files = _latest_kwork_debug_files()
    if not debug_files:
        st.info("Debug-файлы Kwork пока не найдены.")
    else:
        for path in debug_files:
            st.code(str(path))


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


def _sorted_options(rows: pd.DataFrame, column: str) -> list[str]:
    if column not in rows.columns:
        return []
    return sorted(str(value) for value in rows[column].dropna().unique() if str(value).strip())


def _number_range(series: pd.Series, fallback: tuple[float, float]) -> tuple[float, float]:
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if numeric.empty:
        return fallback
    minimum = float(numeric.min())
    maximum = float(numeric.max())
    if minimum == maximum:
        maximum = minimum + 1.0
    return round(minimum, 2), round(maximum, 2)


def _opportunity_label(row: pd.Series) -> str:
    title = row.get("possible_kwork_service_title") or row.get("safe_wording") or row.get("source_subcategory") or "без названия"
    score = row.get("opportunity_score")
    risk = row.get("risk_level") or "?"
    return f"#{int(row['id'])} | {title} | риск {risk} | балл {score}"


def _json_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return [str(value)]
    return parsed if isinstance(parsed, list) else [parsed]


def _format_faq(items: list) -> str:
    lines: list[str] = []
    for item in items:
        if isinstance(item, dict):
            question = str(item.get("question") or "").strip()
            answer = str(item.get("answer") or "").strip()
            if question or answer:
                lines.append(f"Вопрос: {question}\nОтвет: {answer}")
        else:
            lines.append(str(item))
    return "\n\n".join(lines)


def _latest_kwork_debug_files(limit: int = 20) -> list[Path]:
    debug_dir = Path("data/debug/kwork")
    if not debug_dir.exists():
        return []
    files = [path for path in debug_dir.iterdir() if path.is_file()]
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return files[:limit]


def _render_dataframe(view: pd.DataFrame, height: int = 650) -> None:
    column_config = {}
    for column in ("Ссылка", "Источник FunPay"):
        if column in view.columns:
            column_config[column] = st.column_config.LinkColumn(column)

    st.dataframe(
        view,
        hide_index=True,
        height=height,
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


@st.cache_data(ttl=30)
def _load_run_history() -> pd.DataFrame:
    rows = fetch_run_history()
    return pd.DataFrame([dict(row) for row in rows])


@st.cache_data(ttl=30)
def _load_parse_errors() -> pd.DataFrame:
    rows = fetch_parse_errors()
    return pd.DataFrame([dict(row) for row in rows])


def _select_columns(dataframe: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    existing = [column for column in columns if column in dataframe.columns]
    view = dataframe.loc[:, existing].copy()
    object_columns = view.select_dtypes(include="object").columns
    view.loc[:, object_columns] = view.loc[:, object_columns].fillna("")
    return view


if __name__ == "__main__":
    main()
