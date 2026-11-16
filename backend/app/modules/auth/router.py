from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.deps import CurrentUser
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.rate_limit import limit
from app.db.session import get_db
from app.modules.auth.repository import AuthRepository
from app.modules.auth.schemas import (
    ForgotPasswordRequest,
    GoogleLoginRequest,
    LoginRequest,
    MessageResponse,
    OtpResendRequest,
    OtpVerifyRequest,
    PasswordChangeRequest,
    PasswordResetRequest,
    RefreshRequest,
    RestoreRequest,
    SignupRequest,
)
from app.modules.auth.service import AuthService

router = APIRouter()

DbDep = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_auth_service(db: DbDep) -> AuthService:
    return AuthService(AuthRepository(db))


AuthS = Annotated[AuthService, Depends(get_auth_service)]


@router.post('/signup', status_code=202, dependencies=[Depends(limit('auth:signup'))])
async def signup(payload: SignupRequest, auth: AuthS) -> dict:
    """Create the account, then send the code that activates it.

    202 rather than 200: the row exists, but it cannot sign in until verified.
    """
    return auth.challenge_payload(await auth.signup(payload))


@router.post('/verify-email', dependencies=[Depends(limit('auth:verify'))])
async def verify_email(payload: OtpVerifyRequest, auth: AuthS) -> MessageResponse:
    await auth.verify_signup(payload)
    return MessageResponse(message='Email confirmed. You can sign in now.')


@router.post('/login', dependencies=[Depends(limit('auth:login'))])
async def login(payload: LoginRequest, request: Request, auth: AuthS) -> dict:
    """Password first, then an emailed code. Same response shape as /signup."""
    return auth.challenge_payload(await auth.login(payload, request))


@router.post('/verify-otp', dependencies=[Depends(limit('auth:verify'))])
async def verify_otp(payload: OtpVerifyRequest, request: Request, auth: AuthS) -> Response:
    return auth.token_response(await auth.login_with_otp(payload, request))


@router.post('/resend-otp', dependencies=[Depends(limit('auth:login'))])
async def resend_otp(payload: OtpResendRequest, auth: AuthS) -> dict:
    return auth.challenge_payload(await auth.resend(payload))


@router.post('/refresh')
async def refresh_token(
    request: Request,
    auth: AuthS,
    settings: SettingsDep,
    payload: RefreshRequest | None = None,
    x_poultech_refresh: Annotated[str | None, Header(alias='X-Poultech-Refresh')] = None,
) -> Response:
    """Issue a new access token (and rotate refresh).

    Pass the **refresh** token — never the access JWT.
    Prefer cookie ``poultech_refresh``; otherwise body ``refresh_token``.
    Header ``X-Poultech-Refresh: 1`` is required (CSRF guard).
    """
    if x_poultech_refresh != '1':
        raise ForbiddenError('Missing refresh header', code='csrf_guard')
    token = request.cookies.get(settings.refresh_cookie_name)
    if not token and payload is not None:
        token = (payload.refresh_token or '').strip() or None
    if not token:
        raise UnauthorizedError('No refresh token', code='missing_refresh')
    return auth.token_response(await auth.refresh(token, request))


@router.post('/logout')
async def logout(user: CurrentUser, auth: AuthS, settings: SettingsDep) -> Response:
    await auth.logout(user_id=user.user_id, session_id=user.session_id)
    response = Response(status_code=204)
    response.delete_cookie(
        settings.refresh_cookie_name, path='/api/v1/auth', samesite=settings.refresh_cookie_samesite
    )
    return response


@router.post('/forgot-password', dependencies=[Depends(limit('auth:forgot'))])
async def forgot_password(payload: ForgotPasswordRequest, auth: AuthS) -> dict:
    """Answers identically whether or not the address is registered."""
    challenge = await auth.forgot_password(payload.email)
    return auth.challenge_payload(challenge) if challenge else auth.generic_challenge('reset')


@router.post('/reset-password', dependencies=[Depends(limit('auth:verify'))])
async def reset_password(payload: PasswordResetRequest, request: Request, auth: AuthS) -> MessageResponse:
    await auth.reset_password(payload, request)
    return MessageResponse(message='Password updated. Sign in with your new password.')


@router.post('/password/change')
async def change_password(
    payload: PasswordChangeRequest, user: CurrentUser, auth: AuthS, db: DbDep
) -> MessageResponse:
    account = await AuthRepository(db).get_by_id(user.user_id)
    if account is None:  # pragma: no cover - the dependency already loaded it
        raise UnauthorizedError('Account no longer exists', code='account_gone')
    await auth.change_password(account, payload, user.session_id)
    return MessageResponse(message='Password changed. Other devices were signed out.')


@router.post('/google', dependencies=[Depends(limit('auth:google'))])
async def google_login(payload: GoogleLoginRequest, request: Request, auth: AuthS) -> Response:
    issued, meta = await auth.google(payload.id_token, request)
    return auth.token_response(issued, extra=meta)


@router.post('/restore', dependencies=[Depends(limit('auth:restore'))])
async def restore_request(payload: RestoreRequest, auth: AuthS) -> dict:
    challenge = await auth.restore_request(payload.email)
    return auth.challenge_payload(challenge) if challenge else auth.generic_challenge('restore')


@router.post('/restore/confirm', dependencies=[Depends(limit('auth:verify'))])
async def restore_confirm(payload: OtpVerifyRequest, request: Request, auth: AuthS) -> Response:
    return auth.token_response(await auth.restore_confirm(payload, request))
