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
    'nagpur': 'Nagpur',
}

# NECC hubs per state (preference when profile city is not on the NECC list).
_STATE_HUB_ORDER: dict[str, list[str]] = {
    'maharashtra': ['Pune', 'Mumbai (CC)', 'Nagpur'],
    'gujarat': ['Ahmedabad', 'Surat'],
    'karnataka': ['Bengaluru (CC)', 'Mysuru', 'Hospet', 'Namakkal'],
    'tamil nadu': ['Namakkal', 'Chennai (CC)'],
    'delhi': ['Delhi (CC)'],
    'haryana': ['Barwala', 'Delhi (CC)'],
    'west bengal': ['Kolkata (WB)'],
    'telangana': ['Hyderabad', 'Warangal'],
    'andhra pradesh': ['Vijayawada', 'Vizag', 'Chittoor', 'E.Godavari', 'W.Godavari', 'Hyderabad'],
    'odisha': ['Brahmapur (OD)'],
    'madhya pradesh': ['Bhopal', 'Jabalpur'],
    'rajasthan': ['Ajmer'],
    'punjab': ['Ludhiana'],
    'chhattisgarh': ['Raipur'],
}

_VIDARBHA_HINTS = (
    'nagpur',
    'wardha',
    'amravati',
    'chandrapur',
    'akola',
    'yavatmal',
    'buldhana',
    'bhandara',
    'gondia',
    'washim',
)
_MUMBAI_REGION_HINTS = ('mumbai', 'thane', 'palghar', 'raigad', 'navi', 'mumbai suburban')
_PUNE_REGION_HINTS = (
    'pune',
    'satara',
    'sangli',
    'kolhapur',
    'solapur',
    'ahmednagar',
    'ahilyanagar',
)

# First 2-3 PIN digits -> NECC hub (India Post CSV has no lat/long).
_PIN_HUB: dict[str, str] = {
    '400': 'Mumbai (CC)',
    '401': 'Mumbai (CC)',
    '40': 'Mumbai (CC)',
    '41': 'Pune',
    '42': 'Pune',
    '44': 'Nagpur',
    '11': 'Delhi (CC)',
    '12': 'Delhi (CC)',
    '13': 'Barwala',
    '14': 'Ludhiana',
    '38': 'Ahmedabad',
    '39': 'Surat',
    '50': 'Hyderabad',
    '56': 'Bengaluru (CC)',
    '57': 'Mysuru',
    '60': 'Chennai (CC)',
    '63': 'Namakkal',
    '70': 'Kolkata (WB)',
    '30': 'Ajmer',
    '45': 'Bhopal',
    '46': 'Bhopal',
    '48': 'Jabalpur',
    '49': 'Raipur',
    '75': 'Brahmapur (OD)',
    '76': 'Brahmapur (OD)',
}

_DISTRICT_HUB: dict[str, str] = {
    'thane': 'Mumbai (CC)',
    'palghar': 'Mumbai (CC)',
    'raigad': 'Mumbai (CC)',
    'mumbai': 'Mumbai (CC)',
    'mumbai suburban': 'Mumbai (CC)',
    'nagpur': 'Nagpur',
    'wardha': 'Nagpur',
    'amravati': 'Nagpur',
    'chandrapur': 'Nagpur',
    'akola': 'Nagpur',
    'salem': 'Namakkal',
    'erode': 'Namakkal',
    'namakkal': 'Namakkal',
    'karur': 'Namakkal',
    'tiruppur': 'Namakkal',
    'coimbatore': 'Namakkal',
    'chennai': 'Chennai (CC)',
    'chengalpattu': 'Chennai (CC)',
    'kanchipuram': 'Chennai (CC)',
    'tiruvallur': 'Chennai (CC)',
    'surat': 'Surat',
    'navsari': 'Surat',
    'valsad': 'Surat',
    'ahmedabad': 'Ahmedabad',
    'gandhinagar': 'Ahmedabad',
    'bengaluru urban': 'Bengaluru (CC)',
    'bengaluru rural': 'Bengaluru (CC)',
    'bangalore': 'Bengaluru (CC)',
    'mysuru': 'Mysuru',
    'mysore': 'Mysuru',
    'visakhapatnam': 'Vizag',
    'krishna': 'Vijayawada',
}


def necc_disclaimer() -> str:
    return _NECC_DISCLAIMER


def normalize_lookup_key(value: str) -> str:
    text = unicodedata.normalize('NFKC', value).strip().lower()
    text = re.sub(r'\s+', ' ', text)
    return text


def _state_key(state: str | None) -> str | None:
    if not state:
        return None
    key = normalize_lookup_key(state)
    aliases = {
        'mh': 'maharashtra',
        'maharashtra': 'maharashtra',
        'gj': 'gujarat',
        'gujarat': 'gujarat',
        'ka': 'karnataka',
        'karnataka': 'karnataka',
        'tn': 'tamil nadu',
        'tamil nadu': 'tamil nadu',
        'dl': 'delhi',
        'delhi': 'delhi',
        'nct of delhi': 'delhi',
        'wb': 'west bengal',
        'west bengal': 'west bengal',
        'ts': 'telangana',
        'telangana': 'telangana',
        'ap': 'andhra pradesh',
        'andhra pradesh': 'andhra pradesh',
        'mp': 'madhya pradesh',
        'madhya pradesh': 'madhya pradesh',
        'rj': 'rajasthan',
        'rajasthan': 'rajasthan',
        'pb': 'punjab',
        'punjab': 'punjab',
        'cg': 'chhattisgarh',
        'chhattisgarh': 'chhattisgarh',
        'chattisgarh': 'chhattisgarh',
        'odisha': 'odisha',
        'orissa': 'odisha',
        'haryana': 'haryana',
        'hr': 'haryana',
    }
    return aliases.get(key)


def _hub_order_for_profile(
    state_key: str,
    district: str | None,
    village_city: str | None,
) -> list[str]:
    base = list(_STATE_HUB_ORDER.get(state_key, []))
    if state_key != 'maharashtra' or not base:
        return base
    loc = normalize_lookup_key(' '.join(p for p in (village_city, district) if p))
    if any(h in loc for h in _VIDARBHA_HINTS):
        return ['Nagpur', 'Mumbai (CC)', 'Pune']
    if any(h in loc for h in _MUMBAI_REGION_HINTS):
        return ['Mumbai (CC)', 'Pune', 'Nagpur']
    if any(h in loc for h in _PUNE_REGION_HINTS):
        return ['Pune', 'Mumbai (CC)', 'Nagpur']
    return base


def pick_nearest_hub_in_state(
    state: str | None,
    district: str | None,
    village_city: str | None,
    known_zones: set[str],
) -> str | None:
    """First NECC hub in state priority list that exists in synced data."""
    state_key = _state_key(state)
    if not state_key:
        return None
    for hub in _hub_order_for_profile(state_key, district, village_city):
        if hub in known_zones:
            return hub
    return None


def _match_place_to_hub(candidate: str | None, known_zones: set[str]) -> tuple[str, str] | None:
    if not candidate:
        return None
    key = normalize_lookup_key(candidate)
    mapped = _ZONE_ALIASES.get(key)
    if mapped and mapped in known_zones:
        return mapped, 'alias'
    if candidate in known_zones:
        return candidate, 'exact'
    for zone in known_zones:
        if key and key in normalize_lookup_key(zone):
            return zone, 'fuzzy'
    return None


def hub_from_pincode(pincode: str | None, known_zones: set[str]) -> str | None:
    digits = ''.join(ch for ch in (pincode or '') if ch.isdigit())
    if len(digits) < 2:
        return None
    for width in (3, 2):
        hub = _PIN_HUB.get(digits[:width])
        if hub and hub in known_zones:
            return hub
    return None


def hub_from_district(district: str | None, known_zones: set[str]) -> str | None:
    if not district:
        return None
    hub = _DISTRICT_HUB.get(normalize_lookup_key(district))
    if hub and hub in known_zones:
        return hub
    return None


def resolve_zone_name(
    *,
    preferred: str | None,
    village_city: str | None,
    district: str | None,
    state: str | None,
    known_zones: set[str],
    fallback: str,
    tehsil: str | None = None,
    pincode: str | None = None,
) -> tuple[str, str | None, str]:
    """Return (selected_zone, resolved_from_profile, match_status).

    Profile address / pincode always wins. ``preferred`` is only used when the
    user has no location yet (otherwise a one-tap city preview stuck forever).
    """

    has_location = bool((pincode and str(pincode).strip()) or district or village_city or tehsil)
    if preferred and preferred in known_zones and not has_location:
        return preferred, preferred, 'preferred'

    for candidate in (village_city, tehsil, district):
        hit = _match_place_to_hub(candidate, known_zones)
        if hit:
            return hit[0], hit[0], hit[1]

    pin_hub = hub_from_pincode(pincode, known_zones)
    if pin_hub:
        return pin_hub, pin_hub, 'pincode'

    dist_hub = hub_from_district(district, known_zones)
    if dist_hub:
        return dist_hub, dist_hub, 'district'

    state_hub = pick_nearest_hub_in_state(state, district, village_city, known_zones)
    if state_hub:
        return state_hub, state_hub, 'state_nearest'

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


def state_label_for_zone(name: str) -> str:
    """Best-effort Indian state label for a NECC hub (for city pickers)."""
    for state, hubs in _STATE_HUB_ORDER.items():
        if name in hubs:
            return state.replace('_', ' ').title()
    return 'Other'


def validate_zone_or_raise(zone: str, known: set[str]) -> str:
    if zone not in known:
        from app.core.market_errors import MarketZoneNotFoundError

        raise MarketZoneNotFoundError(
            'Zone is not on the NECC list',
            details=[{'field': 'zone', 'message': f'Unknown zone {zone!r}'}],
        )
    return zone
