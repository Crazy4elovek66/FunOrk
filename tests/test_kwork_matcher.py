from decimal import Decimal

from app.analyzers.kwork_matcher import (
    build_kwork_index,
    calculate_demand_from_matches,
    match_funpay_to_kwork,
)
from app.models import ScrapedItem


def test_gemini_funpay_matches_kwork_help():
    funpay = ScrapedItem(source="funpay", url="https://funpay.com/lots/1/", title="Gemini подписка")
    kwork = ScrapedItem(source="kwork", url="https://kwork.ru/kwork/1", title="Помощь с Gemini", price=Decimal("1500"))

    matches = match_funpay_to_kwork(funpay, build_kwork_index([kwork]))

    assert len(matches) == 1
    assert calculate_demand_from_matches(matches)["demand_weight"] >= 35


def test_runway_ai_video_matches_kwork_ai_video():
    funpay = ScrapedItem(source="funpay", url="https://funpay.com/lots/2/", title="Runway AI video")
    kwork = ScrapedItem(source="kwork", url="https://kwork.ru/kwork/2", title="AI-видео для рекламы")

    matches = match_funpay_to_kwork(funpay, build_kwork_index([kwork]))

    assert len(matches) == 1


def test_unrelated_services_have_low_demand():
    funpay = ScrapedItem(source="funpay", url="https://funpay.com/lots/3/", title="Gemini подписка")
    kwork = ScrapedItem(source="kwork", url="https://kwork.ru/kwork/3", title="Дизайн визитки")

    matches = match_funpay_to_kwork(funpay, build_kwork_index([kwork]))

    assert matches == []
    assert calculate_demand_from_matches(matches)["demand_weight"] == 10
