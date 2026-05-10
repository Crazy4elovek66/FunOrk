"""Расчет скоринга и итоговой бизнес-модели Opportunity."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from app.analyzers.margin_calculator import (
    calculate_margin_percent,
    calculate_sell_price,
)
from app.models import Opportunity, RiskLevel, ScrapedItem
from app.rules.loader import load_scoring_rules


def score_opportunity(
    item: ScrapedItem,
    risk_level: RiskLevel,
    risk_reason: str,
    mapping_data: dict[str, Any] | None,
) -> Opportunity:
    """Создает Opportunity с маржой, спросом, рисками и русским выводом."""

    scoring_rules = load_scoring_rules()
    mapping = mapping_data or {}

    sell_price = _select_sell_price(item.price, mapping.get("average_price"))
    margin_percent = calculate_margin_percent(item.price, sell_price)
    margin_score = _score_margin(margin_percent, scoring_rules)
    risk_score = _score_risk(
        risk_level,
        scoring_rules,
        competitors_count=mapping.get("competitors_count"),
        requires_login_password=item.requires_login_password,
    )
    demand_score = _score_demand(mapping.get("competitors_count"), scoring_rules)
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
        forbidden_words=list(mapping.get("forbidden_words") or []),
        safe_wording=mapping.get("safe_wording"),
        buyer_requirements=mapping.get("buyer_requirements"),
        forbidden_buyer_requests=_forbidden_buyer_requests_text(),
        report_format=mapping.get("report_format"),
    )


def _score_margin(margin_percent: float | None, rules: dict[str, Any]) -> float:
    margin_rules = rules.get("margin_scores", {})
    if margin_percent is None:
        return float(margin_rules.get("unknown", {}).get("score", 30))

    if margin_percent < margin_rules.get("low_margin", {}).get("percent_max", 20):
        return _midpoint(margin_rules.get("low_margin", {}), 20)
    if margin_percent < margin_rules.get("medium_margin", {}).get("percent_max", 50):
        return _midpoint(margin_rules.get("medium_margin", {}), 55)
    return _midpoint(margin_rules.get("high_margin", {}), 85)


def _select_sell_price(
    buy_price: Decimal | None,
    kwork_average_price: object | None,
) -> Decimal | None:
    parsed_average = _parse_decimal(kwork_average_price)
    if parsed_average is not None and parsed_average > 0:
        return parsed_average
    return calculate_sell_price(buy_price)


def _parse_decimal(value: object | None) -> Decimal | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    raw = raw.replace("\u00a0", " ").replace(" ", "").replace(",", ".")
    raw = re.sub(r"[^0-9.\-]", "", raw)
    if not raw:
        return None
    try:
        parsed = Decimal(raw)
    except InvalidOperation:
        return None
    return parsed if parsed >= 0 else None


def _score_risk(
    risk_level: RiskLevel,
    rules: dict[str, Any],
    *,
    competitors_count: object | None = None,
    requires_login_password: bool = False,
) -> float:
    risk_rules = rules.get("risk_scores", {})
    if risk_level == "GREEN":
        base_score = _range_midpoint(
            risk_rules.get("green_min", 80),
            risk_rules.get("green_max", 100),
        )
    elif risk_level == "YELLOW":
        base_score = _range_midpoint(
            risk_rules.get("yellow_min", 30),
            risk_rules.get("yellow_max", 70),
        )
    else:
        return float(risk_rules.get("red", 0))

    competitors = _safe_int(competitors_count)
    if competitors is not None and competitors >= 50:
        base_score -= 10
    if requires_login_password:
        base_score += float(rules.get("adjustments", {}).get("requires_login_password", -40))
    return max(0, min(100, base_score))


def _score_demand(competitors_count: object | None, rules: dict[str, Any]) -> float:
    demand_rules = rules.get("demand_scores", {})
    competitors = _safe_int(competitors_count)
    if competitors is None:
        return float(demand_rules.get("unknown", {}).get("score", 30))

    high = demand_rules.get("high", {})
    medium = demand_rules.get("medium", {})
    low = demand_rules.get("low", {})

    if competitors >= int(high.get("competitors_min", 20)):
        return _midpoint(high, 85)
    if competitors >= int(medium.get("competitors_min", 5)):
        return _midpoint(medium, 55)
    return _midpoint(low, 25)


def _safe_int(value: object | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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


def _forbidden_buyer_requests_text() -> str:
    return (
        "Нельзя просить логин, пароль, коды восстановления, секретные ключи, "
        "доступ к аккаунту или платежные данные покупателя."
    )
