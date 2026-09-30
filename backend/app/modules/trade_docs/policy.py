"""열 분류 레지스트리 `FIELD_POLICY` — 동결 계약의 유일 정본 (S3-1 ADR-0053 / design-B B2 / X-04).

전표 헤더·라인 테이블의 **모든 컬럼**을 정확히 하나로 등재한다(미등재·유령 = CI 실패, fail-closed):

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
}


def columns_of(table: str, *classes: ColumnClass) -> frozenset[str]:
    return frozenset(name for name, cls in FIELD_POLICY[table].items() if cls in classes)


def frozen_columns(table: str) -> frozenset[str]:
    """동결 후 불변 열 = CONTENT + ORIGIN(파생 — 수기 정의 금지)."""
    return columns_of(table, ColumnClass.CONTENT, ColumnClass.ORIGIN)


def classify(table: str, column: str) -> ColumnClass:
    """미등재 컬럼은 KeyError — 조용한 미분류를 허용하지 않는다."""
    return FIELD_POLICY[table][column]
