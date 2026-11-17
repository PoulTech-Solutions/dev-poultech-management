from __future__ import annotations

import re
import unicodedata

DEFAULT_FALLBACK_ZONE = 'Pune'

_NECC_DISCLAIMER = (
    'Daily egg prices suggested by NECC are indicative and not mandatory, published '
    'for trade reference only. NECC does not enforce compliance with suggested prices.'
)

# Normalise free-text profile locations to NECC centre names (subset; extended after each sync).
_ZONE_ALIASES: dict[str, str] = {
    'pune': 'Pune',
    'mumbai': 'Mumbai (CC)',
    'bombay': 'Mumbai (CC)',
    'delhi': 'Delhi (CC)',
    'new delhi': 'Delhi (CC)',
    'bengaluru': 'Bengaluru (CC)',
    'bangalore': 'Bengaluru (CC)',
    'chennai': 'Chennai (CC)',
    'madras': 'Chennai (CC)',
    'kolkata': 'Kolkata (WB)',
    'calcutta': 'Kolkata (WB)',
    'hyderabad': 'Hyderabad',
    'ahmedabad': 'Ahmedabad',
    'surat': 'Surat',
    'namakkal': 'Namakkal',
    'vizag': 'Vizag',
    'visakhapatnam': 'Vizag',
    'vijayawada': 'Vijayawada',
    'ludhiana': 'Ludhiana',
    'ajmer': 'Ajmer',
    'barwala': 'Barwala',
    'raipur': 'Raipur',
    'jabalpur': 'Jabalpur',
    'allahabad': 'Allahabad (CC)',
    'prayagraj': 'Allahabad (CC)',
    'bhopal': 'Bhopal',
}


def necc_disclaimer() -> str:
    return _NECC_DISCLAIMER


def normalize_lookup_key(value: str) -> str:
    text = unicodedata.normalize('NFKC', value).strip().lower()
    text = re.sub(r'\s+', ' ', text)
    return text


def resolve_zone_name(
    *,
    preferred: str | None,
    village_city: str | None,
    district: str | None,
    state: str | None,
    known_zones: set[str],
    fallback: str,
) -> tuple[str, str | None, str]:
    """Return (selected_zone, resolved_from_profile, match_status)."""

    if preferred and preferred in known_zones:
        return preferred, preferred, 'preferred'

    for candidate in (village_city, district, region_hint(state)):
        if not candidate:
            continue
        key = normalize_lookup_key(candidate)
        mapped = _ZONE_ALIASES.get(key)
        if mapped and mapped in known_zones:
            return mapped, mapped, 'alias'
        if candidate in known_zones:
            return candidate, candidate, 'exact'
        # Fuzzy: NECC name contains profile token
        for zone in known_zones:
            if key and key in normalize_lookup_key(zone):
                return zone, zone, 'fuzzy'

    if fallback in known_zones:
        return fallback, None, 'fallback'
    if known_zones:
        first = sorted(known_zones)[0]
        return first, None, 'fallback'
    return fallback, None, 'unmapped'


def region_hint(state: str | None) -> str | None:
    if not state:
        return None
    key = normalize_lookup_key(state)
    if key == 'maharashtra':
        return 'Pune'
    if key in {'karnataka', 'ka'}:
        return 'Bengaluru (CC)'
    if key in {'tamil nadu', 'tn'}:
        return 'Namakkal'
    if key in {'delhi', 'nct of delhi'}:
        return 'Delhi (CC)'
    return None


def validate_zone_or_raise(zone: str, known: set[str]) -> str:
    if zone not in known:
        from app.core.market_errors import MarketZoneNotFoundError

        raise MarketZoneNotFoundError(
            'Zone is not on the NECC list',
            details=[{'field': 'zone', 'message': f'Unknown zone {zone!r}'}],
        )
    return zone
