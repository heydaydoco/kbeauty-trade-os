"""오더 보드 상수 — 열(stage)·상태 매핑·상한·벌크 열거의 단일 출처 (S3-1 PR-15a / design-D D6 / ADR-0066).

■ 보드 범위는 **접수 이후~선적중까지**의 인테이크·SO 뷰다(할당 열은 소비 세션 S4-2가 상수 1줄+테스트로 붙인다 — 죽은 열 금지).
  S3-2 PR-3a(ADR-0066 부기·ADR-0075): SO IN_SHIPMENT가 RESERVED에서 빠졌으므로 "선적중" 5번째 열을 더했다(카드가 조용히 사라지지 않게).
■ `BOARD_STAGE_STATUSES`(SO 상태→열)는 완전성 테스트가 지킨다: **모든 SO 상태 = 매핑됨 ∪ {CANCELLED} ∪ RESERVED**. RESERVED가 줄거나 상태가 늘면
  테스트가 실패해 카드가 조용히 사라지지 않는다. 취소·거부는 보드에서 제외한다(각 목록 화면의 상태 필터로 본다).
■ 벌크 열거는 3종뿐이다 — 보류·거부·취소·override·승인 요청은 **건별 사유·판정 해시가 필요한 행위**라 벌크로 흘리지 않는다(재판정 트리거: 실사용 요구).
"""

from __future__ import annotations

from enum import StrEnum

from app.modules.order_intake.models import IntakeStatus
from app.modules.trade_docs.machine import SalesOrderStatus


class BoardStage(StrEnum):
    """보드 열 — 순서가 곧 화면의 왼쪽→오른쪽이다(`STAGE_ORDER`)."""

    INTAKE_PENDING = "INTAKE_PENDING"
    SO_RECEIVED = "SO_RECEIVED"
    SO_ON_HOLD = "SO_ON_HOLD"
    SO_CONFIRMED = "SO_CONFIRMED"
    SO_IN_SHIPMENT = "SO_IN_SHIPMENT"


#: 고정 5열(화면 순서).
STAGE_ORDER: tuple[BoardStage, ...] = (
    BoardStage.INTAKE_PENDING,
    BoardStage.SO_RECEIVED,
    BoardStage.SO_ON_HOLD,
    BoardStage.SO_CONFIRMED,
    BoardStage.SO_IN_SHIPMENT,
)

STAGE_LABELS_KO: dict[BoardStage, str] = {
    BoardStage.INTAKE_PENDING: "인테이크 대기",
    BoardStage.SO_RECEIVED: "수주 접수",
    BoardStage.SO_ON_HOLD: "수주 보류",
    BoardStage.SO_CONFIRMED: "수주 확정",
    BoardStage.SO_IN_SHIPMENT: "선적중",
}

#: 인테이크 열이 보는 인테이크 상태 — 상태 열거의 단일 출처(`IntakeStatus`)에서 가져온다(문자열 하드코딩 금지).
INTAKE_STAGE_STATUS = IntakeStatus.PENDING.value
#: 보드에서 제외하는 인테이크 상태(확정=SO 카드로 이어짐·거부=종결) — 완전성 시험: `IntakeStatus` 전체 = 보드 포함 ∪ 보드 제외.
EXCLUDED_INTAKE_STATUSES: frozenset[str] = frozenset(
    {IntakeStatus.CONFIRMED.value, IntakeStatus.REJECTED.value}
)

#: SO 상태 → 열. 값이 없는 SO 상태(CANCELLED·RESERVED)는 보드에 나오지 않는다 — 완전성 테스트가 이 집합을 고정한다.
BOARD_STAGE_STATUSES: dict[BoardStage, tuple[str, ...]] = {
    BoardStage.SO_RECEIVED: (SalesOrderStatus.RECEIVED.value,),
    BoardStage.SO_ON_HOLD: (SalesOrderStatus.ON_HOLD.value,),
    BoardStage.SO_CONFIRMED: (SalesOrderStatus.CONFIRMED.value,),
    BoardStage.SO_IN_SHIPMENT: (SalesOrderStatus.IN_SHIPMENT.value,),
}

#: 보드에서 제외하는 SO 상태(매핑 완전성 테스트의 한 축) — 취소는 각 목록 화면에서 본다.
EXCLUDED_SO_STATUSES: frozenset[str] = frozenset({SalesOrderStatus.CANCELLED.value})

#: 열 정렬 — 대기·접수·보류는 접수(생성) 오래된 순(주의 필요), 확정·선적중은 최근 확정 순(같은 인덱스 `ix_sales_orders_board_confirmed`).
NEWEST_FIRST_STAGES: frozenset[BoardStage] = frozenset(
    {BoardStage.SO_CONFIRMED, BoardStage.SO_IN_SHIPMENT}
)

#: 열당 카드 상한(그 이상은 `has_more` + `GET /order-board/items` 드릴다운).
COLUMN_LIMIT = 50

#: 벌크 대상 상한(초과 422 `ORDER_BOARD.BULK.TOO_MANY`).
BULK_MAX_TARGETS = 50

#: 요청 스키마의 하드 상한 — 이 위는 본문 검증 단계에서 422(INVALID_FIELD). 51~이 값은 서비스가 `TOO_MANY`로 거절한다.
BULK_SCHEMA_HARD_LIMIT = 500

#: 사용자당 활성 저장 필터 상한(초과 422 `ORDER_BOARD.FILTER.LIMIT_REACHED`).
SAVED_FILTER_LIMIT = 20

SAVED_FILTER_NAME_MAX = 60
QUERY_MAX = 100

#: 보드 CSV 상한 — 기존 내보내기 6모듈 선례(초과 422로 조건 좁히기 안내, 조용한 잘라내기 금지).
EXPORT_MAX_ROWS = 50_000


class CardKind(StrEnum):
    INTAKE = "INTAKE"
    SO = "SO"


class BulkAction(StrEnum):
    """벌크 3종 — 각 건은 기존 단일 통로 함수를 그대로 부른다(벌크 전용 확정 코드 없음)."""

    CONFIRM_INTAKE = "CONFIRM_INTAKE"
    CONFIRM_SO = "CONFIRM_SO"
    ASSIGN = "ASSIGN"


class BulkOutcome(StrEnum):
    """건별 결과 — 부분 성공이 정상이다(응답 200)."""

    OK = "OK"
    SKIPPED = "SKIPPED"  # 변경 없음(이미 그 담당자)
    #: 업무상 막힘 — 개별 처리 필요(상세 화면 링크 대상). 확정 게이트 미해소(`GATE_BLOCKED` — `blocked_gates`에 서버 값)와
    #: 업무 거부 409(`BLOCKING_409_CODES` — `blocked_gates`는 빈 목록, `code`로 구분).
    BLOCKED = "BLOCKED"
    #: 경합 — 다른 사람이 먼저 바꿨다(`CONTENTION_409_CODES`·잠금 대기 초과·교착·`StaleDataError`, 그리고 분류표에 없는 409). 보드를 다시 불러오면 된다.
    CONFLICT = "CONFLICT"
    FORBIDDEN = "FORBIDDEN"  # 행별 서버측 역할 검증 거부(403)·처리 도중 행위자 자격 상실
    FAILED = "FAILED"  # 그 밖의 거부(404·422)·예상 못 한 오류


#: 경합 409 — 같은 대상을 다른 사람이 먼저 바꿨다(새로고침 후 다시 선택).
CONTENTION_409_CODES: frozenset[str] = frozenset(
    {
        "COMMON.CONCURRENCY.VERSION_CONFLICT",
        "COMMON.CONCURRENCY.LOCK_BUSY",
        "ORDER_INTAKE.STATE.NOT_PENDING",
        "TRADE_DOCS.TRANSITION.NOT_ALLOWED",
        "COMMON.IDEMPOTENCY.KEY_CONFLICT",
    }
)

#: 업무 거부 409 → BLOCKED(개별 처리 필요 — 품번 재해석·중복 PO 정리·평가 불능 확인·승인 재요청·복제 원본 확인은 상세 화면의 일이다).
#: 단일 통로(`confirm_intake`·`confirm_sales_order`)가 낼 수 있는 업무 거부 409 전부다. 이 집합과 경합 집합에 없는 409는 CONFLICT로 둔다.
BLOCKING_409_CODES: frozenset[str] = frozenset(
    {
        "ORDER_INTAKE.LINE.STALE_MAPPING",
        "TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO",
        "ORDER_INTAKE.GATE.UNRESOLVED",
        "APPROVALS.APPROVAL.STALE",
        "TRADE_DOCS.COPY.SOURCE_NOT_ELIGIBLE",
    }
)


#: 액션별 허용 대상 종류 — 확정 2종은 자기 종류만, ASSIGN은 둘 다.
ACTION_KINDS: dict[BulkAction, frozenset[CardKind]] = {
    BulkAction.CONFIRM_INTAKE: frozenset({CardKind.INTAKE}),
    BulkAction.CONFIRM_SO: frozenset({CardKind.SO}),
    BulkAction.ASSIGN: frozenset({CardKind.INTAKE, CardKind.SO}),
}

#: 처리 순서의 종류 순위 — 전역 LOCK_ORDER(인테이크 (1) → SO (5))와 같은 방향. 같은 종류 안에서는 id 오름차순.
#: 목적은 **결정적 처리 순서(재현성)**다 — 교착 방지는 건별 독립 TX와 각 단일 통로의 LOCK_ORDER가 맡는다(건 사이에 잠금을 들고 있지 않다).
KIND_RANK: dict[CardKind, int] = {CardKind.INTAKE: 0, CardKind.SO: 1}
