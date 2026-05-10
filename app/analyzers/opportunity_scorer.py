"""Расчет маржи и итогового скоринга возможностей."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from app.config import config
from app.models import Opportunity, RiskLevel, ScrapedItem
from app.rules.loader import load_scoring_rules


def score_opportunity(
    item: ScrapedItem,
    risk_level: RiskLevel,
    risk_reason: str,
    mapping_data: dict[str, Any] | None,
) -> Opportunity:
    """Создает бизнес-модель Opportunity с учетом комиссий Kwork."""

    scoring_rules = load_scoring_rules()
    mapping = mapping_data or {}

    sell_price = _calculate_sell_price(item.price)
    margin_percent = _calculate_margin_percent(item.price, sell_price)
    margin_score = _score_margin(margin_percent, scoring_rules)
    risk_score = _score_risk(risk_level, scoring_rules)
    demand_score = float(
        scoring_rules.get("demand_scores", {}).get("unknown", {}).get("score", 30)
    )
    opportunity_score = _weighted_score(
        margin_score=margin_score,
        risk_score=risk_score,
        demand_score=demand_score,
        scoring_rules=scoring_rules,
    )

    if risk_level == "RED":
        opportunity_score = 0

    verdict = mapping.get("verdict") or _select_verdict(opportunity_score, scoring_rules)

    return Opportunity(
        funpay_category_id=item.id,
        source_category=item.category or "FunPay",
        source_subcategory=item.subcategory,
        source_url=item.url,
        normalized_type=str(mapping.get("normalized_type") or "other"),
        possible_kwork_service_title=mapping.get("service_title"),
        possible_kwork_category=mapping.get("kwork_category"),
        buy_price=item.price,
        sell_price=sell_price,
        estimated_margin_percent=margin_percent,
        risk_level=risk_level,
        risk_reason=risk_reason,
        moderation_risk=_moderation_risk_text(risk_level),
        dispute_risk=_dispute_risk_text(risk_level),
        demand_weight=demand_score,
        margin_weight=margin_score,
        risk_weight=risk_score,
        opportunity_score=round(opportunity_score, 2),
        verdict=verdict,
        recommendation=_recommendation_text(verdict),
        buyer_requirements=mapping.get("buyer_requirements"),
        report_format=mapping.get("report_format"),
    )


def _calculate_sell_price(buy_price: Decimal | None) -> Decimal | None:
    if buy_price is None:
        return None

    target_margin = Decimal(str(config.DEFAULT_TARGET_MARGIN_PERCENT)) / Decimal("100")
    total_fee = (
        Decimal(str(config.KWORK_FEE_PERCENT + config.WITHDRAWAL_FEE_PERCENT))
        / Decimal("100")
    )
    net_multiplier = Decimal("1") - total_fee
    if net_multiplier <= 0:
        raise ValueError("Суммарная комиссия не может быть 100% или выше")

    sell_price = (buy_price * (Decimal("1") + target_margin)) / net_multiplier
    return sell_price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _calculate_margin_percent(
    buy_price: Decimal | None,
    sell_price: Decimal | None,
) -> float | None:
    if buy_price is None or sell_price is None or buy_price == 0:
        return None

    total_fee = Decimal(str(config.KWORK_FEE_PERCENT + config.WITHDRAWAL_FEE_PERCENT))
    net_revenue = sell_price * (Decimal("1") - total_fee / Decimal("100"))
    margin_percent = ((net_revenue - buy_price) / buy_price) * Decimal("100")
    return float(margin_percent.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _score_margin(margin_percent: float | None, rules: dict[str, Any]) -> float:
    margin_rules = rules.get("margin_scores", {})
    if margin_percent is None:
        return float(margin_rules.get("unknown", {}).get("score", 30))

    if margin_percent < margin_rules.get("low_margin", {}).get("percent_max", 20):
        return _midpoint(margin_rules.get("low_margin", {}), 20)
    if margin_percent < margin_rules.get("medium_margin", {}).get("percent_max", 50):
        return _midpoint(margin_rules.get("medium_margin", {}), 55)
    return _midpoint(margin_rules.get("high_margin", {}), 85)


def _score_risk(risk_level: RiskLevel, rules: dict[str, Any]) -> float:
    risk_rules = rules.get("risk_scores", {})
    if risk_level == "GREEN":
        return _range_midpoint(
            risk_rules.get("green_min", 80),
            risk_rules.get("green_max", 100),
        )
    if risk_level == "YELLOW":
        return _range_midpoint(
            risk_rules.get("yellow_min", 30),
            risk_rules.get("yellow_max", 70),
        )
    return float(risk_rules.get("red", 0))


def _weighted_score(
    margin_score: float,
    risk_score: float,
    demand_score: float,
    scoring_rules: dict[str, Any],
) -> float:
    weights = scoring_rules.get("weights", {})
    return (
        margin_score * float(weights.get("margin_weight", 0.4))
        + risk_score * float(weights.get("risk_weight", 0.4))
        + demand_score * float(weights.get("demand_weight", 0.2))
    )


def _select_verdict(score: float, rules: dict[str, Any]) -> str:
    verdicts = rules.get("verdicts", {})
    if score == 0:
        return str(verdicts.get("blocked", {}).get("title", "не брать"))

    for key in ("test", "careful", "manual"):
        rule = verdicts.get(key, {})
        min_score = rule.get("min_score")
        max_score = rule.get("max_score")
        if min_score is None:
            continue
        if score >= min_score and (max_score is None or score <= max_score):
            return str(rule.get("title"))

    return "только ручная проверка"


def _midpoint(rule: dict[str, Any], fallback: float) -> float:
    if "score_min" not in rule or "score_max" not in rule:
        return fallback
    return _range_midpoint(rule["score_min"], rule["score_max"])


def _range_midpoint(left: object, right: object) -> float:
    return (float(left) + float(right)) / 2


def _moderation_risk_text(risk_level: RiskLevel) -> str:
    if risk_level == "RED":
        return "Высокий риск блокировки на модерации Kwork"
    if risk_level == "YELLOW":
        return "Нужна ручная проверка формулировки услуги"
    return "Низкий риск при корректной формулировке"


def _dispute_risk_text(risk_level: RiskLevel) -> str:
    if risk_level == "RED":
        return "Высокий риск спора с клиентом"
    if risk_level == "YELLOW":
        return "Средний риск, нужны четкие требования от покупателя"
    return "Низкий риск при понятном результате работы"


def _recommendation_text(verdict: str) -> str:
    if verdict == "можно брать в тест":
        return "Запустить малым объемом и проверить спрос"
    if verdict == "тестировать осторожно":
        return "Проверить правила Kwork и начинать только с безопасной формулировки"
    if verdict == "не брать":
        return "Исключить из работы"
    return "Перед запуском провести ручную проверку"
