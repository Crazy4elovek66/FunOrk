"""Расчет цены продажи и маржи с учетом комиссий."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from app.config import config


def calculate_sell_price(buy_price: Decimal | None) -> Decimal | None:
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


def calculate_margin_percent(
    buy_price: Decimal | None,
    sell_price: Decimal | None,
) -> float | None:
    if buy_price is None or sell_price is None or buy_price == 0:
        return None

    total_fee = Decimal(str(config.KWORK_FEE_PERCENT + config.WITHDRAWAL_FEE_PERCENT))
    net_revenue = sell_price * (Decimal("1") - total_fee / Decimal("100"))
    margin_percent = ((net_revenue - buy_price) / buy_price) * Decimal("100")
    return float(margin_percent.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
