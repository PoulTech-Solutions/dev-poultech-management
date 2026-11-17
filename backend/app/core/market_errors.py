"""Market / NECC egg rate errors."""

from app.core.exceptions import AppError


class MarketZoneNotFoundError(AppError):
    status_code = 404
    code = 'market_zone_not_found'
    message = 'Unknown NECC market zone'


class MarketDataUnavailableError(AppError):
    status_code = 503
    code = 'market_data_unavailable'
    message = 'Egg rate data is not available yet; try again after the next sync'


class MarketSyncError(AppError):
    status_code = 502
    code = 'market_sync_failed'
    message = 'Could not refresh egg rates from NECC'


class ProfileLocationRequiredError(AppError):
    status_code = 403
    code = 'profile_location_required'
    message = 'Add pincode, state, and district on your profile to see egg rates for your area'
