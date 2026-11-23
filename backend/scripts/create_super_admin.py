"""One-shot bootstrap: create (or rotate) the platform super_admin.

Password is hashed with argon2id before insert. Plaintext is never stored.

  python -m scripts.create_super_admin \\
    --email owner@example.com --password '...' \\
    --phone 9876543210 --first-name Vaibhav --last-name Sharma

Run `python -m scripts.seed_identity` first so roles exist.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import func, select

from app.core.constants import RoleName
from app.core.security import hash_password, password_policy_errors
from app.db.models.roles import Role
from app.db.models.users import User
from app.db.session import SessionLocal
from app.modules.auth.repository import AuthRepository
from app.modules.auth.schemas import normalise_email, normalise_indian_mobile


async def _run(args: argparse.Namespace) -> int:
    try:
        email = normalise_email(args.email)
        phone = normalise_indian_mobile(args.phone)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    errors = password_policy_errors(args.password, email)
    if errors:
        print('Password rejected: ' + '; '.join(errors), file=sys.stderr)
        return 2

    async with SessionLocal() as db:
        role = (await db.execute(select(Role).where(Role.name == RoleName.SUPER_ADMIN))).scalar_one_or_none()
        if role is None:
            print('super_admin role missing — run: python -m scripts.seed_identity', file=sys.stderr)
            return 2
        repo = AuthRepository(db)
        other = (
            await db.execute(
                select(func.count())
                .select_from(User)
                .join(Role, User.role_id == Role.id)
                .where(
                    Role.name == RoleName.SUPER_ADMIN,
                    User.deleted_at.is_(None),
                    User.email != email,
                )
            )
        ).scalar_one()
        if int(other) > 0:
            print('A different super_admin already exists. Use that account from the admin dashboard.', file=sys.stderr)
            return 2

        existing = await repo.get_by_email(email)
        password_hash = hash_password(args.password)
        if existing is None:
            if await repo.email_taken(email):
                print('Email is already registered as another role.', file=sys.stderr)
                return 2
            if await repo.phone_taken(phone):
                print('This mobile number is already in use.', file=sys.stderr)
                return 2
            user = await repo.create_user(
                email=email,
                password_hash=password_hash,
                role=role,
                is_active=True,
                is_email_verified=True,
            )
            await repo.upsert_profile(
                user_id=user.id,
                first_name=args.first_name.strip(),
                last_name=args.last_name.strip(),
                phone_e164=phone,
            )
            await repo.mark_profile_complete(user.id)
            await db.commit()
            print(f'Created super_admin {email} (password stored as argon2 hash)')
            return 0

        if existing.role.name != RoleName.SUPER_ADMIN:
            print('Email is already registered as another role.', file=sys.stderr)
            return 2
        existing.password_hash = password_hash
        existing.is_email_verified = True
        existing.is_active = True
        await repo.upsert_profile(
            user_id=existing.id,
            first_name=args.first_name.strip(),
            last_name=args.last_name.strip(),
            phone_e164=phone,
        )
        await repo.mark_profile_complete(existing.id)
        await db.commit()
        print(f'Updated super_admin {email} (password re-hashed with argon2)')
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description='Create the PoulTech super_admin (password is hashed)')
    parser.add_argument('--email', required=True)
    parser.add_argument('--password', required=True)
    parser.add_argument('--phone', required=True)
    parser.add_argument('--first-name', required=True)
    parser.add_argument('--last-name', required=True)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == '__main__':
    main()
