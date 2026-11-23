from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_permissions
from app.core.rate_limit import limit
from app.db.session import get_db
from app.modules.admin.repository import AdminUsersRepository
from app.modules.admin.schemas import (
    AdminConfirmUserRequest,
    AdminCreateUserRequest,
    AdminUserItem,
    AdminUserListResponse,
    AdminUserStatusRequest,
)
from app.modules.admin.service import AdminUsersService
from app.modules.auth.repository import AuthRepository

router = APIRouter(prefix='/admin', tags=['admin'])

DbDep = Annotated[AsyncSession, Depends(get_db)]
AdminReader = Annotated[CurrentUser, Depends(require_permissions('admin:users:read'))]
AdminWriter = Annotated[CurrentUser, Depends(require_permissions('admin:users:manage'))]


async def get_admin_users_service(db: DbDep) -> AdminUsersService:
    return AdminUsersService(AdminUsersRepository(db), AuthRepository(db))


AdminS = Annotated[AdminUsersService, Depends(get_admin_users_service)]


@router.get('/users', dependencies=[Depends(limit('admin:read'))])
async def list_internal_users(
    user: AdminReader,
    admin: AdminS,
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0, le=100_000),
) -> AdminUserListResponse:
    return await admin.list_users(user, limit=limit, offset=offset)


@router.post('/users', status_code=202, dependencies=[Depends(limit('admin:write'))])
async def start_internal_user(
    payload: AdminCreateUserRequest, user: AdminWriter, admin: AdminS
) -> dict:
    """Send an OTP to the employee email. Account is created on /users/verify."""
    return await admin.start_create(user, payload)


@router.post('/users/verify', status_code=201, dependencies=[Depends(limit('admin:write'))])
async def confirm_internal_user(
    payload: AdminConfirmUserRequest, user: AdminWriter, admin: AdminS
) -> AdminUserItem:
    return await admin.confirm_create(user, payload)


@router.patch('/users/{user_id}', dependencies=[Depends(limit('admin:write'))])
async def set_internal_user_status(
    user_id: uuid.UUID, payload: AdminUserStatusRequest, user: AdminWriter, admin: AdminS
) -> AdminUserItem:
    return await admin.set_active(user, user_id, active=payload.is_active)


@router.delete('/users/{user_id}', status_code=204, dependencies=[Depends(limit('admin:write'))])
async def delete_internal_user(user_id: uuid.UUID, user: AdminWriter, admin: AdminS) -> Response:
    await admin.delete_user(user, user_id)
    return Response(status_code=204)
