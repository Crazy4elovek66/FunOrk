"""CSS-селекторы публичных страниц FunPay.

Collectors обязаны обращаться к селекторам по имени. Если обязательный
селектор не найден на странице, нужно сохранить parse_failed с понятной ошибкой.
"""

BASE_URL = "https://funpay.com"

BLOCKED_URL_PARTS = (
    "/account",
    "/chat",
    "/users",
    "/offer",
    "/trade",
    "/orders",
    "/login",
)

CATALOG_SELECTORS = {
    "category_section": ".promo-game-list, .game-list, .catalog",
    "category_card": "a[href]",
    "category_title": ".game-title, .media-user-name, .inside, span, div",
    "category_offer_count": ".badge, .counter, .tc-muted",
    "subcategory_link": "a[href]",
}

LOTS_SELECTORS = {
    "lot_row": ".tc-item, .offer, .table-row, a[href*='lots']",
    "lot_title": ".tc-desc-text, .offer-title, .title",
    "lot_price": ".tc-price, .price, [class*='price']",
    "lot_currency": ".unit, .currency",
    "lot_delivery": ".tc-server, .delivery, .desc",
}

REQUIRED_SELECTORS = {
    "catalog": ("category_card",),
    "lots": ("lot_row", "lot_price"),
}

PRICE_PATTERNS = (
    r"(?P<price>\d+(?:[\s,.]\d+)?)\s*(?P<currency>₽|руб\.?|RUB|$|USD|€|EUR)?",
)
