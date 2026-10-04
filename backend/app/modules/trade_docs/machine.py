"""전표 상태 기계 — 코드 고정 (S3-1 ADR-0051 / design-B B1 / DESIGN §7.2·ADR-11 "상태는 고정").

이 파일이 QT·PI·SO·PO·선적(SHIPMENT, S3-2 PR-3a — ADR-0074) 5종의 상태 값·전이의 유일한 정의다. 여기 없는 전이는
존재하지 않고, 상태를 대입하는 코드는 `transition.py`의 두 함수(`record_birth`·`record_transition`)뿐이다(아키텍처 테스트가
고정 — 전이 통로 스캔).

■ 총수 고정 — 허용 30방향(사람 18 + 자동 12) / 미허용 152 / 총 182쌍(자기전이 제외)
  QT 6(사람 3·자동 3)·PI 8(사람 1·자동 7)·SO 10(사람 8·자동 2)·PO 3(사람 3)·선적 3(사람 3). 총수는
  tests/architecture/test_doc_machines.py의 EXPECTED가 집계로 고정한다 — 전이를 더하거나 빼면 그 테스트와
  이 독스트링을 함께 고친다(ADR-0038 관용).
■ 선적 8상태 중 활성은 PLANNED·RELEASE_ORDERED·CANCELLED뿐이다 — 피킹·검수완료·출고·선적·종결 5값은 RESERVED(S4-2,
  출고 = 원장 기록 시점 — §8.4 "검수 통과 후에만"). 출고지시(PLANNED→RELEASE_ORDERED)는 동결 액션 전용(`release-order`).

■ 자동 전이 = 대상 상태를 **사람이 고르지 않고 규칙이 도출한** 전이(행위자는 NULL[스윕]이거나 유발자[입금
  기록자·후속 전표 생성자]일 수 있다). 공개 API의 `to`로는 요청할 수 없다.

■ RESERVED — SO 후반 3값(PARTIALLY_ALLOCATED·ALLOCATED·COMPLETED)과 PO 후반 3값(PARTIALLY_RECEIVED·
  FULLY_RECEIVED·CLOSED)은 §7.2 문면·WBS "그대로 열거"로 근거가 있어 CHECK·StrEnum에 지금 싣지만 **엣지는 0**이다
  (ADR-0041 "죽은 열거" 아님 — 소비 세션 S4-2·S4-1·S3-3이 엣지를 더한다).
■ SO IN_SHIPMENT(S3-2 PR-3a / ADR-0075) — CONFIRMED↔IN_SHIPMENT **자동 수렴 2엣지**만 연다(선적 생성·라인 삭제·취소와 같은 TX,
  판정 함수 하나 `trade_chain.chain_ops.converge_sales_order_shipping` — 불변식 "확정 SO의 IN_SHIPMENT ⇔ 살아 있는 선적 ≥ 1").
  도착 상태는 이행 진행·복귀이지 약정 진입(확정)이 아니다(§15 4금 밖 — 자동 확정 0). IN_SHIPMENT의 보류·취소 엣지는 없다(선적 선취소).
  **SO COMPLETED는 RESERVED 유지**(ADR-0076 — 기본 미수 provider인 동안 열면 노출 공백, 아키텍처 시험이 결속).
"""

from __future__ import annotations

from enum import StrEnum

from app.modules.trade_docs.constants import DocKind

Pair = tuple[str, str]


class QuotationStatus(StrEnum):
    DRAFT = "DRAFT"
    ISSUED = "ISSUED"
    CONVERTED = "CONVERTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class ProformaInvoiceStatus(StrEnum):
    ISSUED = "ISSUED"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    PAID = "PAID"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class SalesOrderStatus(StrEnum):
    RECEIVED = "RECEIVED"
    CONFIRMED = "CONFIRMED"
    PARTIALLY_ALLOCATED = "PARTIALLY_ALLOCATED"
    ALLOCATED = "ALLOCATED"
    IN_SHIPMENT = "IN_SHIPMENT"
    COMPLETED = "COMPLETED"
    ON_HOLD = "ON_HOLD"
    CANCELLED = "CANCELLED"


class PurchaseOrderStatus(StrEnum):
    ISSUED = "ISSUED"
    SUPPLIER_CONFIRMED = "SUPPLIER_CONFIRMED"
    PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED"
    FULLY_RECEIVED = "FULLY_RECEIVED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class ShipmentStatus(StrEnum):
    """선적 8값(§7.2 문면 `D:181` — ADR-0074). 상태 열 VARCHAR(20) — 최장 RELEASE_ORDERED 15자."""

    PLANNED = "PLANNED"  # 계획(출생·편집 가능)
    RELEASE_ORDERED = "RELEASE_ORDERED"  # 출고지시(동결)
    PICKING = "PICKING"  # 피킹 — RESERVED(S4-2)
    INSPECTED = "INSPECTED"  # 검수완료 — RESERVED(S4-2, CI/PL 허용 기준점)
    RELEASED = "RELEASED"  # 출고(원장 기록 시점) — RESERVED(S4-2)
    SHIPPED = "SHIPPED"  # 선적(ETD 실적) — RESERVED
    CLOSED = "CLOSED"  # 종결 — RESERVED
    CANCELLED = "CANCELLED"


STATUS_ENUMS: dict[DocKind, type[StrEnum]] = {
    DocKind.QUOTATION: QuotationStatus,
    DocKind.PROFORMA_INVOICE: ProformaInvoiceStatus,
    DocKind.SALES_ORDER: SalesOrderStatus,
    DocKind.PURCHASE_ORDER: PurchaseOrderStatus,
    DocKind.SHIPMENT: ShipmentStatus,
}

#: 문서별 상태 값 튜플(DB CHECK·이력 CHECK가 이 값에서 파생된다).
STATUSES: dict[DocKind, tuple[str, ...]] = {
    kind: tuple(member.value for member in enum) for kind, enum in STATUS_ENUMS.items()
}

#: 생성 시 초기 상태(PI·PO는 초안이 없다 — 생성=발행).
INITIAL_STATUS: dict[DocKind, str] = {
    DocKind.QUOTATION: QuotationStatus.DRAFT.value,
    DocKind.PROFORMA_INVOICE: ProformaInvoiceStatus.ISSUED.value,
    DocKind.SALES_ORDER: SalesOrderStatus.RECEIVED.value,
    DocKind.PURCHASE_ORDER: PurchaseOrderStatus.ISSUED.value,
    DocKind.SHIPMENT: ShipmentStatus.PLANNED.value,
}

#: 죽은 상태 — 후속 생존 판정·부분 유니크 술어·소비 술어가 전부 이 튜플에서 파생된다(이중 정의 금지).
DEAD_STATUSES: tuple[str, ...] = ("CANCELLED", "EXPIRED")

#: 예약 상태 — 값은 CHECK에 있으나 in/out 엣지는 0(소비 세션이 더한다).
RESERVED: dict[DocKind, frozenset[str]] = {
    DocKind.QUOTATION: frozenset(),
    DocKind.PROFORMA_INVOICE: frozenset(),
    DocKind.SALES_ORDER: frozenset(
        {
            SalesOrderStatus.PARTIALLY_ALLOCATED.value,
            SalesOrderStatus.ALLOCATED.value,
            # IN_SHIPMENT은 S3-2 PR-3a가 자동 수렴 2엣지로 열었다(ADR-0075). COMPLETED는 S3-3 미수 provider PR까지 예약(ADR-0076).
            SalesOrderStatus.COMPLETED.value,
        }
    ),
    DocKind.PURCHASE_ORDER: frozenset(
        {
            PurchaseOrderStatus.PARTIALLY_RECEIVED.value,
            PurchaseOrderStatus.FULLY_RECEIVED.value,
            PurchaseOrderStatus.CLOSED.value,
        }
    ),
    # 선적 후반 5값 — 피킹·검수·출고(원장)·선적·종결은 S4-2가 엣지를 더한다(B: RESERVED 진입 0 = 검수 미완료 CI·PL 차단의 전제).
    DocKind.SHIPMENT: frozenset(
        {
            ShipmentStatus.PICKING.value,
            ShipmentStatus.INSPECTED.value,
            ShipmentStatus.RELEASED.value,
            ShipmentStatus.SHIPPED.value,
            ShipmentStatus.CLOSED.value,
        }
    ),
}

#: 종결 상태 — 탈출 엣지 0. (SO의 COMPLETED·PO의 CLOSED는 후속 세션이 종결로 다룬다.)
TERMINAL_STATUSES: dict[DocKind, frozenset[str]] = {
    DocKind.QUOTATION: frozenset({"EXPIRED", "CANCELLED"}),
    DocKind.PROFORMA_INVOICE: frozenset({"EXPIRED", "CANCELLED"}),
    DocKind.SALES_ORDER: frozenset({"CANCELLED"}),
    DocKind.PURCHASE_ORDER: frozenset({"CANCELLED"}),
    DocKind.SHIPMENT: frozenset({"CANCELLED"}),
}

#: PI 입금 3상태 — 입금 수렴(payment_status)·입금 원장(payments)이 공유하는 단일 출처.
PI_PAYMENT_STATES = ("ISSUED", "PARTIALLY_PAID", "PAID")
_PI_PAYMENT_STATES = PI_PAYMENT_STATES

#: 사람 전이 — 공개 API(전이 엔드포인트·동결 액션)가 수행한다. 18방향(선적 3 — S3-2 PR-3a).
HUMAN_TRANSITIONS: dict[DocKind, frozenset[Pair]] = {
    DocKind.QUOTATION: frozenset(
        {
            ("DRAFT", "ISSUED"),  # 발행(동결 액션 `issue` 전용)
            ("DRAFT", "CANCELLED"),  # 초안 폐기 = 취소(번호는 남는다)
            ("ISSUED", "CANCELLED"),  # 후속 생존 시 409
        }
    ),
    DocKind.PROFORMA_INVOICE: frozenset({("ISSUED", "CANCELLED")}),
    DocKind.SALES_ORDER: frozenset(
        {
            ("RECEIVED", "CONFIRMED"),  # 확정(동결 액션 `confirm` 전용)
            ("RECEIVED", "ON_HOLD"),
            ("RECEIVED", "CANCELLED"),
            ("CONFIRMED", "ON_HOLD"),
            ("CONFIRMED", "CANCELLED"),
            ("ON_HOLD", "RECEIVED"),  # 재개(보류 직전이 RECEIVED였을 때만)
            ("ON_HOLD", "CONFIRMED"),  # 재개(보류 직전이 CONFIRMED였을 때만·게이트 재평가 없음)
            ("ON_HOLD", "CANCELLED"),
        }
    ),
    DocKind.PURCHASE_ORDER: frozenset(
        {
            ("ISSUED", "SUPPLIER_CONFIRMED"),  # OC 기록
            ("ISSUED", "CANCELLED"),
            ("SUPPLIER_CONFIRMED", "CANCELLED"),
        }
    ),
    DocKind.SHIPMENT: frozenset(
        {
            (
                "PLANNED",
                "RELEASE_ORDERED",
            ),  # 출고지시(동결 액션 `release-order` 전용 — 물류도 가능)
            (
                "PLANNED",
                "CANCELLED",
            ),  # 사유 필수 — SO 잔량 복원(파생)·SO 수렴(마지막 선적이면 IN_SHIPMENT→CONFIRMED)
            ("RELEASE_ORDERED", "CANCELLED"),  # 사유 필수(실적·통관 생존 가드는 PR-4a)
        }
    ),
}

#: 자동 전이 — 규칙 도출(연쇄·스윕·입금 수렴·선적 수렴). 12방향. 공개 API로 요청할 수 없다.
AUTO_TRANSITIONS: dict[DocKind, frozenset[Pair]] = {
    DocKind.QUOTATION: frozenset(
        {
            ("ISSUED", "CONVERTED"),  # 살아 있는 확정 SO ≥ 1 (X-18)
            ("CONVERTED", "ISSUED"),  # 마지막 후속이 죽음(복귀 대칭)
            ("ISSUED", "EXPIRED"),  # 만료 스윕·복귀 직후 유효기간 경과
        }
    ),
    DocKind.PROFORMA_INVOICE: frozenset(
        {("ISSUED", "EXPIRED")}
        | {(a, b) for a in _PI_PAYMENT_STATES for b in _PI_PAYMENT_STATES if a != b}
    ),
    DocKind.SALES_ORDER: frozenset(
        {
            (
                "CONFIRMED",
                "IN_SHIPMENT",
            ),  # 살아 있는 선적 ≥ 1(첫 선적 생성과 같은 TX — 행위자 = 선적 생성자)
            (
                "IN_SHIPMENT",
                "CONFIRMED",
            ),  # 살아 있는 선적 0(마지막 선적 취소·복귀 대칭 — 행위자 = 취소자)
        }
    ),
    DocKind.PURCHASE_ORDER: frozenset(),
    DocKind.SHIPMENT: frozenset(),  # 선적 자동 엣지 0 — ETD 실적이 상태를 바꾸지 않는다(출고 RESERVED 우회 금지)
}

#: 동결 액션 전용 엣지 — 범용 전이 통로로는 못 넘는다(우회 표면 제거). record_transition은
#: `via_freeze_action=True`를 받은 호출(issue·confirm)만 통과시킨다.
FREEZE_ACTION_EDGES: dict[DocKind, frozenset[Pair]] = {
    DocKind.QUOTATION: frozenset({("DRAFT", "ISSUED")}),
    DocKind.PROFORMA_INVOICE: frozenset(),
    DocKind.SALES_ORDER: frozenset({("RECEIVED", "CONFIRMED")}),
    DocKind.PURCHASE_ORDER: frozenset(),
    DocKind.SHIPMENT: frozenset({("PLANNED", "RELEASE_ORDERED")}),
}

#: 도달 시 사유가 필수인 상태(사람 입력 또는 자동 문구). 상태이력 CHECK reason_required와 1:1.
REASON_REQUIRED_TO: dict[DocKind, frozenset[str]] = {
    DocKind.QUOTATION: frozenset({"CANCELLED", "EXPIRED"}),
    DocKind.PROFORMA_INVOICE: frozenset({"CANCELLED", "EXPIRED"}),
    DocKind.SALES_ORDER: frozenset({"CANCELLED", "ON_HOLD"}),
    DocKind.PURCHASE_ORDER: frozenset({"CANCELLED"}),
    DocKind.SHIPMENT: frozenset({"CANCELLED"}),
}

#: 편집 가능 상태 — CONTENT 컬럼(라인 포함)을 고칠 수 있는 유일한 구간(ON_HOLD는 동결 취급).
EDITABLE_STATES: dict[DocKind, frozenset[str]] = {
    DocKind.QUOTATION: frozenset({"DRAFT"}),
    DocKind.PROFORMA_INVOICE: frozenset(),
    DocKind.SALES_ORDER: frozenset({"RECEIVED"}),
    DocKind.PURCHASE_ORDER: frozenset(),
    DocKind.SHIPMENT: frozenset(
        {"PLANNED"}
    ),  # 라인·국가 편집은 계획 중에만(출고지시 후 409 FROZEN — X-20)
}


def public_transition_targets(kind: DocKind) -> frozenset[str]:
    """범용 전이 엔드포인트의 `to` Literal 원천 — 사람 엣지 중 동결 액션 엣지·RESERVED 도달을 뺀 도달 상태.

    SO 재개용 CONFIRMED(ON_HOLD→CONFIRMED)는 공개 대상이다. RECEIVED→CONFIRMED는 엣지 단위로 동결 액션
    전용이라 같은 `to`로 요청해도 record_transition이 거부한다.
    """
    return frozenset(
        to
        for pair in HUMAN_TRANSITIONS[kind] - FREEZE_ACTION_EDGES[kind]
        for to in (pair[1],)
        if to not in RESERVED[kind]
    )


def allowed_transitions(kind: DocKind) -> frozenset[Pair]:
    return HUMAN_TRANSITIONS[kind] | AUTO_TRANSITIONS[kind]


def is_dead(status: str) -> bool:
    return status in DEAD_STATUSES


def is_terminal(kind: DocKind, status: str) -> bool:
    return status in TERMINAL_STATUSES[kind]
