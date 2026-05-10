from decimal import Decimal

from app.analyzers.kwork_mapper import map_to_kwork
from app.analyzers.opportunity_scorer import score_opportunity
from app.analyzers.risk_classifier import analyze_risk
from app.models import ScrapedItem


def test_risk_classifier_red():
    item = ScrapedItem(
        source="funpay",
        url="https://funpay.com/lots/1/",
        title="Купить аккаунт для игры",
    )

    risk_level, _ = analyze_risk(item)

    assert risk_level == "RED"


def test_opportunity_scorer_math():
    item = ScrapedItem(
        source="funpay",
        url="https://funpay.com/lots/2/",
        title="Настройка игрового профиля",
        price=Decimal("100"),
        currency="RUB",
    )

    opportunity = score_opportunity(
        item=item,
        risk_level="GREEN",
        risk_reason="Тестовый безопасный сценарий",
        mapping_data=None,
    )

    assert opportunity.sell_price == Decimal("175.00")
    assert opportunity.estimated_margin_percent == 40.0


def test_kwork_mapper_skips_red():
    item = ScrapedItem(
        source="funpay",
        url="https://funpay.com/lots/3/",
        title="Любой красный лот",
    )

    mapping = map_to_kwork(item, "RED")

    assert mapping is None
