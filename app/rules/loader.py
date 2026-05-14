"""Кэшированная загрузка YAML-правил анализа."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.config import PROJECT_ROOT

RULES_DIR = PROJECT_ROOT / "app" / "rules"


class RuleLoaderError(RuntimeError):
    """Понятная ошибка загрузки правил анализа."""


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuleLoaderError(f"Файл правил не найден: {path}")

    try:
        with path.open("r", encoding="utf-8") as file:
            payload = yaml.safe_load(file) or {}
    except yaml.YAMLError as error:
        raise RuleLoaderError(f"Не удалось прочитать YAML-правила {path}: {error}") from error

    if not isinstance(payload, dict):
        raise RuleLoaderError(f"Файл правил должен содержать YAML-словарь: {path}")

    return payload


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("w", encoding="utf-8") as file:
            yaml.safe_dump(payload, file, allow_unicode=True, sort_keys=False)
    except yaml.YAMLError as error:
        raise RuleLoaderError(f"Не удалось записать YAML-правила {path}: {error}") from error


@lru_cache(maxsize=1)
def load_funpay_types() -> dict[str, Any]:
    return _read_yaml(RULES_DIR / "funpay_types.yaml")


@lru_cache(maxsize=1)
def load_kwork_prohibited() -> dict[str, Any]:
    return _read_yaml(RULES_DIR / "kwork_prohibited.yaml")


@lru_cache(maxsize=1)
def load_mapping_rules() -> dict[str, Any]:
    return _read_yaml(RULES_DIR / "mapping_rules.yaml")


@lru_cache(maxsize=1)
def load_scoring_rules() -> dict[str, Any]:
    return _read_yaml(RULES_DIR / "scoring_rules.yaml")


@lru_cache(maxsize=1)
def load_funpay_stop_categories() -> dict[str, Any]:
    return _read_yaml(RULES_DIR / "funpay_stop_categories.yaml")


def save_funpay_stop_categories(payload: dict[str, Any]) -> None:
    _write_yaml(RULES_DIR / "funpay_stop_categories.yaml", payload)
    load_funpay_stop_categories.cache_clear()


def normalize_rule_text(value: Any) -> str:
    """Возвращает текст правила даже если YAML прочитал строку с двоеточием как словарь."""

    if isinstance(value, dict) and len(value) == 1:
        key, nested_value = next(iter(value.items()))
        return f"{key}: {nested_value}".strip()
    return str(value).strip()


def normalize_rule_text_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [text for item in values if (text := normalize_rule_text(item))]


@lru_cache(maxsize=1)
def load_all_rules() -> dict[str, dict[str, Any]]:
    return {
        "funpay_types": load_funpay_types(),
        "kwork_prohibited": load_kwork_prohibited(),
        "mapping_rules": load_mapping_rules(),
        "scoring_rules": load_scoring_rules(),
        "funpay_stop_categories": load_funpay_stop_categories(),
    }
