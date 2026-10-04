"""열 분류 레지스트리 `FIELD_POLICY` — 동결 계약의 유일 정본 (S3-1 ADR-0053 / design-B B2 / X-04).

전표 헤더·라인 테이블(QT·PI·SO·PO·선적 — S3-2 PR-3a)의 **모든 컬럼**을 정확히 하나로 등재한다(미등재·유령 = CI 실패, fail-closed):

  CONTENT  편집 가능 상태에서만 수정, **동결 후 불변**(거래처·통화·환율·결제조건·Incoterms·유효기간·라인 전 컬럼…)
  ORIGIN   생성 시 1회 결정, 이후 어느 상태에서도 불변(참조 FK·복제 원본)
  FREE     동결 후에도 편집 가능 — **정확히 4개**: internal_note·assignee_id·oc_reference·oc_received_on
  SYSTEM   서비스 통로 외 대입 불가(id·doc_number·status·version·동결 시각·감사 컬럼·deleted_at·합계 카운터)

동결 열 집합(`FROZEN_*`)은 수기 정의하지 않고 이 표에서 **파생**한다(CONTENT+ORIGIN). 새 CONTENT 컬럼을 더하는
후속 세션은 분류를 강제당한다(조용한 미동결 불가). DB 트리거·동결 해시는 채택하지 않았다(ADR-0053 — 트리거는 인계
일괄 UPDATE 예외 로직·plpgsql 부담, 해시는 스키마 진화 시 기존 행 전부 불일치).
"""

from __future__ import annotations

from enum import StrEnum


class ColumnClass(StrEnum):
    CONTENT = "CONTENT"
    ORIGIN = "ORIGIN"
    FREE = "FREE"
    SYSTEM = "SYSTEM"


C = ColumnClass.CONTENT
ORG = ColumnClass.ORIGIN
F = ColumnClass.FREE
S = ColumnClass.SYSTEM

#: 동결 후에도 바뀔 수 있는 FREE 열 전체 — 집합이 늘려면 ADR이 필요하다(핀 테스트가 정확히 4개를 고정).
FREE_COLUMNS: frozenset[str] = frozenset(
    {"internal_note", "assignee_id", "oc_reference", "oc_received_on"}
)

_AUDIT_SYSTEM = {
    "id": S,
    "created_at": S,
    "updated_at": S,
    "deleted_at": S,
    "created_by_id": S,
    "updated_by_id": S,
}

FIELD_POLICY: dict[str, dict[str, ColumnClass]] = {
    "quotations": {
        **_AUDIT_SYSTEM,
        "version": S,
        "doc_number": S,
        "status": S,
        "frozen_at": S,
        "last_line_no": S,
        "doc_date": C,
        "currency": C,
        "total_amount": C,
        "fx_rate": C,
        "fx_rate_date": C,
        "payment_type": C,
        "advance_pct_bp": C,
        "balance_anchor": C,
        "balance_days": C,
        "incoterm_code": C,
        "incoterm_place": C,
        "incoterm_year": C,
        "buyer_partner_id": C,  # QT는 DRAFT에서 거래처 변경 가능(PI·SO에서는 ORIGIN)
        "buyer_name": C,
        "dest_market_code": C,
        "buyer_address": C,
        "valid_until": C,
        "copied_from_id": ORG,
        "internal_note": F,
        "assignee_id": F,
    },
    "quotation_lines": {
        **_AUDIT_SYSTEM,
        "qt_id": ORG,
        "currency": C,
        "line_no": C,
        "sku_id": C,
        "sku_code": C,
        "sku_name_ko": C,
        "sku_name_en": C,
        "sku_kind": C,
        "quantity": C,
        "buyer_item_code": C,
        "unit_price_amount": C,
        "list_price_amount": C,
        "line_amount": C,
        "price_basis": C,
        "is_free": C,
        "price_reason": C,
    },
    # PI — 생성 = 발행 = 동결이라 편집 구간이 없다(EDITABLE_STATES 공집합). 그래서 CONTENT는 처음부터 잠겨 있고
    # 서비스가 바꿀 수 있는 것은 FREE 두 열뿐이다. 참조 FK·거래처는 ORIGIN(QT에서 복사 후 불변).
    "proforma_invoices": {
        **_AUDIT_SYSTEM,
        "version": S,
        "doc_number": S,
        "status": S,
        "frozen_at": S,
        "last_line_no": S,
        "doc_date": C,
        "currency": C,
        "total_amount": C,
        "fx_rate": C,
        "fx_rate_date": C,
        "payment_type": C,
        "advance_pct_bp": C,
        "balance_anchor": C,
        "balance_days": C,
        "incoterm_code": C,
        "incoterm_place": C,
        "incoterm_year": C,
        "buyer_name": C,
        "dest_market_code": C,
        "buyer_address": C,
        "valid_until": C,
        "bank_account_id": C,
        "bank_beneficiary_name": C,
        "bank_beneficiary_address": C,
        "bank_name": C,
        "bank_address": C,
        "bank_account_no": C,
        "bank_swift_code": C,
        "qt_id": ORG,
        "buyer_partner_id": ORG,
        "copied_from_id": ORG,
        "internal_note": F,
        "assignee_id": F,
    },
    "proforma_invoice_lines": {
        **_AUDIT_SYSTEM,
        "pi_id": ORG,
        "qt_id": ORG,
        "qt_line_id": ORG,
        "currency": C,
        "line_no": C,
        "sku_id": C,
        "sku_code": C,
        "sku_name_ko": C,
        "sku_name_en": C,
        "sku_kind": C,
        "quantity": C,
        "buyer_item_code": C,
        "unit_price_amount": C,
        "list_price_amount": C,
        "line_amount": C,
        "price_basis": C,
        "is_free": C,
        "price_reason": C,
    },
    # SO — 편집 가능 구간은 RECEIVED 하나뿐이고 동결 표식은 `confirmed_at` 하나다(X-03). **거래처·참조 FK·복제 원본은 ORIGIN**
    # (여신 잠금이 "거래처→SO" 순서를 지키려면 잠금 전에 읽을 수 있어야 한다 — X-09). PO번호 원문·키·일자·요청납기는 CONTENT.
    "sales_orders": {
        **_AUDIT_SYSTEM,
        "version": S,
        "doc_number": S,
        "status": S,
        "confirmed_at": S,
        "credit_verdict": S,
        "credit_approval_id": S,
        "pi_gate_verdict": S,
        "last_line_no": S,
        "doc_date": C,
        "currency": C,
        "total_amount": C,
        "fx_rate": C,
        "fx_rate_date": C,
        "payment_type": C,
        "advance_pct_bp": C,
        "balance_anchor": C,
        "balance_days": C,
        "incoterm_code": C,
        "incoterm_place": C,
        "incoterm_year": C,
        "buyer_name": C,
        "dest_market_code": C,
        "buyer_po_no": C,
        "buyer_po_no_key": C,
        "buyer_po_date": C,
        "qt_id": ORG,
        "pi_id": ORG,
        "buyer_partner_id": ORG,
        "copied_from_id": ORG,
        "internal_note": F,
        "assignee_id": F,
    },
    "sales_order_lines": {
        **_AUDIT_SYSTEM,
        "so_id": ORG,
        "qt_line_id": ORG,
        "pi_line_id": ORG,
        "currency": C,
        "line_no": C,
        "sku_id": C,
        "sku_code": C,
        "sku_name_ko": C,
        "sku_name_en": C,
        "sku_kind": C,
        "quantity": C,
        "requested_delivery_date": C,
        "buyer_item_code": C,
        "unit_price_amount": C,
        "list_price_amount": C,
        "line_amount": C,
        "price_basis": C,
        "is_free": C,
        "price_reason": C,
    },
    # PO — 생성 = 발행 = 동결이라 편집 구간이 없다(EDITABLE_STATES 공집합). 원가 열(`unit_cost`·`line_cost`·`total_cost`)은 전부 CONTENT(처음부터
    # 잠김)이고, 공급사·참조 FK는 ORIGIN이다. **`po_kind`는 CONTENT**(생성 시 동결, 생성 후 불변 — F3). FREE는 메모·담당자·OC 두 열(정확히 4종).
    "purchase_orders": {
        **_AUDIT_SYSTEM,
        "version": S,
        "doc_number": S,
        "status": S,
        "frozen_at": S,
        "last_line_no": S,
        "doc_date": C,
        "currency": C,
        "total_cost": C,
        "fx_rate": C,
        "fx_rate_date": C,
        "payment_type": C,
        "advance_pct_bp": C,
        "balance_anchor": C,
        "balance_days": C,
        "incoterm_code": C,
        "incoterm_place": C,
        "incoterm_year": C,
        "supplier_name": C,
        "po_kind": C,
        "supplier_partner_id": ORG,
        "copied_from_id": ORG,
        "internal_note": F,
        "assignee_id": F,
        "oc_received_on": F,
        "oc_reference": F,
    },
    "purchase_order_lines": {
        **_AUDIT_SYSTEM,
        "po_id": ORG,
        "currency": C,
        "line_no": C,
        "sku_id": C,
        "sku_code": C,
        "sku_name_ko": C,
        "sku_name_en": C,
        "sku_kind": C,
        "quantity": C,
        "requested_delivery_date": C,
        "unit_cost": C,
        "line_cost": C,
        "price_basis": C,
    },
    # 선적(S3-2 PR-3a / design-integrated §2.1 (a)(b) / R-15) — **원천 사본은 전부 ORIGIN**(통화·환율·결제조건·Incoterms·거래 상대·구분·원천 FK):
    # 생성 시 원천(확정 SO·발행 PO)에서 복사하고 이후 어느 상태에서도 불변이다(선적은 재입력 금지라 편집 구간 자체가 없다). `doc_date`도 ORIGIN —
    # 원천 복사가 아니라 생성 시 `today_kst()` 1회 설정(R-15). 편집 구간(PLANNED)의 CONTENT는 국가 2열·합계(라인 수량 연동)뿐이다.
    # 복제 경로가 없어 `copied_from_id`는 SYSTEM(CHECK `no_copy_lineage`로 항상 NULL). FREE는 메모·담당자(FREE_COLUMNS 4개 불변).
    "shipments": {
        **_AUDIT_SYSTEM,
        "version": S,
        "doc_number": S,
        "status": S,
        "frozen_at": S,
        "last_line_no": S,
        "copied_from_id": S,
        "doc_date": ORG,
        "currency": ORG,
        "fx_rate": ORG,
        "fx_rate_date": ORG,
        "payment_type": ORG,
        "advance_pct_bp": ORG,
        "balance_anchor": ORG,
        "balance_days": ORG,
        "incoterm_code": ORG,
        "incoterm_place": ORG,
        "incoterm_year": ORG,
        "shipment_kind": ORG,
        "so_id": ORG,
        "po_id": ORG,
        "counterparty_partner_id": ORG,
        "counterparty_name": ORG,
        "origin_country_code": C,
        "dest_country_code": C,
        "total_amount": C,
        "internal_note": F,
        "assignee_id": F,
    },
    # 선적 라인 — 원천 라인 참조·SKU·통화·단가·무상 표식은 ORIGIN(원천 사본), 수량·금액만 CONTENT(PLANNED 중 원천 잔량 안에서), 번호는 SYSTEM.
    "shipment_lines": {
        **_AUDIT_SYSTEM,
        "shipment_id": ORG,
        "line_no": S,
        "so_line_id": ORG,
        "po_line_id": ORG,
        "sku_id": ORG,
        "sku_code": ORG,
        "sku_name_ko": ORG,
        "sku_name_en": ORG,
        "sku_kind": ORG,
        "currency": ORG,
        "unit_price_amount": ORG,
        "is_free": ORG,
        "quantity": C,
        "line_amount": C,
    },
}


def columns_of(table: str, *classes: ColumnClass) -> frozenset[str]:
    return frozenset(name for name, cls in FIELD_POLICY[table].items() if cls in classes)


def frozen_columns(table: str) -> frozenset[str]:
    """동결 후 불변 열 = CONTENT + ORIGIN(파생 — 수기 정의 금지)."""
    return columns_of(table, ColumnClass.CONTENT, ColumnClass.ORIGIN)


def classify(table: str, column: str) -> ColumnClass:
    """미등재 컬럼은 KeyError — 조용한 미분류를 허용하지 않는다."""
    return FIELD_POLICY[table][column]
