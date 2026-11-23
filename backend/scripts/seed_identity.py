"""Idempotent seed: roles, permissions, the first super admin.

Run once per environment after `alembic upgrade head`:
    docker compose exec api python -m scripts.seed_identity

Passwords are read from env, never hard-coded and never printed.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import structlog
from sqlalchemy import select

# Lets both `python -m scripts.seed_identity` and `python scripts/seed_identity.py`
# work; without it the direct form dies with ModuleNotFoundError: app.
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.core.security import hash_password, password_policy_errors
from app.db.models.roles import Permission, Role, role_permissions
from app.db.models.users import User
from app.db.session import SessionLocal

log = structlog.get_logger()

ROLES: dict[str, str] = {
    'super_admin': 'Platform owner: admins, plans, system settings',
    'admin': 'Users, market rates, marketplace moderation, subscriptions',
    'staff': 'Internal team: vaccination, lab/testing, support',
    'farm_owner': 'Purchased the app; owns a farm and its records',
    'farm_member': 'Manager or worker added by a farm owner',
    'user': 'Registered, not purchased: Mitra and Services are locked',
}

# code -> (description, roles that get it). Adding a module later means adding a
# row here; no route changes.
PERMISSIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    'poultry_info:read': (
        'Market rates and advisories (free for all logged in)',
        ('user', 'farm_owner', 'farm_member', 'staff', 'admin', 'super_admin'),
    ),
    'market:rates:manage': (
        'Update culls and broiler market rates (manual; API sync later)',
        ('staff', 'admin', 'super_admin'),
    ),
    'marketplace:read': (
        'Browse listings',
        ('user', 'farm_owner', 'farm_member', 'staff', 'admin', 'super_admin'),
    ),
    'marketplace:write': (
        'Create and edit own listings',
        ('farm_owner', 'farm_member', 'admin', 'super_admin'),
    ),
    'marketplace:moderate': ('Approve or report listings', ('staff', 'admin', 'super_admin')),
    'services:read': ('Own service requests', ('farm_owner', 'farm_member', 'staff', 'admin', 'super_admin')),
    'services:write': ('Book a service', ('farm_owner',)),
    'services:fulfil': ('Work queue: assign, update, upload reports', ('staff', 'admin', 'super_admin')),
    'mitra:read': ('Farm records', ('farm_owner', 'admin', 'super_admin')),
    'mitra:write': ('Daily records, stock, billing', ('farm_owner',)),
    'user:read:self': ('Read own profile', tuple(ROLES)),
    'user:write:self': ('Update own profile', tuple(ROLES)),
    'user:delete:self': ('Deactivate own account', ('farm_owner', 'user', 'admin', 'super_admin')),
    'admin:users:read': (
        'List directory (staff/admin see farmers; admin+ see staff)',
        ('staff', 'admin', 'super_admin'),
    ),
    'admin:users:manage': ('Change role, deactivate, reactivate', ('admin', 'super_admin')),
    'admin:roles:promote': ('Promote or demote an admin', ('super_admin',)),
}


async def _one(db, stmt):
    """scalar_one_or_none in one line, so the loop bodies stay readable."""
    return (await db.execute(stmt)).scalar_one_or_none()


def _holders_of(code: str) -> tuple[str, ...]:
    return PERMISSIONS[code][1]


async def _ids_by_code(db, codes: set[str]) -> dict[str, object]:
    if not codes:
        return {}
    rows = await db.execute(select(Permission.id, Permission.code).where(Permission.code.in_(codes)))
    return {code: pid for pid, code in rows.all()}


async def _ensure_wiring(db, role_id, wanted: set[str]) -> bool:
    """Sync one role's permissions through the link table.

    The link table is written directly rather than through ``role.permissions``:
    assigning to the collection makes the ORM lazy-load it, and a lazy load inside
    an async session is a MissingGreenlet crash. A seed script should be plain,
    idempotent SQL anyway.
    """
    rows = await db.execute(
        select(role_permissions.c.permission_id).where(role_permissions.c.role_id == role_id)
    )
    linked = {pid for (pid,) in rows.all()}
    targets = await _ids_by_code(db, wanted)
    missing = [pid for code, pid in sorted(targets.items()) if pid not in linked]
    extra = [pid for pid in linked if pid not in set(targets.values())]
    if missing:
        await db.execute(
            role_permissions.insert(), [{'role_id': role_id, 'permission_id': pid} for pid in missing]
        )
    if extra:
        await db.execute(
            role_permissions.delete().where(
                role_permissions.c.role_id == role_id, role_permissions.c.permission_id.in_(extra)
            )
        )
    return bool(missing or extra)


async def seed() -> int:
    created: list[str] = []
    async with SessionLocal() as db:
        for code, (description, _holders) in PERMISSIONS.items():
            if await _one(db, select(Permission).where(Permission.code == code)) is None:
                db.add(Permission(code=code, description=description))
                await db.flush()
                created.append(f'permission {code}')

        for name, description in ROLES.items():
            role = await _one(db, select(Role).where(Role.name == name))
            if role is None:
                role = Role(name=name, description=description, is_system=True)
                db.add(role)
                await db.flush()
                created.append(f'role {name}')
            # Re-derived on every run, so editing PERMISSIONS above is enough to move
            # a permission between roles the next time this script is run.
            if await _ensure_wiring(db, role.id, {c for c in PERMISSIONS if name in _holders_of(c)}):
                created.append(f'role_permissions {name}')

        email = os.getenv('SEED_ADMIN_EMAIL')
        password = os.getenv('SEED_ADMIN_PASSWORD')
        if email and password:
            email = email.strip().lower()
            errors = password_policy_errors(password, email)
            if errors:
                print('SEED_ADMIN_PASSWORD rejected: ' + '; '.join(errors), file=sys.stderr)
                return 2
            admin_role = (await db.execute(select(Role).where(Role.name == 'super_admin'))).scalar_one()
            existing = await _one(db, select(User).where(User.email == email))
            if existing is None:
                db.add(
                    User(
                        email=email,
                        password_hash=hash_password(password),
                        role_id=admin_role.id,
                        is_email_verified=True,
                        is_active=True,
                    )
                )
                created.append(f'admin {email}')
        elif not any(c.startswith('admin') for c in created):
            print('note: SEED_ADMIN_EMAIL/PASSWORD not set, no admin created', file=sys.stderr)

        await db.commit()

    for line in created:
        log.info('seed_created', item=line)
    print(f'seed done: {len(created)} created, {len(ROLES)} roles, {len(PERMISSIONS)} permissions')
    return 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(seed()))
