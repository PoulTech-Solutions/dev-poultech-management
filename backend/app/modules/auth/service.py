"""Auth business logic: signup, OTP challenge, login, sessions, passwords, restore.

Rules this module owns, in one place:
  * the OTP lives only in Redis (5 min, one use, five attempts) - never in a table
  * the plaintext OTP is never stored and never crosses the queue unsealed
  * one live session per account; a second device is refused until the client
    confirms the switch, so nobody is silently signed out
  * unknown email and wrong password answer identically, until a password has
    actually been verified (only then is "deactivated" safe to reveal)
"""

from __future__ import annotations

import asyncio
import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from fastapi import Request
from fastapi.responses import JSONResponse
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.core.exceptions import ConflictError, RateLimitError, UnauthorizedError
from app.core.rate_limit import client_ip
from app.core.redis_conn import get_redis
from app.core.security import (
    create_access_token,
    encrypt_otp_payload,
    generate_otp,
    generate_refresh_token,
    hash_otp,
    hash_password_async,
    hash_token,
    new_otp_session_id,
    password_policy_errors,
    refresh_token_expiry,
    verify_password_async,
)
from app.core.store import (
    OtpError,
    OtpPurpose,
    OtpTicket,
    OtpVerifyError,
    create_otp,
    drop_otp,
    drop_session_cache,
    get_otp_state,
    resend_otp,
    verify_otp,
    warm_session_caches,
)
from app.core.user_snapshot import permission_codes, profile_snapshot
from app.db.models.users import User
from app.modules.auth.repository import AuthRepository
from app.modules.auth.schemas import (
    LoginRequest,
    OtpResendRequest,
    OtpVerifyRequest,
    PasswordChangeRequest,
    PasswordResetRequest,
    SignupRequest,
)

log = structlog.get_logger()

_PURPOSE_LABELS = {
    'signup': 'confirm your new account',
    'login': 'sign in',
    'reset': 'reset your password',
    'change_password': 'confirm this change',
    'email_change': 'confirm your new email',
    'restore': 'reactivate your account',
    'internal_signup': 'confirm your staff account',
}

# Redis OTP extra keys that must survive a resend (pending signup payload).
_OTP_PENDING_KEYS = ('fn', 'ln', 'ph', 'pw', 'rl', 'cfm', 'p', 'ac')


def _device_switch_details(signed_in_at: str) -> list[dict[str, str]]:
    """List shape so clients can call details.map. signed_in_at stays on the item."""
    return [
        {
            'field': 'session',
            'message': 'Already signed in on another device',
            'signed_in_at': signed_in_at,
        }
    ]


def mask_email(email: str) -> str:
    local, _, domain = email.partition('@')
    if not domain:
        return '***'
    return f'{local[:1]}***@{domain}'


class AuthError(Exception):
    """A client-safe refusal raised from anywhere in the flow.

    Kept separate from AppError so an unauthenticated step cannot accidentally
    return a 500: the router maps it explicitly.
    """

    def __init__(self, code: str, message: str, *, status: int = 400) -> None:
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Challenge:
    ticket: OtpTicket
    masked: str
    purpose: str
    display_name: str


@dataclass(frozen=True, slots=True)
class Issued:
    access_token: str
    refresh_token: str
    session_id: uuid.UUID
    expires_in: int
    role: str
    must_verify_email: bool = False
    cookie_max_age: int | None = None


class AuthService:
    def __init__(self, repo: AuthRepository) -> None:
        self._repo = repo
        self._db = repo.db
        self._s = get_settings()

    # ---------------------------------------------------------- OTP plumbing

    @staticmethod
    def _display_name(user: User | None, fallback: str) -> str:
        """Only reads attributes that are already loaded.

        Touching user.profile here would lazy-load outside the greenlet and blow
        up: relationships are loaded by the repository, never by the service.
        """
        if user is not None:
            return user.email.split('@')[0]
        return fallback

    def _render(self, otp: str, purpose: str, name: str) -> tuple[str, str]:
        minutes = math.ceil(self._s.otp_ttl_seconds / 60)
        subject = f'Your Poultech code - {_PURPOSE_LABELS[purpose]}'
        body = (
            f'Hello {name},\n\n'
            f'Your Poultech verification code is {otp}.\n'
            f'It works once and expires in {minutes} minutes.\n\n'
            f'This code was requested to {_PURPOSE_LABELS[purpose]}. If that was not you, '
            'change your password and sign out of all devices in Settings.\n'
        )
        return subject, body

    # Celery's client is synchronous, so publishing runs in a worker thread with a
    # deadline. Without the deadline a broker that accepts the socket and then never
    # answers holds the *event loop* and the request hangs - which is how a mail
    # outage turns into an auth outage.
    PUBLISH_TIMEOUT_SECONDS = 4.0

    async def _enqueue(
        self, *, to: str, subject: str, purpose: str, session_id: str, sealed: str, name: str
    ) -> None:
        from app.tasks.email import send_otp_email

        def _publish() -> None:
            send_otp_email.delay(
                to=to,
                subject=subject,
                purpose=purpose,
                session_id=session_id,
                body=sealed,
                display_name=name,
            )

        try:
            await asyncio.wait_for(asyncio.to_thread(_publish), timeout=self.PUBLISH_TIMEOUT_SECONDS)
        except TimeoutError:
            log.error('otp_enqueue_timeout', timeout_seconds=self.PUBLISH_TIMEOUT_SECONDS)
            raise AuthError(
                'mail_unavailable', 'Could not send the code right now. Please try again.', status=503
            ) from None
        except Exception as exc:
            # A dead broker must not leave a half-written challenge behind.
            log.error('otp_enqueue_failed', error=type(exc).__name__)
            raise AuthError(
                'mail_unavailable', 'Could not send the code right now. Please try again.', status=503
            ) from None

    async def _issue_challenge(
        self,
        *,
        user: User | None,
        email: str,
        purpose: OtpPurpose,
        resend: bool = False,
        session_id: str | None = None,
        extra: dict[str, str] | None = None,
        display_name: str | None = None,
    ) -> Challenge:
        display_name = display_name or self._display_name(user, email.split('@')[0])
        otp = generate_otp()
        sid = session_id or new_otp_session_id()
        otp_hash = hash_otp(otp, session_id=sid, purpose=purpose)
        stamp = {'id': email, **(extra or {})}
        if user is not None:
            stamp['u'] = str(user.id)
        try:
            if resend:
                ticket = await resend_otp(purpose=purpose, session_id=sid, otp_hash=otp_hash, identity=email)
            else:
                ticket = await create_otp(purpose=purpose, session_id=sid, otp_hash=otp_hash, identity=email)
        except OtpError as exc:
            code = 'otp_cooldown' if exc.kind == 'cooldown' else 'too_many_otp_requests'
            headers = {'Retry-After': str(exc.retry_after)} if exc.retry_after else None
            raise RateLimitError(
                'Too many codes requested for this email. Try again shortly.', code=code, headers=headers
            ) from None

        # 'raw' is the sealed blob (never the code): tests read it instead of a
        # mailbox, and a lost mail task can be re-sent from Redis without new OTP.
        stamp['raw'] = encrypt_otp_payload(otp, session_id=sid, purpose=purpose)
        await self._stamp(sid, purpose, stamp)
        subject, _body = self._render(otp, purpose, display_name)
        await self._enqueue(
            to=email,
            subject=subject,
            purpose=purpose,
            session_id=sid,
            sealed=encrypt_otp_payload(otp, session_id=sid, purpose=purpose),
            name=display_name,
        )
        return Challenge(
            ticket=ticket,
            masked=mask_email(email),
            purpose=purpose,
            display_name=display_name,
        )

    @staticmethod
    async def _stamp(sid: str, purpose: str, mapping: dict[str, str]) -> None:
        if not mapping:
            return
        from app.core.store import otp_key

        await get_redis().hset(otp_key(purpose, sid), mapping=mapping)  # type: ignore[arg-type]

    async def _challenge_state(self, *, sid: str, purpose: OtpPurpose) -> dict[str, str]:
        state = await get_otp_state(purpose=purpose, session_id=sid)
        if not state:
            raise AuthError('otp_expired', 'That code has expired. Request a new one.', status=410)
        return state

    async def _consume(self, *, sid: str, purpose: OtpPurpose, otp: str, identity: str) -> None:
        try:
            await verify_otp(
                purpose=purpose, session_id=sid, otp_hash=hash_otp(otp, session_id=sid, purpose=purpose)
            )
        except OtpVerifyError as exc:
            if exc.kind == 'too_many_attempts':
                await drop_otp(purpose=purpose, session_id=sid, identity=identity)
                raise AuthError('otp_attempts_exceeded', 'Too many wrong codes. Request a new one.') from None
            if exc.kind == 'expired_or_used':
                raise AuthError('otp_expired', 'That code has expired or was already used.') from None
            left = exc.attempts_left if exc.attempts_left is not None else 0
            raise AuthError('otp_invalid', f'Invalid code. {left} attempt(s) left.') from None
        await drop_otp(purpose=purpose, session_id=sid, identity=identity)

    # ---------------------------------------------------------------- signup

    async def _require_default_role(self):
        role = await self._repo.get_role_by_name(self._s.default_role_name)
        if role is None:
            log.critical('role_missing', role=self._s.default_role_name)
            raise AuthError(
                'not_initialised',
                'Server is not initialised: run `python -m scripts.seed_identity` after migrations.',
                status=500,
            )
        return role

    async def signup(self, payload: SignupRequest) -> Challenge:
        """Send the email OTP only. User + profile rows are created in verify_signup."""
        existing = await self._repo.get_by_email_any(payload.email)
        if existing is not None and (existing.deleted_at is not None or existing.is_email_verified):
            raise ConflictError('Email is already registered', code='email_taken')

        exclude_id = existing.id if existing is not None else None
        if await self._repo.phone_taken(payload.phone, exclude_user_id=exclude_id):
            raise ConflictError('This mobile number is already in use', code='phone_taken')

        role = await self._require_default_role()
        extra = {
            'fn': payload.first_name,
            'ln': payload.last_name,
            'ph': payload.phone,
            'rl': role.name,
        }
        if existing is not None:
            # Leftover unverified row from the old flow: keep the email claim,
            # replace the password, and do not treat it as a live account.
            existing.password_hash = await hash_password_async(payload.password)
            existing.is_active = False
            existing.is_email_verified = False
            await self._db.commit()
            log.info('signup_otp_reissued', user_id=str(existing.id))
            return await self._issue_challenge(
                user=existing,
                email=payload.email,
                purpose='signup',
                display_name=payload.first_name,
                extra=extra,
            )

        extra['pw'] = await hash_password_async(payload.password)
        log.info('signup_otp_pending', email_masked=mask_email(payload.email))
        return await self._issue_challenge(
            user=None,
            email=payload.email,
            purpose='signup',
            display_name=payload.first_name,
            extra=extra,
        )

    async def verify_signup(self, payload: OtpVerifyRequest) -> None:
        state = await self._challenge_state(sid=payload.otp_session_id, purpose='signup')
        first_name = state.get('fn', '').strip()
        last_name = state.get('ln', '').strip()
        phone = state.get('ph', '').strip()
        if not first_name or not last_name or not phone:
            raise AuthError('signup_expired', 'Start sign-up again.', status=410)

        leftover = await self._repo.get_by_id(uuid.UUID(state['u'])) if state.get('u') else None
        if await self._repo.phone_taken(phone, exclude_user_id=leftover.id if leftover else None):
            raise ConflictError('This mobile number is already in use', code='phone_taken')

        await self._consume(
            sid=payload.otp_session_id, purpose='signup', otp=payload.otp, identity=state.get('id', '')
        )

        user = leftover
        if user is None:
            role_name = state.get('rl') or self._s.default_role_name
            role = await self._repo.get_role_by_name(role_name)
            if role is None:
                raise AuthError(
                    'not_initialised',
                    'Server is not initialised: run `python -m scripts.seed_identity` after migrations.',
                    status=500,
                )
            password_hash = state.get('pw', '')
            if not password_hash:
                raise AuthError('signup_expired', 'Start sign-up again.', status=410)
            try:
                user = await self._repo.create_user(
                    email=state.get('id', ''),
                    password_hash=password_hash,
                    role=role,
                    is_active=True,
                    is_email_verified=True,
                )
            except IntegrityError:
                await self._db.rollback()
                raise ConflictError('Email is already registered', code='email_taken') from None

        await self._repo.upsert_profile(
            user_id=user.id,
            first_name=first_name,
            last_name=last_name,
            phone_e164=phone,
        )
        await self._repo.verify_email(user)
        await self._repo.mark_profile_complete(user.id)
        await self._db.commit()
        # No session here on purpose: registration activates the account, signing in
        # is the next step. Auto-login would occupy the one device slot and make the
        # user's first real sign-in look like a conflict.
        log.info('email_verified', user_id=str(user.id))

    async def issue_internal_signup(
        self, *, email: str, extra: dict[str, str], display_name: str
    ) -> Challenge:
        """OTP to the employee mailbox. Rows are created only after confirm."""
        return await self._issue_challenge(
            user=None,
            email=email,
            purpose='internal_signup',
            extra=extra,
            display_name=display_name,
        )

    async def consume_internal_signup(self, payload: OtpVerifyRequest) -> dict[str, str]:
        purpose: OtpPurpose = 'internal_signup'
        if payload.purpose != 'internal_signup':
            raise AuthError('otp_invalid', 'Start this registration again.')
        state = await self._challenge_state(sid=payload.otp_session_id, purpose=purpose)
        await self._consume(
            sid=payload.otp_session_id, purpose=purpose, otp=payload.otp, identity=state.get('id', '')
        )
        return state

    # -------------------------------------------------------------- log in

    async def login(self, payload: LoginRequest, request: Request) -> Challenge:
        user = await self._repo.get_by_email_any(payload.email)
        if user is None:
            await verify_password_async(payload.password, None)
            raise AuthError(
                'registration_required',
                'We could not find an account with this email. Check the address for typos, '
                'or register with a valid email you can access.',
            )
        if user.password_hash is None:
            await verify_password_async(payload.password, None)
            raise AuthError(
                'invalid_credentials',
                'The email or password is incorrect. Check both and try again, or use Forgot password.',
            )
        if not await verify_password_async(payload.password, user.password_hash):
            await self._register_failure(user, request)
            raise AuthError(
                'invalid_credentials',
                'The email or password is incorrect. Check both and try again, or use Forgot password.',
            )
        # Past a correct password it is safe - and needed - to name the state.
        if user.deleted_at is not None:
            raise AuthError(
                'account_deleted',
                'Your account is deactivated. Use "Reactivate account" to restore it.',
                status=403,
            )
        if not user.is_email_verified:
            raise AuthError(
                'email_not_verified',
                'This email is registered but not verified. Open the signup message in your inbox, '
                'or register again with a valid email you can access.',
            )
        if not user.is_active:
            raise AuthError(
                'account_inactive',
                'This account is deactivated. Use "Reactivate account" to restore it, or contact support.',
                status=403,
            )
        if user.locked_until and user.locked_until > datetime.now(UTC):
            wait = max(int((user.locked_until - datetime.now(UTC)).total_seconds()), 1)
            raise RateLimitError(
                'Too many failed attempts. Try again later.',
                code='account_locked',
                headers={'Retry-After': str(wait)},
            )
        # Answer the device conflict now, before an email is wasted and before a
        # code exists that the client could never spend.
        active = await self._repo.active_session(user.id)
        if active is not None and not payload.confirm_device_switch:
            raise ConflictError(
                'This account is already signed in on another device. Sign out there first, '
                'or confirm to move this session here.',
                code='already_signed_in',
                # Same body as the verify-otp refusal, so the dialog needs one branch.
                details=_device_switch_details(active.last_used_at.isoformat()),
            )
        challenge = await self._issue_challenge(
            user=user,
            email=user.email,
            purpose='login',
            extra={'cfm': '1'} if payload.confirm_device_switch else None,
        )
        await self._db.commit()
        log.info('login_otp_sent', user_id=str(user.id))
        return challenge

    async def login_with_otp(self, payload: OtpVerifyRequest, request: Request) -> Issued:
        state = await self._challenge_state(sid=payload.otp_session_id, purpose='login')
        user = await self._repo.get_by_id(uuid.UUID(state['u'])) if state.get('u') else None
        if user is None:
            raise AuthError('otp_expired', 'Start again to get a new code.', status=410)
        if not user.is_email_verified or not user.is_active:
            raise AuthError(
                'email_not_verified',
                'This email is registered but not verified. Open the signup message in your inbox, '
                'or register again with a valid email you can access.',
            )
        await self._consume(
            sid=payload.otp_session_id, purpose='login', otp=payload.otp, identity=state.get('id', '')
        )
        # The confirmation belongs to the challenge: the client already saw the
        # "another device" notice at /login, it must not have to say it twice.
        return await self._open_session(
            user,
            request,
            remember_me=payload.remember_me,
            confirm=payload.confirm_device_switch or state.get('cfm') == '1',
            purpose='login',
        )

    # ------------------------------------------------- email change (2-step)

    async def issue_email_change_challenge(self, *, user: User, new_email: str) -> Challenge:
        """Sends a code to the *new* address; records the pending value in Redis only."""
        return await self._issue_challenge(
            user=user, email=new_email, purpose='email_change', extra={'u': str(user.id), 'p': new_email}
        )

    async def read_challenge(self, *, sid: str, purpose: OtpPurpose) -> dict[str, str]:
        return await self._challenge_state(sid=sid, purpose=purpose)

    async def consume_email_change(self, *, sid: str, otp: str, user_id: uuid.UUID, identity: str) -> None:
        # Checked again here, not only in the caller: the challenge itself carries
        # the owner, so a mismatched pairing can never be spent.
        state = await get_otp_state(purpose='email_change', session_id=sid)
        if state.get('u') and state['u'] != str(user_id):
            raise AuthError('not_owner', 'This code belongs to another account.', status=401)
        await self._consume(sid=sid, purpose='email_change', otp=otp, identity=identity)

    async def resend(self, payload: OtpResendRequest) -> Challenge:
        purpose = payload.purpose
        if purpose == 'restore':
            raise AuthError('resend_not_allowed', 'Start this step again to get a new code.')
        state = await self._challenge_state(sid=payload.otp_session_id, purpose=purpose)
        email = state.get('id', '')
        if not email:
            raise AuthError('otp_expired', 'Start again to get a new code.', status=410)
        user = await self._repo.get_by_email_any(email)
        extra = {key: state[key] for key in _OTP_PENDING_KEYS if state.get(key)}
        return await self._issue_challenge(
            user=user,
            email=email,
            purpose=purpose,
            resend=True,
            session_id=payload.otp_session_id,
            extra=extra or None,
        )

    async def _register_failure(self, user: User, request: Request) -> None:
        count = user.failed_login_count + 1
        locked_until = None
        if count >= self._s.login_max_failed_attempts:
            locked_until = datetime.now(UTC) + timedelta(minutes=self._s.login_lock_minutes)
            log.warning('account_lockout_triggered', user_id=str(user.id), ip=client_ip(request))
        await self._repo.touch_login(user, success=False, locked_until=locked_until)
        await self._db.commit()

    # ------------------------------------------------------------- sessions

    async def _open_session(
        self,
        user: User,
        request: Request,
        *,
        remember_me: bool = False,
        confirm: bool = False,
        purpose: str = 'login',
    ) -> Issued:
        existing = await self._repo.active_session(user.id)
        if existing is not None:
            if not confirm:
                raise ConflictError(
                    'This account is already signed in on another device. Sign out there first, '
                    'or confirm to move this session here.',
                    code='already_signed_in',
                    details=_device_switch_details(existing.last_used_at.isoformat()),
                )
            await self._repo.revoke_session(existing, reason='device_switch')
            await drop_session_cache(session_id=str(existing.id))

        refresh = generate_refresh_token()
        extra = timedelta(days=self._s.remember_me_extra_days) if remember_me else timedelta(0)
        session = await self._repo.create_session(
            user_id=user.id,
            refresh_token_hash=hash_token(refresh),
            expires_at=refresh_token_expiry() + extra,
            user_agent=request.headers.get('user-agent'),
            ip_address=client_ip(request),
        )
        await self._repo.touch_login(user, success=True)
        await self._db.commit()

        token, perms = self._mint(user, session)
        remaining = int((session.expires_at - datetime.now(UTC)).total_seconds())
        await warm_session_caches(
            session_id=str(session.id),
            user_id=str(user.id),
            role=user.role.name,
            is_active=user.is_active,
            is_email_verified=user.is_email_verified,
            permissions=perms,
            pwd_epoch=self._epoch(user),
            token_version=user.token_version,
            profile=profile_snapshot(user),
        )
        log.info('session_opened', user_id=str(user.id), session_id=str(session.id), purpose=purpose)
        return Issued(
            access_token=token,
            refresh_token=refresh,
            session_id=session.id,
            expires_in=self._s.access_token_seconds,
            role=user.role.name,
            must_verify_email=not user.is_email_verified,
            cookie_max_age=max(remaining, 1),
        )

    @staticmethod
    def _epoch(user: User) -> str:
        value = user.password_changed_at
        if value is None:
            return ''
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return str(int(value.timestamp()))

    def _mint(self, user: User, session) -> tuple[str, tuple[str, ...]]:
        perms = tuple(sorted(p.code for p in user.role.permissions))
        token = create_access_token(
            user_id=user.id,
            session_id=session.id,
            role=user.role.name,
            pwd_epoch=user.password_changed_at,
            token_version=user.token_version,
        )
        return token, perms

    async def refresh(self, refresh_token: str, request: Request) -> Issued:
        session = await self._find_session_for_refresh(hash_token(refresh_token))
        now = datetime.now(UTC)
        if session is None or session.revoked_at is not None or session.expires_at <= now:
            if session is not None:
                await drop_session_cache(session_id=str(session.id))
            raise UnauthorizedError('Session ended, sign in again', code='session_revoked')

        user = await self._repo.get_by_id(session.user_id)
        if user is None:
            raise UnauthorizedError('Account no longer exists', code='account_gone')
        if user.password_changed_at is not None and user.password_changed_at > session.last_used_at:
            await self._repo.revoke_session(session, reason='password_change')
            await self._db.commit()
            await drop_session_cache(session_id=str(session.id))
            raise UnauthorizedError('Sign in again after changing your password', code='password_changed')

        refresh_new = generate_refresh_token()
        await self._repo.rotate_session(
            session, refresh_token_hash=hash_token(refresh_new), expires_at=session.expires_at
        )
        await self._db.commit()
        log.info('token_refreshed', user_id=str(user.id), ip=client_ip(request))
        token, perms = self._mint(user, session)
        # Rewrite (do not leave a hole): same session id, fresh TTL + profile.
        await warm_session_caches(
            session_id=str(session.id),
            user_id=str(user.id),
            role=user.role.name,
            is_active=user.is_active,
            is_email_verified=user.is_email_verified,
            permissions=perms,
            pwd_epoch=self._epoch(user),
            token_version=user.token_version,
            profile=profile_snapshot(user),
        )
        remaining = int((session.expires_at - now).total_seconds())
        return Issued(
            access_token=token,
            refresh_token=refresh_new,
            session_id=session.id,
            expires_in=self._s.access_token_seconds,
            role=user.role.name,
            must_verify_email=not user.is_email_verified,
            cookie_max_age=max(remaining, 1),
        )

    async def _find_session_for_refresh(self, token_hash: str):
        session = await self._repo.get_session_by_hash(token_hash)
        if session is not None:
            return session
        reused = await self._repo.get_session_by_previous_hash(token_hash)
        if reused is not None:
            # A rotated token came back: treat it as theft and kill the session.
            log.warning('refresh_token_reuse', user_id=str(reused.user_id), session_id=str(reused.id))
            await self._repo.revoke_session(reused, reason='token_reuse')
            await self._db.commit()
            await drop_session_cache(session_id=str(reused.id))
            raise UnauthorizedError(
                'This session was signed out for safety. Sign in again.', code='token_reuse_detected'
            )
        return None

    async def logout(self, *, user_id: uuid.UUID, session_id: uuid.UUID | None) -> None:
        if session_id is None:
            return
        session = await self._repo.get_session(session_id)
        if session is not None and session.user_id == user_id and session.revoked_at is None:
            await self._repo.revoke_session(session, reason='logout')
            await self._db.commit()
        await drop_session_cache(session_id=str(session_id))

    # ------------------------------------------------------------ passwords

    async def forgot_password(self, email: str) -> Challenge | None:
        user = await self._repo.get_by_email_any(email)
        if user is None or user.deleted_at is not None or user.password_hash is None:
            # Unknown, Google-only and deleted all answer the same way: no oracle.
            log.info('forgot_password_noop', email_masked=mask_email(email))
            return None
        return await self._issue_challenge(user=user, email=user.email, purpose='reset')

    async def reset_password(self, payload: PasswordResetRequest, request: Request) -> None:
        state = await self._challenge_state(sid=payload.otp_session_id, purpose='reset')
        user = await self._repo.get_by_id(uuid.UUID(state['u'])) if state.get('u') else None
        if user is None:
            raise AuthError('otp_expired', 'Request a new code.', status=410)
        await self._consume(
            sid=payload.otp_session_id, purpose='reset', otp=payload.otp, identity=state.get('id', '')
        )
        errors = password_policy_errors(payload.password, user.email)
        if errors:
            raise AuthError('weak_password', '; '.join(errors))
        await self._repo.set_password(
            user,
            password_hash=await hash_password_async(payload.password),
            keep_session_id=None,
            reason='password_change',
        )
        await self._db.commit()
        log.info('password_reset', user_id=str(user.id), ip=client_ip(request))

    async def change_password(
        self, user: User, payload: PasswordChangeRequest, session_id: uuid.UUID | None
    ) -> None:
        if user.password_hash is None or not await verify_password_async(
            payload.current_password, user.password_hash
        ):
            raise UnauthorizedError('Current password is incorrect', code='invalid_credentials')
        # Keep this device, drop every other one, and let password_changed_at
        # invalidate the tokens already issued for all of them.
        await self._repo.set_password(
            user,
            password_hash=await hash_password_async(payload.password),
            keep_session_id=session_id,
            reason='password_change',
        )
        await self._db.commit()
        if session_id is not None:
            await warm_session_caches(
                session_id=str(session_id),
                user_id=str(user.id),
                role=user.role.name,
                is_active=user.is_active,
                is_email_verified=user.is_email_verified,
                permissions=permission_codes(user),
                pwd_epoch=self._epoch(user),
                token_version=user.token_version,
                profile=profile_snapshot(user),
            )
        log.info('password_changed', user_id=str(user.id), kept_session=str(session_id or ''))

    # --------------------------------------------------------------- google

    async def google(self, id_token: str, request: Request) -> tuple[Issued, dict[str, Any]]:
        if not self._s.google_client_id:
            raise AuthError('google_not_configured', 'Google sign-in is not configured.', status=503)
        try:
            claims = google_id_token.verify_oauth2_token(
                id_token, google_requests.Request(), audience=self._s.google_client_id
            )
        except Exception as exc:
            log.info('google_token_rejected', error=type(exc).__name__)
            raise AuthError('invalid_google_token', 'Google token was rejected', status=401) from None

        email = str(claims.get('email') or '').lower()
        if not email or not claims.get('email_verified'):
            # Linking an address Google did not confirm would defeat OTP: refuse.
            raise AuthError('unverified_google_email', 'Google did not confirm this email', status=403)

        user, is_new_user = await self._google_user(
            email=email,
            provider_user_id=str(claims['sub']),
            first_name=(str(claims.get('given_name') or 'Guest'))[:100],
            last_name=(str(claims.get('family_name') or '-'))[:100],
            avatar_url=(str(claims.get('picture') or ''))[:500] or None,
        )
        await self._db.commit()
        # Google logins take the device slot without a second prompt: there is no
        # password step for the client to have confirmed a switch through.
        issued = await self._open_session(user, request, purpose='google', confirm=True)
        return issued, {'is_new_user': is_new_user, 'auth_provider': 'google'}

    async def _google_user(
        self, *, email: str, provider_user_id: str, first_name: str, last_name: str, avatar_url: str | None
    ) -> tuple[User, bool]:
        is_new_user = False
        account = await self._repo.find_google_account(provider_user_id)
        if account is not None:
            user = await self._repo.get_including_deleted(account.user_id)
            if user is None:  # pragma: no cover - FK protects this
                raise AuthError('account_gone', 'That Google account is no longer linked.', status=410)
            if user.deleted_at is not None:
                raise AuthError(
                    'account_deleted', 'Your account is deactivated. Use "Reactivate account".', status=403
                )
            return user, False

        user = await self._repo.get_by_email_any(email)
        if user is None:
            is_new_user = True
            user = await self._repo.create_google_user(
                email=email, first_name=first_name, last_name=last_name, avatar_url=avatar_url
            )
        elif user.deleted_at is not None:
            # Never silently resurrect, and never let a new account take the address.
            raise AuthError(
                'account_deleted', 'Your account is deactivated. Use "Reactivate account".', status=403
            )
        else:
            await self._repo.apply_google_profile_hints(
                user.id, first_name=first_name, last_name=last_name, avatar_url=avatar_url
            )
        await self._repo.link_google_account(user=user, provider_user_id=provider_user_id, email=email)
        return user, is_new_user

    # -------------------------------------------------------------- restore

    async def restore_request(self, email: str) -> Challenge | None:
        user = await self._repo.get_by_email_any(email)
        if user is None or user.deleted_at is None:
            return None
        return await self._issue_challenge(user=user, email=user.email, purpose='restore')

    async def restore_confirm(self, payload: OtpVerifyRequest, request: Request) -> Issued:
        state = await self._challenge_state(sid=payload.otp_session_id, purpose='restore')
        user = await self._repo.get_including_deleted(uuid.UUID(state['u'])) if state.get('u') else None
        if user is None:
            raise AuthError('otp_expired', 'Start the restore again.', status=410)
        await self._consume(
            sid=payload.otp_session_id, purpose='restore', otp=payload.otp, identity=state.get('id', '')
        )
        await self._repo.restore_user(user)
        await self._db.commit()
        log.info('account_restored', user_id=str(user.id))
        return await self._open_session(user, request, remember_me=True, purpose='restore', confirm=True)

    # ------------------------------------------------------------- responses

    def token_response(
        self, issued: Issued, *, status_code: int = 200, extra: dict[str, Any] | None = None
    ) -> JSONResponse:
        body: dict[str, Any] = {
            'access_token': issued.access_token,
            'token_type': 'bearer',
            'expires_in': issued.expires_in,
            'role': issued.role,
            'session_id': str(issued.session_id),
            'must_verify_email': issued.must_verify_email,
        }
        if extra:
            body.update(extra)
        response = JSONResponse(body, status_code=status_code)
        if issued.refresh_token:
            max_age = issued.cookie_max_age or (self._s.refresh_token_days * 86400)
            response.set_cookie(
                self._s.refresh_cookie_name,
                issued.refresh_token,
                max_age=max_age,
                httponly=True,
                secure=self._s.is_prod,
                samesite=self._s.refresh_cookie_samesite,
                path='/api/v1/auth',
            )
        return response

    def generic_challenge(self, purpose: str) -> dict[str, Any]:
        """The 'we will tell you nothing either way' answer for forgot/restore."""
        return {
            'otp_session_id': '',
            'purpose': purpose,
            'expires_in': 0,
            'resend_after': 0,
            'masked_destination': '•••',
            'hint': 'If an account matches, a code is on its way.',
        }

    def challenge_payload(self, challenge: Challenge) -> dict[str, Any]:
        return {
            'otp_session_id': challenge.ticket.session_id,
            'purpose': challenge.purpose,
            'expires_in': challenge.ticket.expires_in,
            'resend_after': get_settings().otp_resend_cooldown_seconds,
            'masked_destination': challenge.masked,
            'hint': 'We sent a 6-digit code to your email.',
        }


async def auth_error_handler(_request: Request, exc: AuthError) -> JSONResponse:
    from app.core.exceptions import AppError

    mapped = AppError(exc.message, code=exc.code)
    return JSONResponse({'error': {'code': mapped.code, 'message': mapped.message}}, status_code=exc.status)
