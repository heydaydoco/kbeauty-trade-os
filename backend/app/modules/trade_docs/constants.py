"""전표 상수의 단일 출처 (S3-1 design-integrated §2.3 — ADR-0051·0054·0055).

여기서 정한 열거는 DB CHECK·서비스·요청 스키마가 전부 이 값을 참조한다. 값을 더하려면 CHECK 재정의
마이그레이션+ADR 부기가 세트다(fail-closed).
"""

from __future__ import annotations

from enum import StrEnum


class DocKind(StrEnum):
    """폴리모픽 전표 어휘 — DB에 저장되는 값이다(approvals.target_type·gate subject_type⊂DocKind)."""

    QUOTATION = "QUOTATION"
    PROFORMA_INVOICE = "PROFORMA_INVOICE"
    SALES_ORDER = "SALES_ORDER"
    PURCHASE_ORDER = "PURCHASE_ORDER"


#: 채번 접두어(표시·채번 전용). doc_number 형식 CHECK와 3자 일치한다(테스트가 대사).
DOC_PREFIXES: dict[DocKind, str] = {
    DocKind.QUOTATION: "QT",
    DocKind.PROFORMA_INVOICE: "PI",
    DocKind.SALES_ORDER: "SO",
    DocKind.PURCHASE_ORDER: "PO",
}

#: 헤더·라인·상태이력 테이블 이름(테이블 이름 기반 Core 쿼리가 소비 — 모델 무임포트).
DOC_TABLES: dict[DocKind, str] = {
    DocKind.QUOTATION: "quotations",
    DocKind.PROFORMA_INVOICE: "proforma_invoices",
    DocKind.SALES_ORDER: "sales_orders",
    DocKind.PURCHASE_ORDER: "purchase_orders",
}
LINE_TABLES: dict[DocKind, str] = {
    DocKind.QUOTATION: "quotation_lines",
    DocKind.PROFORMA_INVOICE: "proforma_invoice_lines",
    DocKind.SALES_ORDER: "sales_order_lines",
    DocKind.PURCHASE_ORDER: "purchase_order_lines",
}
STATUS_LOG_TABLES: dict[DocKind, str] = {
    DocKind.QUOTATION: "quotation_status_log",
    DocKind.PROFORMA_INVOICE: "proforma_invoice_status_log",
    DocKind.SALES_ORDER: "sales_order_status_log",
    DocKind.PURCHASE_ORDER: "purchase_order_status_log",
}
#: 상태이력 표의 문서 FK 컬럼 이름.
STATUS_LOG_FK: dict[DocKind, str] = {
    DocKind.QUOTATION: "quotation_id",
    DocKind.PROFORMA_INVOICE: "proforma_invoice_id",
    DocKind.SALES_ORDER: "sales_order_id",
    DocKind.PURCHASE_ORDER: "purchase_order_id",
}
#: 라인 → 헤더 FK 컬럼 이름(짧게 — 유니크 인덱스 이름 63자 한도, design-A A1).
LINE_HEADER_FK: dict[DocKind, str] = {
    DocKind.QUOTATION: "qt_id",
    DocKind.PROFORMA_INVOICE: "pi_id",
    DocKind.SALES_ORDER: "so_id",
    DocKind.PURCHASE_ORDER: "po_id",
}
#: 헤더 합계 열·라인 금액 열(PO만 원가 이름 — `_cost` 접미가 마스킹·금액 판정에 자동 편입, design-A A12).
HEADER_TOTAL_COLUMN: dict[DocKind, str] = {
    DocKind.QUOTATION: "total_amount",
    DocKind.PROFORMA_INVOICE: "total_amount",
    DocKind.SALES_ORDER: "total_amount",
    DocKind.PURCHASE_ORDER: "total_cost",
}
LINE_AMOUNT_COLUMN: dict[DocKind, str] = {
    DocKind.QUOTATION: "line_amount",
    DocKind.PROFORMA_INVOICE: "line_amount",
    DocKind.SALES_ORDER: "line_amount",
    DocKind.PURCHASE_ORDER: "line_cost",
}
#: 아웃박스 이벤트 이름 접두(`{접두}.created|status_changed`) — aggregate_type은 복수형 테이블명.
EVENT_PREFIX: dict[DocKind, str] = {
    DocKind.QUOTATION: "quotations.quotation",
    DocKind.PROFORMA_INVOICE: "proforma_invoices.proforma_invoice",
    DocKind.SALES_ORDER: "sales_orders.sales_order",
    DocKind.PURCHASE_ORDER: "purchase_orders.purchase_order",
}
#: 거래 상대 열(이벤트 payload는 이를 `partner_id`로 매핑한다 — X-25).
PARTNER_COLUMN: dict[DocKind, str] = {
    DocKind.QUOTATION: "buyer_partner_id",
    DocKind.PROFORMA_INVOICE: "buyer_partner_id",
    DocKind.SALES_ORDER: "buyer_partner_id",
    DocKind.PURCHASE_ORDER: "supplier_partner_id",
}

#: 동결 표식 열 — 이 열이 NOT NULL이면 동결된 전표다(X-03). SO만 confirmed_at(확정 시각=동결 시각).
FREEZE_COLUMN: dict[DocKind, str] = {
    DocKind.QUOTATION: "frozen_at",
    DocKind.PROFORMA_INVOICE: "frozen_at",
    DocKind.SALES_ORDER: "confirmed_at",
    DocKind.PURCHASE_ORDER: "frozen_at",
}

#: 금액·수량 상한 — 프런트 number 보호(JS 안전 정수)·오버플로 방지(design-A A2).
MAX_SAFE_INTEGER = 9_007_199_254_740_991
MAX_QUANTITY = 99_999_999
#: 한 전표의 라인 수 상한(합계 검산·응답 크기 보호).
MAX_LINES = 500
#: 유효기간 상한 — 발행일로부터 365일(오타 방어).
MAX_VALIDITY_DAYS = 365

DOC_NUMBER_MAX_LENGTH = 20

#: SWIFT/BIC — 8자 또는 11자(영대문자·숫자). 은행 계좌 마스터·PI 은행 스냅샷 열의 CHECK가 같은 식을 쓴다.
SWIFT_PATTERN = "^[A-Z0-9]{8}([A-Z0-9]{3})?$"


class PaymentType(StrEnum):
    TT_ADVANCE = "TT_ADVANCE"  # 선수금 T/T(100% 선수금 포함)
    TT_DEFERRED = "TT_DEFERRED"  # 후불 T/T
    LC = "LC"  # L/C — 입력은 기능 플래그 `lc`가 켜졌을 때만(fail-closed)


class BalanceAnchor(StrEnum):
    ORDER_DATE = "ORDER_DATE"
    INVOICE_DATE = "INVOICE_DATE"
    ETD_DATE = "ETD_DATE"
    BL_DATE = "BL_DATE"
    ARRIVAL_DATE = "ARRIVAL_DATE"
    RECEIPT_DATE = "RECEIPT_DATE"  # 입고 확정일 — PO 전용(판매 체인에서 선택하면 422)


class IncotermCode(StrEnum):
    EXW = "EXW"
    FCA = "FCA"
    FAS = "FAS"
    FOB = "FOB"
    CFR = "CFR"
    CIF = "CIF"
    CPT = "CPT"
    CIP = "CIP"
    DAP = "DAP"
    DPU = "DPU"
    DDP = "DDP"
    DAT = "DAT"


#: Incoterms 판 — DAT는 2010판에만, DPU는 2020판에만 존재한다(교차 CHECK).
INCOTERM_YEARS: tuple[int, ...] = (2010, 2020)
DEFAULT_INCOTERM_YEAR = 2020


class PriceBasis(StrEnum):
    MASTER = "MASTER"  # 마스터 판가(price_at)
    MANUAL = "MANUAL"  # 사람이 입력
    BUYER_PO = "BUYER_PO"  # 바이어 PO 추출 단가(인테이크 — D)


SALES_PRICE_BASES: tuple[str, ...] = tuple(b.value for b in PriceBasis)
SKU_KINDS: tuple[str, ...] = ("SINGLE", "SET")

#: QT 개정 발행으로 원본을 취소할 때의 자동 사유 문구(사람 엣지 ISSUED→CANCELLED, 행위자=발행자).
REVISION_CANCEL_REASON = "개정 발행으로 취소 (개정본 {doc_number})"
