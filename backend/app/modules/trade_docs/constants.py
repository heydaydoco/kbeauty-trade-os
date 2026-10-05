"""전표 상수의 단일 출처 (S3-1 design-integrated §2.3 — ADR-0051·0054·0055).

여기서 정한 열거는 DB CHECK·서비스·요청 스키마가 전부 이 값을 참조한다. 값을 더하려면 CHECK 재정의
마이그레이션+ADR 부기가 세트다(fail-closed).
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum


class DocKind(StrEnum):
    """폴리모픽 전표 어휘 — DB에 저장되는 값이다(approvals.target_type·gate subject_type⊂DocKind)."""

    QUOTATION = "QUOTATION"
    PROFORMA_INVOICE = "PROFORMA_INVOICE"
    SALES_ORDER = "SALES_ORDER"
    PURCHASE_ORDER = "PURCHASE_ORDER"
    #: S3-2 PR-3a — 선적(수출·수입 한 kind + `shipment_kind` 4값, ADR-0074). approvals·gates 대상 CHECK는 넓히지 않는다
    #: (두 집합은 DocKind의 부분집합이면 된다 — 선적 승인·게이트는 만들지 않는다).
    SHIPMENT = "SHIPMENT"


#: 채번 접두어(표시·채번 전용). doc_number 형식 CHECK와 3자 일치한다(테스트가 대사).
DOC_PREFIXES: dict[DocKind, str] = {
    DocKind.QUOTATION: "QT",
    DocKind.PROFORMA_INVOICE: "PI",
    DocKind.SALES_ORDER: "SO",
    DocKind.PURCHASE_ORDER: "PO",
    DocKind.SHIPMENT: "SH",  # 수출·수입 공유(ADR-0074 — `SI`는 S3-3 Shipping Instruction 약어와 충돌)
}

#: 헤더·라인·상태이력 테이블 이름(테이블 이름 기반 Core 쿼리가 소비 — 모델 무임포트).
DOC_TABLES: dict[DocKind, str] = {
    DocKind.QUOTATION: "quotations",
    DocKind.PROFORMA_INVOICE: "proforma_invoices",
    DocKind.SALES_ORDER: "sales_orders",
    DocKind.PURCHASE_ORDER: "purchase_orders",
    DocKind.SHIPMENT: "shipments",
}
LINE_TABLES: dict[DocKind, str] = {
    DocKind.QUOTATION: "quotation_lines",
    DocKind.PROFORMA_INVOICE: "proforma_invoice_lines",
    DocKind.SALES_ORDER: "sales_order_lines",
    DocKind.PURCHASE_ORDER: "purchase_order_lines",
    DocKind.SHIPMENT: "shipment_lines",
}
STATUS_LOG_TABLES: dict[DocKind, str] = {
    DocKind.QUOTATION: "quotation_status_log",
    DocKind.PROFORMA_INVOICE: "proforma_invoice_status_log",
    DocKind.SALES_ORDER: "sales_order_status_log",
    DocKind.PURCHASE_ORDER: "purchase_order_status_log",
    DocKind.SHIPMENT: "shipment_status_log",
}
#: 상태이력 표의 문서 FK 컬럼 이름.
STATUS_LOG_FK: dict[DocKind, str] = {
    DocKind.QUOTATION: "quotation_id",
    DocKind.PROFORMA_INVOICE: "proforma_invoice_id",
    DocKind.SALES_ORDER: "sales_order_id",
    DocKind.PURCHASE_ORDER: "purchase_order_id",
    DocKind.SHIPMENT: "shipment_id",
}
#: 라인 → 헤더 FK 컬럼 이름(짧게 — 유니크 인덱스 이름 63자 한도, design-A A1).
LINE_HEADER_FK: dict[DocKind, str] = {
    DocKind.QUOTATION: "qt_id",
    DocKind.PROFORMA_INVOICE: "pi_id",
    DocKind.SALES_ORDER: "so_id",
    DocKind.PURCHASE_ORDER: "po_id",
    DocKind.SHIPMENT: "shipment_id",
}
#: 헤더 합계 열·라인 금액 열(PO만 원가 이름 — `_cost` 접미가 마스킹·금액 판정에 자동 편입, design-A A12).
#: 선적은 **판매가 축**(수출 = SO 단가 사본 × 수량, 수입 = 0 — PO 원가 비복사, ADR-0024 10번째 채널 미개설).
HEADER_TOTAL_COLUMN: dict[DocKind, str] = {
    DocKind.QUOTATION: "total_amount",
    DocKind.PROFORMA_INVOICE: "total_amount",
    DocKind.SALES_ORDER: "total_amount",
    DocKind.PURCHASE_ORDER: "total_cost",
    DocKind.SHIPMENT: "total_amount",
}
LINE_AMOUNT_COLUMN: dict[DocKind, str] = {
    DocKind.QUOTATION: "line_amount",
    DocKind.PROFORMA_INVOICE: "line_amount",
    DocKind.SALES_ORDER: "line_amount",
    DocKind.PURCHASE_ORDER: "line_cost",
    DocKind.SHIPMENT: "line_amount",
}
#: 아웃박스 이벤트 이름 접두(`{접두}.created|status_changed`) — aggregate_type은 복수형 테이블명.
EVENT_PREFIX: dict[DocKind, str] = {
    DocKind.QUOTATION: "quotations.quotation",
    DocKind.PROFORMA_INVOICE: "proforma_invoices.proforma_invoice",
    DocKind.SALES_ORDER: "sales_orders.sales_order",
    DocKind.PURCHASE_ORDER: "purchase_orders.purchase_order",
    DocKind.SHIPMENT: "shipments.shipment",
}
#: 거래 상대 열(이벤트 payload는 이를 `partner_id`로 매핑한다 — X-25).
PARTNER_COLUMN: dict[DocKind, str] = {
    DocKind.QUOTATION: "buyer_partner_id",
    DocKind.PROFORMA_INVOICE: "buyer_partner_id",
    DocKind.SALES_ORDER: "buyer_partner_id",
    DocKind.PURCHASE_ORDER: "supplier_partner_id",
    DocKind.SHIPMENT: "counterparty_partner_id",  # 수출 = SO 바이어, 수입 = PO 공급사(원천 사본)
}

#: 동결 표식 열 — 이 열이 NOT NULL이면 동결된 전표다(X-03). SO만 confirmed_at(확정 시각=동결 시각).
#: 선적은 출고지시(PLANNED→RELEASE_ORDERED, 동결 액션 `release-order`) 시각이다.
FREEZE_COLUMN: dict[DocKind, str] = {
    DocKind.QUOTATION: "frozen_at",
    DocKind.PROFORMA_INVOICE: "frozen_at",
    DocKind.SALES_ORDER: "confirmed_at",
    DocKind.PURCHASE_ORDER: "frozen_at",
    DocKind.SHIPMENT: "frozen_at",
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


class PoKind(StrEnum):
    """구매 발주 구분(design-F F3·ADR-0057) — OEM 생산 발주는 S3-2 마일스톤 프로파일의 키다."""

    PURCHASE = "PURCHASE"
    OEM_PRODUCTION = "OEM_PRODUCTION"


class ShipmentKind(StrEnum):
    """선적 구분 4값(§7.5 문면 — ADR-0074). **채널입고·샘플무상은 값만 싣고 생성 경로가 닫혀 있다** — DB
    `ck_shipments_kind_source`가 두 값의 행을 거부한다(S4-3·S5-2·무상 SO 판정 세션이 CHECK를 재정의하며 연다, 부채 Q-03).
    수출(EXPORT) = SO 참조 생성(PR-3a), 수입(IMPORT) = PO 참조 생성(PR-5a)."""

    EXPORT = "EXPORT"
    IMPORT = "IMPORT"
    CHANNEL_INBOUND = "CHANNEL_INBOUND"
    SAMPLE_FREE = "SAMPLE_FREE"


class PartyRole(StrEnum):
    """선적 당사자 역할 5값(design-A A5). 수출 CONSIGNEE·수입 SHIPPER는 원천 거래처의 **자동 스냅샷 행(불변)**이고,
    수출 SHIPPER·수입 CONSIGNEE는 자사라 행을 만들지 않는다(422 `SHIPMENTS.PARTY.ROLE_NOT_ALLOWED`)."""

    SHIPPER = "SHIPPER"
    CONSIGNEE = "CONSIGNEE"
    NOTIFY = "NOTIFY"
    FORWARDER = "FORWARDER"
    CUSTOMS_BROKER = "CUSTOMS_BROKER"


class DeclarationKind(StrEnum):
    """통관 신고 구분(design-A A8) — 선적 구분과 같아야 한다(수출선적 = 수출신고만, 422 `SHIPMENTS.CUSTOMS.KIND_MISMATCH`).
    통관 기록은 사실 기록이다(세율·과세가격·세액·HS 열 없음 — §15 법적 판정 금지)."""

    EXPORT = "EXPORT"
    IMPORT = "IMPORT"


class MilestoneType(StrEnum):
    """마일스톤 종류(§7.5 9종 → 11종 + OEM 4종 — ADR-0080 / design-integrated §2.4·§9 R-03).

    저장형(사람 입력, `milestones` 행)과 파생형(계산값 — 저장하지 않는다, DB CHECK가 거부)으로 나뉜다. 파생형 쓰기는 422
    `SHIPMENTS.MILESTONE.DERIVED_NOT_EDITABLE`(덮어쓰기 금지 2중 — 서비스 + CHECK). OEM 4종은 PO 소유 행만(PR-4c 쓰기 경로)."""

    # 선적 저장형 8
    DOC_CUTOFF = "DOC_CUTOFF"  # 서류마감(시각형)
    CARGO_CLOSING = "CARGO_CLOSING"  # Cargo Closing(시각형)
    PSI = "PSI"  # 수출 전 검사
    CUSTOMS_CLEARED = "CUSTOMS_CLEARED"  # 신고수리 — 실적 = 통관 기록 MIN(accepted_on) 파생(X-02)
    ETD = "ETD"
    BL_ISSUED = "BL_ISSUED"  # B/L(AWB) 발행일(§7.5 확장 — 제시기한 산식 입력)
    ETA = "ETA"
    IMPORT_TAX_DUE = "IMPORT_TAX_DUE"  # 수입 세금 납부기한(사람 입력 — 법정 기한 계산 안 함)
    # OEM 생산 4(PO 소유 — PR-4c)
    RAW_MATERIAL_READY = "RAW_MATERIAL_READY"
    FILLING = "FILLING"
    PACKING = "PACKING"
    OUTGOING_INSPECTION = "OUTGOING_INSPECTION"
    # 파생 3(비저장 — DB CHECK에 없다)
    LOADING_DEADLINE = "LOADING_DEADLINE"  # 적재기한 = 수리일 + 30(수출)
    PAYMENT_DUE = "PAYMENT_DUE"  # 대금만기(결제유형 분기)
    PRESENTATION_DEADLINE = "PRESENTATION_DEADLINE"  # L/C 제시기한 = MIN(B/L+21, 유효기일)(LC만)


#: 선적 소유 저장형 8종(표시 순서와 무관한 집합). 품목군 마일스톤 세트도 이 8종만 담는다(CHECK).
SHIPMENT_STORED_MILESTONES: frozenset[str] = frozenset(
    {
        MilestoneType.DOC_CUTOFF.value,
        MilestoneType.CARGO_CLOSING.value,
        MilestoneType.PSI.value,
        MilestoneType.CUSTOMS_CLEARED.value,
        MilestoneType.ETD.value,
        MilestoneType.BL_ISSUED.value,
        MilestoneType.ETA.value,
        MilestoneType.IMPORT_TAX_DUE.value,
    }
)
#: PO 소유 OEM 생산 4종(`ck_milestones_owner_type_scope` — OEM 4종 ⇔ po_id).
OEM_MILESTONES: frozenset[str] = frozenset(
    {
        MilestoneType.RAW_MATERIAL_READY.value,
        MilestoneType.FILLING.value,
        MilestoneType.PACKING.value,
        MilestoneType.OUTGOING_INSPECTION.value,
    }
)
#: 저장형 전부(DB CHECK `ck_milestones_type_valid`의 값 공간).
STORED_MILESTONES: frozenset[str] = SHIPMENT_STORED_MILESTONES | OEM_MILESTONES
#: 파생형 3종 — 저장하지 않는다(쓰기 422 + CHECK 거부).
DERIVED_MILESTONES: frozenset[str] = frozenset(
    {
        MilestoneType.LOADING_DEADLINE.value,
        MilestoneType.PAYMENT_DUE.value,
        MilestoneType.PRESENTATION_DEADLINE.value,
    }
)
#: 시각형(UTC 시각 + IANA tz) 종류 — 나머지는 날짜형(현지 달력일 DATE).
DATETIME_MILESTONES: frozenset[str] = frozenset(
    {MilestoneType.DOC_CUTOFF.value, MilestoneType.CARGO_CLOSING.value}
)
#: 출고지시(RELEASE_ORDERED) 이후에만 실적을 받는 종류 — 실적이 살아 있으면 선적 취소 409(R-01, 도착 실적은 출항을 함의).
RELEASE_BOUND_ACTUALS: frozenset[str] = frozenset(
    {MilestoneType.ETD.value, MilestoneType.BL_ISSUED.value, MilestoneType.ETA.value}
)
#: 선적 구분별 저장형 적용 집합(design-B B1 표 — 채널입고·샘플무상은 경로가 열리는 세션이 행을 더한다).
SHIPMENT_MILESTONES_BY_KIND: dict[str, frozenset[str]] = {
    ShipmentKind.EXPORT.value: SHIPMENT_STORED_MILESTONES - {MilestoneType.IMPORT_TAX_DUE.value},
    ShipmentKind.IMPORT.value: SHIPMENT_STORED_MILESTONES - {MilestoneType.PSI.value},
}
#: 선적 마일스톤 보드의 행 순서(업무 흐름 — design-D D6). 저장형 8 + 파생 3 = 11행.
SHIPMENT_BOARD_ORDER: tuple[str, ...] = (
    MilestoneType.DOC_CUTOFF.value,
    MilestoneType.CARGO_CLOSING.value,
    MilestoneType.PSI.value,
    MilestoneType.CUSTOMS_CLEARED.value,
    MilestoneType.LOADING_DEADLINE.value,
    MilestoneType.ETD.value,
    MilestoneType.BL_ISSUED.value,
    MilestoneType.PRESENTATION_DEADLINE.value,
    MilestoneType.ETA.value,
    MilestoneType.IMPORT_TAX_DUE.value,
    MilestoneType.PAYMENT_DUE.value,
)
#: OEM 생산 일정 보드의 행 순서(PO 상세 '생산 일정' 섹션 — design-D D7: 원료수급 → 충진 → 포장 → 출하검사). 날짜형 4행, 파생 없음.
OEM_BOARD_ORDER: tuple[str, ...] = (
    MilestoneType.RAW_MATERIAL_READY.value,
    MilestoneType.FILLING.value,
    MilestoneType.PACKING.value,
    MilestoneType.OUTGOING_INSPECTION.value,
)


class MilestoneChangeKind(StrEnum):
    """마일스톤 변경 이력 종류(ADR-0083) — 롤오버 = PLAN_CHANGED. PLAN_CHANGED·ACTUAL_CORRECTED는 사유 필수(CHECK)."""

    PLAN_SET = "PLAN_SET"
    PLAN_CHANGED = "PLAN_CHANGED"
    ACTUAL_RECORDED = "ACTUAL_RECORDED"
    ACTUAL_CORRECTED = "ACTUAL_CORRECTED"


#: 사유 필수 변경 종류(롤오버·실적 정정).
REASON_REQUIRED_CHANGES: frozenset[str] = frozenset(
    {MilestoneChangeKind.PLAN_CHANGED.value, MilestoneChangeKind.ACTUAL_CORRECTED.value}
)
#: "롤오버"로 세는 종류(design-B B9·design-D D6) — 이 종류의 PLAN_CHANGED만 롤오버 횟수·'통보 기록 없음' 배지 대상이다.
#: 다른 종류의 계획 변경도 이력(PLAN_CHANGED·사유 필수)은 남지만 배지는 0. 기일 스캔(PR-6)이 같은 집합을 공유한다.
ROLLOVER_TYPES: frozenset[str] = frozenset(
    {MilestoneType.ETD.value, MilestoneType.ETA.value, MilestoneType.CARGO_CLOSING.value}
)
#: 업무 날짜 범위 — 마일스톤 계획·실적(날짜형 값, 시각형은 이 범위의 UTC 시각)·통관 신고일·수리일·선적 통보의 오간 날.
#: 휴일 연도 규약(2000~2999 — ADR-0082)과 같다. 범위 밖 = 422, DB CHECK가 같은 범위를 강제(달력 끝 산술 OverflowError 500 방지 —
#: S3-2 PR-4a 적대 검토 반영 ⑤).
BUSINESS_DATE_MIN = date(2000, 1, 1)
BUSINESS_DATE_MAX = date(2999, 12, 31)
BUSINESS_DATE_MESSAGE = "2000-01-01 ~ 2999-12-31 범위의 날짜만 받습니다."
#: 통관 신고번호 최대 길이(VARCHAR(40) — 정규화 뒤 길이로 검사한다).
DECLARATION_NO_MAX = 40


class PriceBasis(StrEnum):
    MASTER = "MASTER"  # 마스터 판가(price_at)
    MANUAL = "MANUAL"  # 사람이 입력
    BUYER_PO = "BUYER_PO"  # 바이어 PO 추출 단가(인테이크 — D)


SALES_PRICE_BASES: tuple[str, ...] = tuple(b.value for b in PriceBasis)
#: 구매 발주 라인의 단가 기준 — 바이어 PO 추출(BUYER_PO)은 판매 체인 전용이다(design-A A12).
PURCHASE_PRICE_BASES: tuple[str, ...] = (PriceBasis.MASTER.value, PriceBasis.MANUAL.value)
SKU_KINDS: tuple[str, ...] = ("SINGLE", "SET")

#: QT 개정 발행으로 원본을 취소할 때의 자동 사유 문구(사람 엣지 ISSUED→CANCELLED, 행위자=발행자).
REVISION_CANCEL_REASON = "개정 발행으로 취소 (개정본 {doc_number})"
