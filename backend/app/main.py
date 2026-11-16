import time
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import setup_logging
from app.core.redis_conn import close_redis, get_redis
from app.db.session import engine
from app.modules.auth.service import AuthError, auth_error_handler

settings = get_settings()
setup_logging(settings.log_level, json_logs=settings.is_prod)
log = structlog.get_logger()

SAFE_PATHS = {'/health/live', '/health/ready'}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    log.info('app_starting', env=settings.app_env)
    yield
    await close_redis()
    await engine.dispose()
    log.info('app_stopped')


app = FastAPI(
    title='Poultech API',
    version='0.1.0',
    lifespan=lifespan,
    docs_url=None if settings.is_prod else '/docs',
    redoc_url=None,
    openapi_url=None if settings.is_prod else '/openapi.json',
    swagger_ui_parameters={'persistAuthorization': True},
)

register_exception_handlers(app)
app.add_exception_handler(AuthError, auth_error_handler)  # type: ignore[arg-type]

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=['GET', 'POST', 'PATCH', 'DELETE'],
    allow_headers=['Authorization', 'Content-Type', 'X-Request-ID', 'X-Poultech-Refresh'],
)


@app.middleware('http')
async def request_context(request: Request, call_next):
    request_id = request.headers.get('x-request-id') or uuid.uuid4().hex[:32]
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        request_id=request_id, method=request.method, path=request.url.path
    )
    start = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
    response.headers['X-Request-ID'] = request_id
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cache-Control'] = 'no-store'
    if settings.is_prod and request.url.scheme == 'https':
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    if request.url.path not in SAFE_PATHS:
        log.info('request_completed', status=response.status_code, duration_ms=duration_ms)
    return response


@app.get('/health/live', tags=['health'])
async def live():
    return {'status': 'ok'}


@app.get('/health/ready', tags=['health'])
async def ready():
    checks = {}
    try:
        async with engine.connect() as conn:
            await conn.execute(text('SELECT 1'))
        checks['db'] = 'ok'
    except Exception:
        log.error('readiness_db_failed', exc_info=True)
        checks['db'] = 'fail'
    try:
        await get_redis().ping()
        checks['redis'] = 'ok'
    except Exception:
        log.error('readiness_redis_failed', exc_info=True)
        checks['redis'] = 'fail'

    healthy = all(v == 'ok' for v in checks.values())
    return JSONResponse(
        {'status': 'ready' if healthy else 'degraded', **checks},
        status_code=200 if healthy else 503,
    )


app.include_router(api_router, prefix='/api/v1')
