from app.modules.market.zones import pick_nearest_hub_in_state, resolve_zone_name


def test_pune_profile_picks_mumbai_when_pune_missing():
    known = {'Mumbai (CC)', 'Nagpur'}
    hub = pick_nearest_hub_in_state('Maharashtra', 'Pune', 'Pune', known)
    assert hub == 'Mumbai (CC)'


def test_pune_profile_picks_nagpur_when_only_nagpur():
    known = {'Nagpur'}
    hub = pick_nearest_hub_in_state('Maharashtra', 'Pune', 'Pune', known)
    assert hub == 'Nagpur'


def test_vidarbha_prefers_nagpur_over_mumbai():
    known = {'Mumbai (CC)', 'Nagpur'}
    hub = pick_nearest_hub_in_state('Maharashtra', 'Nagpur', None, known)
    assert hub == 'Nagpur'


def test_resolve_zone_state_nearest_for_pune_without_pune_zone():
    known = {'Mumbai (CC)', 'Nagpur'}
    selected, resolved, status = resolve_zone_name(
        preferred=None,
        village_city='Pune',
        district='Pune',
        state='Maharashtra',
        known_zones=known,
        fallback='Pune',
    )
    assert selected == 'Mumbai (CC)'
    assert status == 'state_nearest'
    assert resolved == 'Mumbai (CC)'


def test_namakkal_district_matches_hub():
    known = {'Namakkal', 'Chennai (CC)', 'Pune'}
    selected, resolved, status = resolve_zone_name(
        preferred=None,
        village_city=None,
        district='Namakkal',
        state='Tamil Nadu',
        known_zones=known,
        fallback='Pune',
    )
    assert selected == 'Namakkal'
    assert status in {'exact', 'alias', 'fuzzy'}
    assert resolved == 'Namakkal'


def test_chattisgarh_csv_spelling_maps_to_raipur():
    known = {'Raipur', 'Pune'}
    hub = pick_nearest_hub_in_state('Chattisgarh', 'Raipur', None, known)
    assert hub == 'Raipur'


def test_thane_pincode_maps_to_mumbai_not_pune():
    known = {'Pune', 'Mumbai (CC)', 'Nagpur'}
    selected, resolved, status = resolve_zone_name(
        preferred=None,
        village_city=None,
        district='Thane',
        state='Maharashtra',
        pincode='400601',
        known_zones=known,
        fallback='Pune',
    )
    assert selected == 'Mumbai (CC)'
    assert status in {'pincode', 'district', 'alias'}
    assert resolved == 'Mumbai (CC)'


def test_nagpur_pin_maps_to_nagpur_hub():
    known = {'Pune', 'Mumbai (CC)', 'Nagpur'}
    selected, _, status = resolve_zone_name(
        preferred=None,
        village_city=None,
        district='Nagpur',
        state='Maharashtra',
        pincode='440001',
        known_zones=known,
        fallback='Pune',
    )
    assert selected == 'Nagpur'
    assert status in {'pincode', 'district', 'exact', 'alias', 'fuzzy'}


def test_salem_district_maps_to_namakkal():
    known = {'Namakkal', 'Chennai (CC)', 'Pune'}
    selected, _, status = resolve_zone_name(
        preferred=None,
        village_city=None,
        district='Salem',
        state='Tamil Nadu',
        pincode='636001',
        known_zones=known,
        fallback='Pune',
    )
    assert selected == 'Namakkal'
    assert status in {'pincode', 'district'}


def test_saved_city_does_not_override_pune_pincode():
    known = {'Pune', 'Bengaluru (CC)', 'Mumbai (CC)'}
    selected, resolved, status = resolve_zone_name(
        preferred='Bengaluru (CC)',
        village_city='Pune',
        district='Pune',
        state='Maharashtra',
        pincode='411001',
        known_zones=known,
        fallback='Pune',
    )
    assert selected == 'Pune'
    assert resolved == 'Pune'
    assert status in {'exact', 'alias', 'fuzzy', 'pincode', 'district'}


def test_no_location_falls_back_to_pune():
    known = {'Pune', 'Namakkal', 'Delhi (CC)'}
    selected, resolved, status = resolve_zone_name(
        preferred=None,
        village_city=None,
        district=None,
        state=None,
        pincode=None,
        known_zones=known,
        fallback='Pune',
    )
    assert selected == 'Pune'
    assert status == 'fallback'
    assert resolved is None
