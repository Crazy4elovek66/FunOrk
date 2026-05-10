"""Загрузка и валидация конфигурации проекта."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"

load_dotenv()


class ConfigError(RuntimeError):
    """Понятная ошибка загрузки конфигурации."""


class ReportSettings(BaseModel):
    csv_delimiter: str = ";"
    encoding: str = "utf-8-sig"
    default_format: str = "xlsx"


class ScoringSettings(BaseModel):
    margin_weight: float = Field(0.4, ge=0)
    risk_weight: float = Field(0.4, ge=0)
    demand_weight: float = Field(0.2, ge=0)

    @model_validator(mode="after")
    def validate_total_weight(self) -> "ScoringSettings":
        total = self.margin_weight + self.risk_weight + self.demand_weight
        if abs(total - 1.0) > 0.001:
            raise ValueError("Сумма весов скоринга должна быть равна 1.0")
        return self


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    REQUEST_DELAY_MIN: float = Field(..., ge=0)
    REQUEST_DELAY_MAX: float = Field(..., ge=0)
    MAX_REQUESTS_PER_MINUTE: int = Field(..., gt=0)
    USER_AGENT: str = Field(..., min_length=1)
    CACHE_TTL_HOURS: int = Field(..., ge=0)
    DATABASE_PATH: Path
    REPORTS_DIR: Path
    ENABLE_FUNPAY_COLLECTION: bool
    ENABLE_KWORK_COLLECTION: bool
    DRY_RUN: bool
    MAX_PAGES_PER_RUN: int = Field(..., gt=0)
    LOG_LEVEL: str = Field(..., min_length=1)
    KWORK_FEE_PERCENT: float = Field(..., ge=0, le=100)
    WITHDRAWAL_FEE_PERCENT: float = Field(..., ge=0, le=100)
    DEFAULT_TARGET_MARGIN_PERCENT: float = Field(..., ge=0)

    FUNPAY_BASE_URL: str = Field(..., min_length=1)
    KWORK_BASE_URL: str = Field(..., min_length=1)
    KWORK_SITEMAP_URL: str = Field(..., min_length=1)
    KWORK_COOKIE: str | None = Field(default_factory=lambda: os.getenv("KWORK_COOKIE"))
    CACHE_DIR: Path

    REPORT_SETTINGS: ReportSettings = Field(default_factory=ReportSettings)
    SCORING: ScoringSettings = Field(default_factory=ScoringSettings)

    @field_validator("DATABASE_PATH", "REPORTS_DIR", "CACHE_DIR", mode="before")
    @classmethod
    def make_path_absolute(cls, value: Any) -> Path:
        if value is None or str(value).strip() == "":
            raise ValueError("Путь в конфигурации не может быть пустым")

        path = Path(value)
        if path.is_absolute():
            return path
        return PROJECT_ROOT / path

    @model_validator(mode="after")
    def validate_request_delay(self) -> "AppConfig":
        if self.REQUEST_DELAY_MIN > self.REQUEST_DELAY_MAX:
            raise ValueError(
                "REQUEST_DELAY_MIN не может быть больше REQUEST_DELAY_MAX"
            )
        return self

    @property
    def DATA_DIR(self) -> Path:
        return PROJECT_ROOT / "data"

    @property
    def RAW_DIR(self) -> Path:
        return self.DATA_DIR / "raw"

    @property
    def PROCESSED_DIR(self) -> Path:
        return self.DATABASE_PATH.parent


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Файл конфигурации не найден: {path}")

    try:
        with path.open("r", encoding="utf-8") as file:
            payload = yaml.safe_load(file) or {}
    except yaml.YAMLError as error:
        raise ConfigError(f"Не удалось прочитать YAML-конфигурацию: {error}") from error

    if not isinstance(payload, dict):
        raise ConfigError("Файл config.yaml должен содержать YAML-словарь")

    return payload


def ensure_directories(app_config: AppConfig) -> None:
    """Создает рабочие директории, если они отсутствуют."""

    directories = (
        app_config.DATA_DIR,
        app_config.RAW_DIR,
        app_config.CACHE_DIR,
        app_config.PROCESSED_DIR,
        app_config.REPORTS_DIR,
    )
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)


def load_config(path: Path | str | None = None, create_dirs: bool = True) -> AppConfig:
    """Загружает config.yaml и возвращает провалидированный объект настроек."""

    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path

    try:
        app_config = AppConfig.model_validate(_read_yaml(config_path))
    except ValueError as error:
        raise ConfigError(f"Ошибка в config.yaml: {error}") from error

    if create_dirs:
        ensure_directories(app_config)

    return app_config


config = load_config()
