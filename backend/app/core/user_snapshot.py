"""Safe Redis-facing snapshots of a user. No secrets, no tokens, no password material."""

from __future__ import annotations

from typing import Any

from app.db.models.users import User


def profile_snapshot(user: User) -> dict[str, Any]:
    """Flat profile dict for ``sess:{sid}:p``. Callers must not add secrets."""
    profile = user.profile
    return {
        'user_id': str(user.id),
        'email': user.email,
        'role': user.role.name,
        'first_name': profile.first_name if profile else '',
        'last_name': profile.last_name if profile else '',
        'phone': profile.phone_e164 if profile else None,
        'is_phone_verified': bool(profile and profile.is_phone_verified),
        'address_line1': profile.address_line1 if profile else None,
        'address_line2': profile.address_line2 if profile else None,
        'village_city': profile.village_city if profile else None,
        'district': profile.district if profile else None,
        'state': profile.state if profile else None,
        'pincode': profile.pincode if profile else None,
        'country': profile.country if profile else 'IN',
        'avatar_url': profile.avatar_url if profile else None,
        'preferred_language': profile.preferred_language if profile else 'en',
        'region': profile.region if profile else None,
        'is_profile_complete': bool(profile and profile.has_required_fields),
        'is_email_verified': user.is_email_verified,
        'must_verify_email': not user.is_email_verified,
        'created_at': user.created_at.isoformat(),
        'last_login_at': user.last_login_at.isoformat() if user.last_login_at else None,
    }


def permission_codes(user: User) -> tuple[str, ...]:
    return tuple(sorted(p.code for p in user.role.permissions))
