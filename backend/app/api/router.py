"""API 라우터 루트. 버전 접두(/api/v1)는 여기 한 곳에서만 붙인다."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import system
from app.modules.catalog import router as catalog_router
from app.modules.certifications import router as certifications_router
from app.modules.documents import router as documents_router
from app.modules.handover import router as handover_router
from app.modules.identity import router as identity_router
from app.modules.imports import router as imports_router
from app.modules.ingredients import router as ingredients_router
from app.modules.markets import router as markets_router
from app.modules.materials import router as materials_router
from app.modules.partners import router as partners_router
from app.modules.requirements import router as requirements_router
from app.modules.worklist import router as worklist_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(system.router)
api_router.include_router(identity_router.router)
api_router.include_router(identity_router.users_router)
api_router.include_router(handover_router.router)
api_router.include_router(worklist_router.router)
api_router.include_router(catalog_router.brands_router)
api_router.include_router(catalog_router.item_profiles_router)
api_router.include_router(catalog_router.products_router)
api_router.include_router(catalog_router.router)
api_router.include_router(markets_router.router)
api_router.include_router(requirements_router.router)
api_router.include_router(requirements_router.profile_requirement_templates_router)
api_router.include_router(certifications_router.router)
api_router.include_router(ingredients_router.router)
api_router.include_router(ingredients_router.product_ingredients_router)
api_router.include_router(ingredients_router.ingredient_rules_router)
api_router.include_router(materials_router.router)
api_router.include_router(materials_router.product_boms_router)
api_router.include_router(materials_router.sku_labels_router)
api_router.include_router(materials_router.labels_router)
api_router.include_router(materials_router.bom_export_router)
api_router.include_router(imports_router.router)
api_router.include_router(partners_router.router)
api_router.include_router(partners_router.signatories_router)
api_router.include_router(documents_router.router)
api_router.include_router(documents_router.document_types_router)
api_router.include_router(documents_router.profile_document_types_router)
