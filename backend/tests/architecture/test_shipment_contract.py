"""I·K. 선적 계약 스캔 — 원장 무접촉·자동 선적 0·가용 '자리'·이벤트 화이트리스트·요청 스키마 (S3-2 PR-3a / ADR-0074·0075 / design-C C8·C12).

■ **stock_movements 무접촉**(검증 K 아키텍처 단언 — §15 4금 ④ 장부 확정): 선적 모듈·선적 오케스트레이션이 재고 원장 테이블·모듈을 언급하지 않는다.
■ **자동 선적 0**(I-05): SO 확정·인테이크·보드 벌크·스케줄러·CLI가 선적 생성·전이 모듈을 임포트하지 않는다.
■ §8.3 '자리'(X-26): `AllocationPort`는 무변경(쓰기 훅 2개뿐 — 읽기 메서드를 더하지 않았다), 응답 값은 `NOT_IMPLEMENTED` 하나.
■ 요청 스키마에는 원천 값(SKU·단가·통화·환율·거래처·Incoterms·결제조건·구분·증빙일) 필드가 구조적으로 없다(`D:175` ①·R-15).
스캔은 공회전하지 않도록 자기검사로 위반을 실제로 잡는다.
"""

from __future__ import annotations

import ast
import inspect
from importlib import import_module

import pytest

from app.modules.sales_orders import ports
from app.modules.shipments import schemas
from tests.support.astscan import app_sources, imported_modules, parse_source

pytestmark = pytest.mark.group_k

SHIPMENT_FILES = {
    "modules/shipments/models.py",
    "modules/shipments/schemas.py",
    "modules/shipments/service.py",
    "modules/trade_chain/shipment_flow.py",
    "modules/trade_chain/shipment_view.py",
    "modules/trade_chain/shipment_router.py",
    # S3-2 PR-4a — 마일스톤·통관 쓰기·조립·라우터
    "modules/trade_chain/milestone_flow.py",
    "modules/trade_chain/customs_flow.py",
    "modules/trade_chain/milestone_view.py",
    "modules/trade_chain/milestone_router.py",
    # S3-2 PR-4c — 품목군 마일스톤 세트 쓰기
    "modules/trade_chain/milestone_set_flow.py",
}
LEDGER_WORDS = ("stock_movements", "stock_movement", "StockMovement", "inventory_ledger")


def _ledger_mentions(source: str) -> list[str]:
    return [word for word in LEDGER_WORDS if word in source]


def test_shipment_code_never_touches_the_stock_ledger() -> None:
    """검증 K — 선적 모듈·오케스트레이션 11파일(PR-4a 마일스톤·통관 4·PR-4c 세트 1 포함)에 재고 원장(stock_movements) 언급·임포트 0(출고·선적 = RESERVED, 원장 기록은 S4-2)"""
    sources = app_sources()
    assert set(sources) >= SHIPMENT_FILES
    for rel in SHIPMENT_FILES:
        text = inspect.getsource(import_module(f"app.{rel[:-3].replace('/', '.')}"))
        assert _ledger_mentions(text) == [], rel
        assert not {m for m in imported_modules(sources[rel]) if "stock" in m or "inventory" in m}


def test_the_ledger_scan_is_not_vacuous() -> None:
    """자기검사 — 원장 언급을 실제로 잡는다"""
    assert _ledger_mentions("session.add(StockMovement(...)) # stock_movements")


def _imports_shipment_flow(tree: ast.Module) -> bool:
    """`from app.modules.trade_chain import shipment_flow`·`from app.modules.trade_chain.shipment_flow import …`·`import …shipment_flow`."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "app.modules.trade_chain.shipment_flow":
                return True
            if node.module == "app.modules.trade_chain" and any(
                alias.name == "shipment_flow" for alias in node.names
            ):
                return True
        elif isinstance(node, ast.Import) and any(
            alias.name == "app.modules.trade_chain.shipment_flow" for alias in node.names
        ):
            return True
    return False


@pytest.mark.group_i
def test_no_automatic_path_imports_the_shipment_orchestration() -> None:
    """I-05 — 선적 생성·전이 모듈을 임포트하는 앱 파일은 선적 라우터 1곳뿐이다(SO 확정·인테이크·보드·스케줄러·CLI·이관·알림 경로 0 — 자동 선적 0)"""
    importers = {rel for rel, tree in app_sources().items() if _imports_shipment_flow(tree)}
    assert importers == {"modules/trade_chain/shipment_router.py"}, importers


def test_the_import_scan_is_not_vacuous() -> None:
    """자기검사 — 세 가지 임포트 형태를 전부 잡는다"""
    for source in (
        "from app.modules.trade_chain import shipment_flow\n",
        "from app.modules.trade_chain.shipment_flow import create_shipment_from_sales_order\n",
        "import app.modules.trade_chain.shipment_flow\n",
    ):
        assert _imports_shipment_flow(parse_source(source)), source
    assert not _imports_shipment_flow(
        parse_source("from app.modules.trade_chain import lifecycle\n")
    )


def test_the_allocation_port_is_unchanged_and_the_placeholder_is_not_implemented() -> None:
    """X-26 — AllocationPort는 확정·취소 훅 2개뿐(읽기 메서드 미추가), 가용 '자리' 값은 NOT_IMPLEMENTED 하나(0·현재고 표시 금지)"""
    from app.modules.trade_chain.shipment_view import availability_of

    methods = {
        name
        for name, _ in inspect.getmembers(ports.AllocationPort, predicate=inspect.isfunction)
        if not name.startswith("_")
    }
    assert methods == {"on_confirmed", "on_cancelled"}
    assert {s.value for s in ports.AllocationStatus} == {"NOT_IMPLEMENTED"}
    assert availability_of() == {"status": "NOT_IMPLEMENTED"}


#: 요청 스키마에 있으면 안 되는 원천 값 필드(재입력 금지 `D:175` ① — 서버가 원천에서 복사한다).
SOURCE_FIELDS = {
    "sku_id",
    "unit_price",
    "unit_price_amount",
    "currency",
    "fx_rate",
    "fx_rate_date",
    "buyer_partner_id",
    "counterparty_partner_id",
    "counterparty_name",
    "incoterm",
    "payment_terms",
    "payment_type",
    "shipment_kind",
    "doc_date",
    "status",
    "doc_number",
    "total_amount",
    "line_amount",
    "so_id",
    "po_id",
}


def test_write_schemas_carry_no_source_values_and_forbid_extras() -> None:
    """요청 스키마 17종(PR-4a 마일스톤·통관 7·PR-4c 세트 1 포함) — extra=forbid이고 원천 값·상태·번호·합계 필드가 없다(생성 본문은 원천 라인 id·수량·국가·당사자·메모뿐)"""
    requests = [
        schemas.ShipmentCreateFromSo,
        schemas.ShipmentLineFromSo,
        # S3-2 PR-5a — PO 참조 수입선적 생성(원천 라인 = PO 라인 id·수량뿐 — 단가·원가·통화 필드 구조적 부재)
        schemas.ShipmentCreateFromPo,
        schemas.ShipmentLineFromPo,
        schemas.PartyIn,
        schemas.ShipmentUpdateRequest,
        schemas.ShipmentLineAddRequest,
        schemas.ShipmentLineUpdateRequest,
        schemas.ShipmentVersionRequest,
        schemas.ShipmentTransitionRequest,
        schemas.PartyAddRequest,
        # S3-2 PR-4a — 마일스톤·통보·통관 요청(원천 값·상태·금액 필드 없음)
        schemas.MilestonePlanRequest,
        schemas.MilestoneActualRequest,
        schemas.MilestonePlanDraftRequest,
        schemas.MilestoneNoticeRequest,
        schemas.CustomsRecordCreateRequest,
        schemas.CustomsRecordUpdateRequest,
        schemas.CustomsRecordDeleteRequest,
        # S3-2 PR-4c — 품목군 마일스톤 세트 추가(종류 1필드)
        schemas.ProfileMilestoneTypeAddRequest,
    ]
    for model in requests:
        assert model.model_config.get("extra") == "forbid", model.__name__
        assert not set(model.model_fields) & SOURCE_FIELDS, (
            model.__name__,
            set(model.model_fields) & SOURCE_FIELDS,
        )
    assert set(schemas.ShipmentCreateFromSo.model_fields) == {
        "lines",
        "origin_country_code",
        "dest_country_code",
        "parties",
        "internal_note",
    }
    assert set(schemas.ShipmentCreateFromPo.model_fields) == set(
        schemas.ShipmentCreateFromSo.model_fields
    )
    assert set(schemas.ShipmentLineFromPo.model_fields) == {"po_line_id", "quantity"}


def test_shipment_responses_carry_no_cost_fields() -> None:
    """K-06 — 선적 응답 스키마 전 필드에 원가·마진 계열 이름이 없다(판매가 축만 — 수입선적 원가 비복사, ADR-0024)"""
    from pydantic import BaseModel

    def names(model: type[BaseModel], seen: set[type]) -> set[str]:
        if model in seen:
            return set()
        seen.add(model)
        found = set(model.model_fields)
        for field in model.model_fields.values():
            annotation = field.annotation
            for candidate in (*getattr(annotation, "__args__", ()), annotation):
                inner = getattr(candidate, "__args__", ())
                for item in (candidate, *inner):
                    if isinstance(item, type) and issubclass(item, BaseModel):
                        found |= names(item, seen)
        return found

    for model in (
        schemas.ExportShipmentDetail,
        schemas.ImportShipmentDetail,
        schemas.ExportShipmentListItem,
        schemas.ImportShipmentListItem,
        schemas.ShipmentPreview,
        schemas.ImportShipmentPreview,
        schemas.MilestoneBoardOut,
        schemas.MilestoneWriteOut,
        schemas.MilestoneChangeOut,
        schemas.CustomsRecordOut,
        # S3-2 PR-4c — OEM 생산 일정 보드·쓰기 응답(PO 소유지만 원가 키 0)·세트 행
        schemas.OemMilestoneBoardOut,
        schemas.OemMilestoneWriteOut,
        schemas.ProfileMilestoneTypeOut,
    ):
        fields = names(model, set())
        assert not {f for f in fields if "cost" in f or "margin" in f or "purchase" in f}, model


def test_the_scanners_catch_a_synthetic_violation() -> None:
    """자기검사 — 원장 임포트 문장을 실제로 잡는다"""
    tree = parse_source("from app.modules.stock import ledger\n")
    assert {m for m in imported_modules(tree) if "stock" in m}


# ── G3 구조(S3-2 PR-5a) — 선적 코드는 PO 원가 열에 닿을 수단이 없다 ─────────────────────────────

#: PO 원가 열 이름(`PurchaseLineMixin`·PO 헤더 — ADR-0057). 선적 코드의 식별자·문자열 상수로 나타나면 원가를 고르는 문장이 생길 수 있다.
PO_COST_NAMES = frozenset({"unit_cost", "line_cost", "total_cost", "price_basis"})


def _cost_name_uses(tree: ast.Module) -> set[str]:
    """식별자(이름·속성)·**정확히 일치하는** 문자열 상수(`column("unit_cost")`)로 쓰인 원가 열 이름 — 주석·설명 문장은 대상 아님."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in PO_COST_NAMES:
            found.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in PO_COST_NAMES:
            found.add(node.attr)
        elif isinstance(node, ast.Constant) and node.value in PO_COST_NAMES:
            found.add(str(node.value))
        elif isinstance(node, ast.keyword) and node.arg in PO_COST_NAMES:
            found.add(str(node.arg))
    return found


@pytest.mark.group_g
def test_shipment_code_cannot_select_po_cost_columns() -> None:
    """GC-G3 구조 — 선적 모듈·오케스트레이션(수입선적 생성·응답 조립 포함)과 PO 라인 입고예정 조회(L0 `receipts`)는 **PO 모델·서비스를 임포트하지 않고**
    PO 원가 열 이름(`unit_cost`·`line_cost`·`total_cost`·`price_basis`)을 식별자·열 이름 상수로 쓰지 않는다 — 원가를 복사·조인하는 문장이
    구조적으로 생길 수 없다(ADR-0024 10번째 채널 미개설). PO는 테이블 이름으로 원가 아닌 열만 고른다"""
    sources = app_sources()
    targets = sorted(SHIPMENT_FILES | {"modules/trade_docs/receipts.py"})
    for rel in targets:
        tree = sources[rel]
        assert "purchase_orders" not in imported_modules(tree), rel
        assert _cost_name_uses(tree) == set(), (rel, _cost_name_uses(tree))
    # 공회전 방지 — 수입선적 생성 파일은 실제로 PO 표를 (원가 아닌 열로) 읽는다
    flow = ast.unparse(sources["modules/trade_chain/shipment_flow.py"])
    assert '"purchase_order_lines"' in flow.replace("'", '"') and "po_line_id" in flow


def test_the_cost_name_scan_is_not_vacuous() -> None:
    """자기검사 — 열 이름 상수·속성·키워드 인자로 원가 열을 고르는 코드를 잡고, 설명 문장 속 낱말은 무시한다"""
    bad = parse_source(
        "t = table('purchase_order_lines', column('unit_cost'))\n"
        "x = t.c.line_cost\nf(total_cost=1)\nprice_basis = 2\n"
    )
    assert _cost_name_uses(bad) == PO_COST_NAMES
    assert _cost_name_uses(parse_source('"""unit_cost는 고르지 않는다"""\n')) == set()
    assert "purchase_orders" in imported_modules(
        parse_source("from app.modules.purchase_orders.models import X\n")
    )
