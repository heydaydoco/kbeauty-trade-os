"""API 라우터 루트. 버전 접두(/api/v1)는 여기 한 곳에서만 붙인다."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import system

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(system.router)
