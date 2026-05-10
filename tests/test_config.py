from app.config import AppConfig, PROJECT_ROOT


def _base_config_payload() -> dict[str, object]:
    return {
        "REQUEST_DELAY_MIN": 0,
        "REQUEST_DELAY_MAX": 0,
        "MAX_REQUESTS_PER_MINUTE": 60,
        "USER_AGENT": "pytest",
        "CACHE_TTL_HOURS": 1,
        "DATABASE_PATH": "data/processed/test.sqlite",
        "REPORTS_DIR": "data/reports",
        "ENABLE_FUNPAY_COLLECTION": True,
        "ENABLE_KWORK_COLLECTION": True,
        "DRY_RUN": True,
        "MAX_PAGES_PER_RUN": 1,
        "LOG_LEVEL": "INFO",
        "KWORK_FEE_PERCENT": 20,
        "WITHDRAWAL_FEE_PERCENT": 0,
        "DEFAULT_TARGET_MARGIN_PERCENT": 40,
        "FUNPAY_BASE_URL": "https://funpay.com",
        "KWORK_BASE_URL": "https://kwork.ru",
        "KWORK_SITEMAP_URL": "https://kwork.ru/sitemap.xml",
        "CACHE_DIR": "data/cache",
    }


def test_kwork_cookie_defaults_to_environment(monkeypatch):
    monkeypatch.setenv("KWORK_COOKIE", "sessionid=from-env")

    loaded = AppConfig.model_validate(_base_config_payload())

    assert loaded.KWORK_COOKIE == "sessionid=from-env"
    assert loaded.DATABASE_PATH == PROJECT_ROOT / "data/processed/test.sqlite"
