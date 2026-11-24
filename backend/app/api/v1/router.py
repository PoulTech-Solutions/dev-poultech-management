from fastapi import APIRouter

from app.modules.admin.router import router as admin_router
from app.modules.auth.router import router as auth_router
from app.modules.geo.router import router as geo_router
from app.modules.market.router import router as market_router
from app.modules.mitra.router import router as mitra_router
from app.modules.users.router import router as users_router

api_router = APIRouter()

api_router.include_router(auth_router, prefix='/auth', tags=['auth'])
api_router.include_router(users_router, tags=['profile'])
api_router.include_router(admin_router)
api_router.include_router(market_router)
api_router.include_router(geo_router)
api_router.include_router(mitra_router)
