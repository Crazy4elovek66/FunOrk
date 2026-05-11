from decimal import Decimal

from app.analyzers.opportunity_scorer import score_opportunity
from app.models import ScrapedItem


def test_demand_score_argument_changes_score():
    item = ScrapedItem(
        source="funpay",
        url="https://funpay.com/lots/1/",
        title="Gemini подписка",
        price=Decimal("100"),
    )

    low = score_opportunity(item, "GREEN", "Тест", None, demand_score=10)
    high = score_opportunity(item, "GREEN", "Тест", None, demand_score=90)

    assert high.demand_weight == 90
    assert high.opportunity_score > low.opportunity_score


def test_red_opportunity_always_zero():
    item = ScrapedItem(
        source="funpay",
        url="https://funpay.com/lots/2/",
        title="Продажа аккаунта",
        price=Decimal("100"),
    )

    opportunity = score_opportunity(item, "RED", "Запрещенное направление", None, demand_score=100)

    assert opportunity.opportunity_score == 0
