from collections.abc import Awaitable, Callable, Mapping
from typing import Any, cast

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

log = structlog.get_logger()


class AppError(Exception):
    """Base class for all expected business errors."""

    status_code = 400
    code = 'bad_request'
    message = 'Bad request'

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details=None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.details = details
        self.headers = dict(headers) if headers else None
        super().__init__(self.message)


class UnauthorizedError(AppError):
    status_code = 401
    code = 'unauthorized'
    message = 'Authentication required'


class ForbiddenError(AppError):
    status_code = 403
    code = 'forbidden'
    message = 'You do not have permission'


class NotFoundError(AppError):
    status_code = 404
    code = 'not_found'
    message = 'Resource not found'


class ConflictError(AppError):
    status_code = 409
    code = 'conflict'
    message = 'Conflict'


class RateLimitError(AppError):
    status_code = 429
    code = 'rate_limited'
    message = 'Too many requests, try again later'


def _body(code: str, message: str, details=None) -> dict:
    error = {
        'code': code,
        'message': message,
        'request_id': structlog.contextvars.get_contextvars().get('request_id'),
    }
    if details is not None:
        error['details'] = details
    return {'error': error}


# A unique constraint is the only race-proof way to claim an email or a number.
# The database message is never returned: only this map is.
_CONSTRAINT_MESSAGES = {
    'uq_users_email_any': ('email', 'Email is already registered'),
    'uq_user_profiles_phone_e164': ('phone', 'This mobile number is already in use'),
    'uq_user_profiles_user_id': ('user', 'Profile already exists for this account'),
    'uq_user_sessions_one_active': ('session', 'This account is already signed in on another device'),
    'uq_user_sessions_refresh_token_hash': ('session', 'Session conflict, sign in again'),
    'ck_users_active_not_deleted': ('account', 'Reactivate your account to continue'),
    'uq_market_poultry_daily': ('rate', 'A rate for this city, product, and day already exists'),
}


def _constraint_name(exc: IntegrityError) -> str:
    text = str(exc.orig)
    for marker in (
        'duplicate key value violates unique constraint "',
        'violates check constraint "',
        'UNIQUE constraint failed: ',
        'CHECK constraint failed: ',
    ):
        if marker in text:
            tail = text.split(marker, 1)[1]
            return tail.split('"', 1)[0].split(':', 1)[-1].strip().lstrip('(').rstrip(')').split(', ')[-1]
    return ''


async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        _body(exc.code, exc.message, exc.details), status_code=exc.status_code, headers=exc.headers
    )


async def http_error_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        _body('http_error', str(exc.detail)), status_code=exc.status_code, headers=exc.headers
    )


async def integrity_error_handler(_request: Request, exc: IntegrityError) -> JSONResponse:
    name = _constraint_name(exc)
    log.warning('integrity_error', constraint=name or 'unknown', exc_info=exc)
    field, message = _CONSTRAINT_MESSAGES.get(
        name, ('detail', 'This value is already taken or no longer valid')
    )
    return JSONResponse(_body('conflict', message, [{'field': field, 'message': message}]), status_code=409)


async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    # Only field and message are returned, never the submitted values (could be passwords).
    details = [
        {'field': '.'.join(str(p) for p in e['loc'] if p != 'body'), 'message': e['msg']}
        for e in exc.errors()
    ]
    return JSONResponse(_body('validation_error', 'Invalid request data', details), status_code=422)


async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    log.error('unhandled_exception', exc_info=exc)
    return JSONResponse(_body('internal_error', 'Something went wrong'), status_code=500)


def register_exception_handlers(app: FastAPI) -> None:
    """Starlette's stubs type handlers as taking a bare Exception; ours narrow it,
    which is what FastAPI actually calls. One cast here beats five at the callsites.
    """
    register = cast(
        'Callable[[type[BaseException], Callable[[Request, Any], Awaitable[JSONResponse]]], None]',
        app.add_exception_handler,
    )
    register(AppError, app_error_handler)
    register(StarletteHTTPException, http_error_handler)
    register(IntegrityError, integrity_error_handler)
    register(RequestValidationError, validation_error_handler)
    register(Exception, unhandled_error_handler)
