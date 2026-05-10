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
    "kwork_card": ".kwork-card, .kwork-card-item, .card, article, [data-kwork-id]",
    "kwork_title": ".kwork-card-item__title, .title, h1, h2, a",
    "kwork_url": "a[href]",
    "kwork_price": ".price, .kwork-price, .kwork-card-item__price, [class*='price']",
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
