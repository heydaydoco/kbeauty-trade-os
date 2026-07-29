"""API 라우터 루트. 버전 접두(/api/v1)는 여기 한 곳에서만 붙인다."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import system
from app.modules.catalog import router as catalog_router
from app.modules.handover import router as handover_router
from app.modules.identity import router as identity_router
from app.modules.worklist import router as worklist_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(system.router)
api_router.include_router(identity_router.router)
api_router.include_router(identity_router.users_router)
api_router.include_router(handover_router.router)
api_router.include_router(worklist_router.router)
api_router.include_router(catalog_router.brands_router)
api_router.include_router(catalog_router.products_router)
api_router.include_router(catalog_router.router)
