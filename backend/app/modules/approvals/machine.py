"""승인 상태 기계·열거 상수 — 단일 출처 (S3-1 ADR-0060 / design-C C1·C2).

■ **2단 = 기안(요청 행위 1회) + 결정(결재 자격자 1명의 1회 결정)**이다. 다단·검토·합의는 구조적으로 불가
  (단계 컬럼·부모 컬럼·복수 결정 자리가 없다). 기안은 상태가 아니라 `request_approval()`이 REQUESTED 행과
  `NULL→REQUESTED` 이벤트를 만드는 행위다.
■ 상태 6값 / 허용 전이 **7방향**(사람 결정 4 + 도메인 통로 3). 종결 4태(REJECTED·WITHDRAWN·CONSUMED·VOIDED)는
  탈출 전이가 없다 — 재기안은 되돌림이 아니라 **신규 행**이다(이력 보존·SoD 세탁 차단).
■ 사람 전이(HUMAN)는 `service.decide_approval` 한 통로만, 시스템 전이(SYSTEM)는 `consume_approval`·`void_for_target`만
  만든다. 시스템 통로는 승인을 **부여하지 않는다**(좁히는 방향뿐) — 승인 부여(REQUESTED→APPROVED)는 사람 전용이다.
■ 승인 유형은 코드 고정 열거다(ADR-11). S3-1이 소비하는 값은 `SO_CREDIT_EXCEEDED` 1종뿐이고 나머지는 소비 세션이
  값 추가 절차(ADR-0060)로 더한다 — 소비 없는 선확장은 "죽은 열거"다(ADR-0041).
"""

from __future__ import annotations

from enum import StrEnum


class ApprovalStatus(StrEnum):
    REQUESTED = "REQUESTED"  # 요청됨(결재 대기)
    APPROVED = "APPROVED"  # 승인됨(미소비)
    REJECTED = "REJECTED"  # 반려
    WITHDRAWN = "WITHDRAWN"  # 회수
    CONSUMED = "CONSUMED"  # 소비됨(확정에 사용 — 1회)
    VOIDED = "VOIDED"  # 무효(대상 변경·취소·상한 초과·승인 불필요)


R, A, J, W, C, V = (
    ApprovalStatus.REQUESTED,
    ApprovalStatus.APPROVED,
    ApprovalStatus.REJECTED,
    ApprovalStatus.WITHDRAWN,
    ApprovalStatus.CONSUMED,
    ApprovalStatus.VOIDED,
)

#: 사람 결정 통로(`decide_approval`)의 4방향.
HUMAN: frozenset[tuple[ApprovalStatus, ApprovalStatus]] = frozenset(
    {(R, A), (R, J), (R, W), (A, W)}
)
#: 도메인 통로의 3방향 — `void_for_target`(T4·T6)·`consume_approval`(T5).
SYSTEM: frozenset[tuple[ApprovalStatus, ApprovalStatus]] = frozenset({(R, V), (A, C), (A, V)})
ALLOWED: frozenset[tuple[ApprovalStatus, ApprovalStatus]] = HUMAN | SYSTEM

TERMINAL: frozenset[ApprovalStatus] = frozenset({J, W, C, V})
#: 대상당 1건만 존재할 수 있는 상태(활성 부분 유니크의 술어) — 종결 4태 밖이다.
ACTIVE_STATUSES: tuple[ApprovalStatus, ...] = (R, A)
#: 사유가 필수인 도착 상태(VOIDED는 사유 코드가 필수 — `VoidReasonCode`).
REASON_REQUIRED_TO: frozenset[ApprovalStatus] = frozenset({J, W})

REASON_MAX = 1000


class DecisionVerb(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    WITHDRAW = "WITHDRAW"


VERB_TO: dict[DecisionVerb, ApprovalStatus] = {
    DecisionVerb.APPROVE: A,
    DecisionVerb.REJECT: J,
    DecisionVerb.WITHDRAW: W,
}


class VoidReasonCode(StrEnum):
    TARGET_CHANGED = "TARGET_CHANGED"  # 승인 후 대상(게이트 입력)이 바뀌었다
    TARGET_CANCELLED = "TARGET_CANCELLED"  # 대상이 취소·확정·삭제되어 게이트 대상이 아니다
    CAP_EXCEEDED = "CAP_EXCEEDED"  # 승인 상한(요청 시점 초과분)을 넘는 금액이 됐다
    NOT_REQUIRED = "NOT_REQUIRED"  # 더는 승인이 필요하지 않다(초과분 0)


class ConsumeOutcome(StrEnum):
    CONSUMED = "CONSUMED"
    NOT_REQUIRED = "NOT_REQUIRED"
    BLOCKED = "BLOCKED"


class AuthorityKind(StrEnum):
    OWN = "OWN"  # 결재 역할 보유자
    ADMIN = "ADMIN"  # 활성 관리자(모든 유형·구간의 자격자 — 단 기안자 본인은 제외)
    DELEGATED = "DELEGATED"  # 오늘(KST) 유효한 대결 수임자
    DENIED_SELF = "DENIED_SELF"  # 기안자 본인(ADMIN 포함 — 직무분리)
    DENIED_NOT_APPROVER = "DENIED_NOT_APPROVER"


class ApprovalType(StrEnum):
    #: 여신 초과 수주(SO) 확정 승인 — S3-1이 소비하는 유일한 유형(§2·§7.10).
    SO_CREDIT_EXCEEDED = "SO_CREDIT_EXCEEDED"


class TargetType(StrEnum):
    SALES_ORDER = "SALES_ORDER"  # trade_docs DocKind 풀네임과 같은 어휘(X-14)


APPROVAL_TYPES: tuple[str, ...] = tuple(t.value for t in ApprovalType)
TARGET_TYPES: tuple[str, ...] = tuple(t.value for t in TargetType)
#: 유형 → 대상 종류 — 유형이 어떤 전표를 겨냥하는지(DB `type_target_pair` CHECK의 원천).
TYPE_TARGET: dict[str, str] = {ApprovalType.SO_CREDIT_EXCEEDED.value: TargetType.SALES_ORDER.value}


class ApproverRole(StrEnum):
    """결재 역할 — `RoleCode`에서 VIEWER를 뺀 4값(조회 전용은 결재할 수 없다)."""

    ADMIN = "ADMIN"
    TRADE = "TRADE"
    LOGISTICS = "LOGISTICS"
    CERT = "CERT"


APPROVER_ROLES: tuple[str, ...] = tuple(r.value for r in ApproverRole)

#: 이벤트 `pair_allowed` CHECK의 `(from, to)` 쌍 — 상태 기계를 DB가 한 번 더 강제한다(이중 정의 방지: 여기서 생성).
ALLOWED_PAIRS_SQL: str = " OR ".join(
    f"(from_status = '{a.value}' AND to_status = '{b.value}')"
    for a, b in sorted(ALLOWED, key=lambda pair: (pair[0].value, pair[1].value))
)

#: 사람 전이 이벤트 이름(아웃박스) — 과거형.
EVENT_NAME: dict[ApprovalStatus, str] = {
    R: "requested",
    A: "approved",
    J: "rejected",
    W: "withdrawn",
    C: "consumed",
    V: "voided",
}
