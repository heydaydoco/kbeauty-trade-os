"""API 라우터 루트. 버전 접두(/api/v1)는 여기 한 곳에서만 붙인다."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import system
from app.modules.approvals import router as approvals_router
from app.modules.bank_accounts import router as bank_accounts_router
from app.modules.catalog import router as catalog_router
from app.modules.certifications import router as certifications_router
from app.modules.collaboration import router as collaboration_router
from app.modules.credit import (
    spec as credit_spec,  # noqa: F401 — TargetSpec 등록 배선(approvals는 도메인을 모른다)
)
from app.modules.deadlines import router as deadlines_router
from app.modules.documents import router as documents_router
from app.modules.handover import router as handover_router
from app.modules.identity import router as identity_router
from app.modules.imports import router as imports_router
from app.modules.ingredients import router as ingredients_router
from app.modules.markets import router as markets_router
from app.modules.materials import router as materials_router
from app.modules.notifications import router as notifications_router
from app.modules.partners import router as partners_router
from app.modules.platform import router as platform_router
from app.modules.policies import router as policies_router
from app.modules.proforma_invoices import router as proforma_invoices_router
from app.modules.purchase_orders import router as purchase_orders_router
from app.modules.quotations import router as quotations_router
from app.modules.readiness import router as readiness_router
from app.modules.requirements import router as requirements_router
from app.modules.sales_orders import router as sales_orders_router
from app.modules.seeds import router as seeds_router
from app.modules.trade_chain import router as trade_chain_router
from app.modules.worklist import router as worklist_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(system.router)
api_router.include_router(identity_router.router)
api_router.include_router(identity_router.users_router)
api_router.include_router(handover_router.router)
api_router.include_router(worklist_router.router)
api_router.include_router(notifications_router.router)
api_router.include_router(notifications_router.rules_router)
api_router.include_router(platform_router.router)
api_router.include_router(policies_router.router)
api_router.include_router(approvals_router.lines_router)
api_router.include_router(approvals_router.router)
api_router.include_router(approvals_router.delegations_router)
api_router.include_router(quotations_router.router)
api_router.include_router(trade_chain_router.router)
api_router.include_router(trade_chain_router.pi_router)
api_router.include_router(trade_chain_router.so_router)
api_router.include_router(trade_chain_router.po_router)
api_router.include_router(trade_chain_router.flow_router)
api_router.include_router(sales_orders_router.router)
api_router.include_router(proforma_invoices_router.router)
api_router.include_router(purchase_orders_router.router)
api_router.include_router(bank_accounts_router.router)
api_router.include_router(catalog_router.brands_router)
api_router.include_router(catalog_router.item_profiles_router)
api_router.include_router(catalog_router.products_router)
api_router.include_router(catalog_router.router)
api_router.include_router(markets_router.router)
api_router.include_router(seeds_router.router)
api_router.include_router(seeds_router.wizard_router)
api_router.include_router(requirements_router.router)
api_router.include_router(requirements_router.profile_requirement_templates_router)
api_router.include_router(certifications_router.router)
api_router.include_router(collaboration_router.agencies_router)
api_router.include_router(collaboration_router.contracts_router)
api_router.include_router(collaboration_router.comm_logs_router)
api_router.include_router(readiness_router.router)
api_router.include_router(deadlines_router.router)
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
