"""Генерация безопасной черновой Kwork-карточки из Opportunity."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from app.models import AdaptedKworkCard


PROHIBITED_MARKERS = [
    "накрутка",
    "обход",
    "взлом",
    "логин",
    "пароль",
    "аккаунт",
    "гарантия",
    "100%",
    "без риска",
    "казино",
    "ставки",
    "крипта",
    "vpn",
    "запрещенные соцсети",
    "запрещённые соцсети",
    "продажа аккаунтов",
    "регистрация аккаунтов",
]

RISKY_PATTERNS = [
    ("продажа/регистрация аккаунтов", re.compile(r"\b(продаж\w*|регистрац\w*)\s+\w*\s*аккаунт", re.I)),
]


def generate_adapted_kwork_card(opportunity: dict[str, Any]) -> AdaptedKworkCard:
    opportunity_id = _required_int(opportunity.get("id"), "id")
    service_title = _clean(
        opportunity.get("possible_kwork_service_title")
        or opportunity.get("safe_wording")
        or opportunity.get("source_subcategory")
        or opportunity.get("source_category")
        or "Практичная цифровая услуга"
    )
    kwork_category = _clean(opportunity.get("possible_kwork_category") or "Другое")
    source_url = _clean(opportunity.get("source_url"))
    if not source_url:
        raise ValueError("У opportunity нет ссылки на исходный FunPay-лот")

    title = _build_title(service_title)
    description = _build_description(service_title, opportunity)
    buyer_requirements = _build_buyer_requirements(opportunity)
    base_price = _decimal_or_none(opportunity.get("sell_price") or opportunity.get("buy_price"))
    extra_options = _build_extra_options(base_price)
    faq = _build_faq()
    risk_warnings = _build_risk_warnings(opportunity)
    image_prompt = _build_image_prompt(service_title, kwork_category)

    markers = check_compliance(
        [
            title,
            kwork_category,
            image_prompt,
            description,
            buyer_requirements,
            "\n".join(extra_options),
            "\n".join(f"{item.get('question', '')} {item.get('answer', '')}" for item in faq),
            risk_warnings,
            source_url,
        ]
    )
    risk_level = str(opportunity.get("risk_level") or "").upper()
    compliance_status = "ready"
    if markers or risk_level == "RED":
        compliance_status = "needs_manual_review"
    if risk_level == "RED":
        markers = sorted(set(markers + ["красный риск исходного лота"]))

    return AdaptedKworkCard(
        opportunity_id=opportunity_id,
        title=title,
        kwork_category=kwork_category,
        image_prompt=image_prompt,
        description=description,
        buyer_requirements=buyer_requirements,
        base_price=base_price,
        extra_options=extra_options,
        faq=faq,
        risk_warnings=risk_warnings,
        source_funpay_url=source_url,
        compliance_status=compliance_status,
        compliance_markers=markers,
    )


def check_compliance(text_blocks: list[str]) -> list[str]:
    text = "\n".join(block for block in text_blocks if block).casefold()
    found: set[str] = set()
    for marker in PROHIBITED_MARKERS:
        if marker.casefold() in text:
            found.add(marker)
    for label, pattern in RISKY_PATTERNS:
        if pattern.search(text):
            found.add(label)
    return sorted(found)


def _build_title(service_title: str) -> str:
    title = re.sub(r"\s+", " ", service_title).strip(" .")
    if len(title) > 75:
        title = title[:72].rstrip() + "..."
    return title[0].upper() + title[1:] if title else "Практичная цифровая услуга"


def _build_description(service_title: str, opportunity: dict[str, Any]) -> str:
    safe_wording = _clean(opportunity.get("safe_wording"))
    recommendation = _clean(opportunity.get("recommendation"))
    result_line = safe_wording or service_title
    parts = [
        f"Подготовлю результат по задаче: {result_line}.",
        "Работа ведется по открытому описанию задачи и материалам, которые покупатель вправе передать исполнителю.",
        "В базовый объем входит уточнение задачи, выполнение согласованной работы и передача результата в понятном формате.",
    ]
    if recommendation:
        parts.append(f"Формат запуска: {recommendation}.")
    parts.append(
        "Не принимаю задачи, где нужны доступы, личные данные, нарушение правил площадок или действия с чужими профилями."
    )
    return "\n\n".join(parts)


def _build_buyer_requirements(opportunity: dict[str, Any]) -> str:
    requirements = _clean(opportunity.get("buyer_requirements"))
    base = requirements or "Кратко опишите задачу, желаемый результат, сроки и приложите исходные материалы, если они есть."
    return (
        f"{base}\n\n"
        "Не отправляйте секретные данные, коды восстановления, платежные реквизиты и личные доступы. "
        "Если для выполнения нужны дополнительные материалы, я уточню это до начала работы."
    )


def _build_extra_options(base_price: Decimal | None) -> list[str]:
    if base_price is None:
        return [
            "Ускоренное выполнение после согласования объема",
            "Расширенный итоговый отчет по выполненной работе",
            "Дополнительная правка в рамках исходного задания",
        ]
    quick_price = max(300, int(base_price * Decimal("0.3")))
    report_price = max(500, int(base_price * Decimal("0.5")))
    return [
        f"Ускоренное выполнение после согласования объема: +{quick_price} руб.",
        f"Расширенный итоговый отчет по выполненной работе: +{report_price} руб.",
        "Дополнительная правка в рамках исходного задания: +300 руб.",
    ]


def _build_faq() -> list[dict[str, str]]:
    return [
        {
            "question": "Можно ли обсудить детали до заказа?",
            "answer": "Да, сначала уточним задачу, объем и формат результата.",
        },
        {
            "question": "Что входит в базовую стоимость?",
            "answer": "В базовый объем входит выполнение согласованной задачи и передача результата в рабочем формате.",
        },
        {
            "question": "Какие задачи не принимаются?",
            "answer": "Не беру задачи с нарушением правил площадок, передачей личных данных или действиями с чужими профилями.",
        },
    ]


def _build_risk_warnings(opportunity: dict[str, Any]) -> str:
    lines = [
        f"Риск исходного направления: {_clean(opportunity.get('risk_level')) or 'не указан'}.",
        _clean(opportunity.get("risk_reason")) or "Причина риска не указана.",
    ]
    moderation = _clean(opportunity.get("moderation_risk"))
    dispute = _clean(opportunity.get("dispute_risk"))
    if moderation:
        lines.append(f"Модерация: {moderation}.")
    if dispute:
        lines.append(f"Споры: {dispute}.")
    lines.append("Перед публикацией проверьте карточку вручную и не добавляйте обещания результата.")
    return "\n".join(lines)


def _build_image_prompt(service_title: str, kwork_category: str) -> str:
    return (
        "Чистая современная обложка для Kwork: рабочий стол, аккуратный интерфейс, "
        f"визуальная метафора услуги «{service_title}», категория «{kwork_category}», "
        "без логотипов площадок, без скриншотов личных данных, без обещаний результата."
    )


def _required_int(value: object, field_name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"У opportunity нет корректного поля {field_name}") from error
    return result


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    raw = str(value).replace(" ", "").replace(",", ".")
    raw = re.sub(r"[^0-9.\-]", "", raw)
    if not raw:
        return None
    try:
        result = Decimal(raw)
    except Exception:
        return None
    return result if result >= 0 else None


def _clean(value: object | None) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()
