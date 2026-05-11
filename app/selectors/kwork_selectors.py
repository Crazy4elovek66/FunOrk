"""CSS-селекторы публичных страниц Kwork.

Collectors используют эти селекторы как конфигурацию и не хардкодят CSS в
бизнес-логике.
"""

BASE_URL = "https://kwork.ru"
SITEMAP_URL = "https://kwork.ru/sitemap.xml"

BLOCKED_URL_PARTS = (
    "/inbox",
    "/conversations",
    "/login",
    "/signup",
    "/manage",
    "/user/",
    "/orders",
    "/cart",
)

CATALOG_SELECTORS = {
    "category_nav": "nav, .category-menu, .all-categories",
    "category_link": "a[href]",
    "category_title": "span, .title, .category-name",
    "subcategory_link": "a[href]",
}

KWORK_CARD_SELECTORS = {
    "kwork_card": "div.js-kwork-card.kwork-card-item[data-id], div.kwork-card-item[data-id]",
    "kwork_id": "div.js-kwork-card.kwork-card-item[data-id], div.kwork-card-item[data-id]",
    "kwork_url": ".kwork-card-item__title a[href], .kwork-card-item__cover a[href]",
    "kwork_title": ".kwork-card-item__title a span.first-letter, .kwork-card-item__title a",
    "kwork_price": ".kwork-card-item__info-price .price-wrap__value, .kwork-card-item__info-price, .price-wrap__value",
    "kwork_seller": ".kwork-card-item__username a",
    "kwork_rating": ".kwork-card-item__rating-number",
    "kwork_reviews": ".kwork-card-item__rating-count",
    "kwork_seller_level": ".kwork-card-item__user-level",
    "kwork_badges": ".cusongsblock__labels, .fox-express, .kwork-card-item__user-level",
    "kwork_keywords": ".tag, .tags a, [class*='tag'], [class*='keyword']",
    "filter_keywords": ".filter a, .filters a, .sidebar a, [class*='filter'] a",
    "pagination_next": "a[rel='next'], .pagination a.next, .paging a.next, a[href*='page=']",
}

REQUIRED_SELECTORS = {
    "catalog": ("category_link",),
    "cards": ("kwork_card", "kwork_title", "kwork_price"),
}

ALLOWED_PUBLIC_CATEGORY_NAMES = (
    "Дизайн",
    "Разработка и IT",
    "Тексты и переводы",
    "SEO и трафик",
    "Соцсети и маркетинг",
    "Аудио, видео, съемка",
    "Бизнес и жизнь",
    "AI-услуги",
)
