import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.deps import CurrentUser
from app.core.exceptions import NotFoundError
from app.core.rate_limit import limit
from app.db.session import get_db
from app.modules.auth.repository import AuthRepository
from app.modules.auth.router import get_auth_service
from app.modules.auth.service import AuthService
from app.modules.users.repository import UsersRepository
from app.modules.users.schemas import (
    AccountDeleteRequest,
    EmailChangeConfirmRequest,
    EmailChangeStartRequest,
    ProfileUpdateRequest,
)
from app.modules.users.service import UsersService

router = APIRouter()

DbDep = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_users_service(db: DbDep) -> UsersService:
    return UsersService(UsersRepository(db), AuthRepository(db))


UsersS = Annotated[UsersService, Depends(get_users_service)]
AuthDep = Annotated[AuthService, Depends(get_auth_service)]


@router.get('/me')
async def read_me(user: CurrentUser, users: UsersS) -> dict:
    """The single call the SPA uses to decide routing, sidebar and banners."""
    return await users.profile_payload(user)


@router.patch('/me/profile', dependencies=[Depends(limit('profile:patch'))])
async def update_profile(payload: ProfileUpdateRequest, user: CurrentUser, users: UsersS) -> dict:
    account = await users.require_user(user.user_id)
    return (await users.update_profile(account, payload, session_id=user.session_id)).model_dump()


@router.post('/me/email', status_code=202, dependencies=[Depends(limit('profile:email'))])
async def start_email_change(
    payload: EmailChangeStartRequest, user: CurrentUser, users: UsersS, auth: AuthDep
) -> dict:
    """Step 1: prove the password; the code goes to the new address."""
    return await users.email_change_started(user, payload, auth)


@router.post('/me/email/confirm', dependencies=[Depends(limit('profile:email'))])
async def confirm_email_change(
    payload: EmailChangeConfirmRequest,
    request: Request,
    user: CurrentUser,
    users: UsersS,
    auth: AuthDep,
) -> JSONResponse:
    """Step 2: move login identity, revoke every old session, return fresh tokens.

    Old access + refresh tokens are disposed (session revoke + token_version bump).
    Response matches login (access_token + httpOnly refresh cookie) and includes
    ``profile`` so the SPA can update UI without an extra round trip.
    """
    issued, profile = await users.confirm_email_change(user, payload, auth, request)
    return auth.token_response(issued, extra={'profile': profile.model_dump()})


@router.get('/me/sessions')
async def list_sessions(user: CurrentUser, users: UsersS) -> dict:
    return await users.sessions_payload(user)


@router.delete('/me/sessions/{session_id}', status_code=204)
async def revoke_session(session_id: str, user: CurrentUser, users: UsersS) -> Response:
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise NotFoundError('No such session') from None
    await users.revoke_session_by_id(user, sid)
    return Response(status_code=204)


@router.delete('/me', dependencies=[Depends(limit('account:delete'))])
async def delete_me(
    payload: AccountDeleteRequest, user: CurrentUser, users: UsersS, settings: SettingsDep
) -> Response:
    """Self-deletion needs the password: a stolen access token must not be enough
    to close an account. Every session dies with it.
    """
    message = await users.delete_self(user, payload)
    response = JSONResponse({'message': message})
    response.delete_cookie(
        settings.refresh_cookie_name, path='/api/v1/auth', samesite=settings.refresh_cookie_samesite
    )
    return response
