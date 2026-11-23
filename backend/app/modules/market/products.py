from __future__ import annotations

POULTRY_PRODUCTS: dict[str, dict[str, str]] = {
    'culls': {
        'id': 'culls',
        'title': 'Culls birds',
        'unit': 'per bird',
        'ticker_label': 'Culls rate',
    },
    'broilers': {
        'id': 'broilers',
        'title': 'Broiler rate',
        'unit': 'per kg',
        'ticker_label': 'Broiler rate',
    },
}

MANUAL_PRODUCTS = frozenset(POULTRY_PRODUCTS)
