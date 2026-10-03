"""오더 인테이크 상태 기계 — 전이 표와 **상태 대입의 단일 통로** (S3-1 PR-13a / design-D D1(e) / ADR-0071).

■ 상태 3값(PENDING·CONFIRMED·REJECTED)·방향 쌍 6(자기전이 제외): **허용 2 = PENDING→CONFIRMED(사람 1클릭 `confirm_intake`) · PENDING→REJECTED(사유 ≥5자, 사람)**, 미허용 4.
  종결 2태(CONFIRMED·REJECTED) 탈출 0 · 자동 전이 0. 총수 `EXPECTED_COUNTS = (허용 2, 미허용 4)`는 테스트가 집계로 고정한다(전이를 더하면 그 테스트와 이 독스트링을 함께 고친다).
■ `apply_intake_transition`이 **`status`·`decided_at`·`decided_by_id`·`reject_reason`·`sales_order_id`를 대입하는 유일한 함수**다(이 함수 밖의 `.status =`·`update().values(status=…)`·
  `OrderIntake(status=…)`는 0건 — 아키텍처 스캔). 전이 호출처는 `confirm_intake`(CONFIRMED) 1곳과 `reject_intake`(REJECTED) 1곳이다.
■ 이벤트 `order_intakes.order_intake.status_changed`는 이 함수 안에서만 발행한다(payload 화이트리스트 — 금액·사유 원문·메모 금지).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError
from app.core.time import utcnow
from app.modules.order_intake.models import (
    REJECT_REASON_MAX,
    REJECT_REASON_MIN,
    IntakeStatus,
    OrderIntake,
)
from app.modules.outbox import service as outbox

PENDING = IntakeStatus.PENDING.value
CONFIRMED = IntakeStatus.CONFIRMED.value
REJECTED = IntakeStatus.REJECTED.value

#: 허용 전이(방향) — 사람 요청뿐이다. 자동 전이는 존재하지 않는다.
ALLOWED_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {(PENDING, CONFIRMED), (PENDING, REJECTED)}
)
TERMINAL_STATUSES: frozenset[str] = frozenset({CONFIRMED, REJECTED})

#: (허용, 미허용) 총수 — 3상태의 서로 다른 방향 쌍 6 = 2 + 4.
EXPECTED_COUNTS: tuple[int, int] = (2, 4)

EVENT_CREATED = "order_intakes.order_intake.created"
EVENT_STATUS_CHANGED = "order_intakes.order_intake.status_changed"
AGGREGATE_TYPE = "order_intakes"

#: 이벤트 payload 허용 키 — 금액·사유 원문·메모 금지(외부 채널로 나갈 수 있다).
STATUS_PAYLOAD_KEYS = (
    "intake_id",
    "from_status",
    "to_status",
    "buyer_partner_id",
    "assignee_id",
    "source_kind",
    "sales_order_id",
)
CREATED_PAYLOAD_KEYS = ("intake_id", "buyer_partner_id", "assignee_id", "source_kind")


def _checked_payload(payload: dict[str, object], keys: tuple[str, ...]) -> dict[str, object]:
    """이벤트 payload는 화이트리스트 키와 **정확히 같아야** 나간다(금액·사유 원문·메모가 새는 회귀를 막는다)."""
    if set(payload) != set(keys):
        raise ValueError(f"이벤트 payload 키가 화이트리스트와 다르다: {sorted(payload)}")
    return payload


def publish_created(session: Session, intake: OrderIntake) -> None:
    """착지 이벤트 — `register_intake`가 부른다."""
    outbox.publish(
        session,
        event_type=EVENT_CREATED,
        aggregate_type=AGGREGATE_TYPE,
        aggregate_id=intake.id,
        payload=_checked_payload(
            {
                "intake_id": intake.id,
                "buyer_partner_id": intake.buyer_partner_id,
                "assignee_id": intake.assignee_id,
                "source_kind": intake.source_kind,
            },
            CREATED_PAYLOAD_KEYS,
        ),
    )


def is_allowed(from_status: str, to_status: str) -> bool:
    return (from_status, to_status) in ALLOWED_TRANSITIONS


def _not_pending(from_status: str, to_status: str) -> AppError:
    return AppError(
        ErrorCode.ORDER_INTAKE_STATE_NOT_PENDING,
        detail={"from": from_status, "to": to_status},
    )


def apply_intake_transition(
    session: Session,
    intake: OrderIntake,
    to: str,
    *,
    actor_id: int,
    reason: str | None = None,
    sales_order_id: int | None = None,
) -> None:
    """상태 전이 1건 — 허용 표 검사 → 결정 열 대입 → 이벤트. 호출부가 행 잠금·version 대조를 이미 했다는 전제다.

    CONFIRMED는 `sales_order_id`가 필수이고(백링크), REJECTED는 `reason`(5~500자, 서비스가 위생 검사)이 필수다 — 인자가 어긋나면 프로그래밍 오류(ValueError)다.
    """
    from_status = intake.status
    if not is_allowed(from_status, to):
        raise _not_pending(from_status, to)
    if to == CONFIRMED:
        if sales_order_id is None or reason is not None:
            raise ValueError("CONFIRMED 전이는 sales_order_id만 받는다.")
    elif to == REJECTED:
        if sales_order_id is not None or reason is None:
            raise ValueError("REJECTED 전이는 reason만 받는다.")
        if not REJECT_REASON_MIN <= len(reason.strip()) <= REJECT_REASON_MAX:
            raise ValueError("거부 사유 길이는 서비스 검사를 통과해야 한다.")
    intake.status = to
    intake.decided_at = utcnow()
    intake.decided_by_id = actor_id
    intake.updated_by_id = actor_id
    if to == CONFIRMED:
        intake.sales_order_id = sales_order_id
    else:
        intake.reject_reason = reason.strip() if reason is not None else None
    session.flush()
    outbox.publish(
        session,
        event_type=EVENT_STATUS_CHANGED,
        aggregate_type=AGGREGATE_TYPE,
        aggregate_id=intake.id,
        payload=_checked_payload(
            {
                "intake_id": intake.id,
                "from_status": from_status,
                "to_status": to,
                "buyer_partner_id": intake.buyer_partner_id,
                "assignee_id": intake.assignee_id,
                "source_kind": intake.source_kind,
                "sales_order_id": intake.sales_order_id,
            },
            STATUS_PAYLOAD_KEYS,
        ),
    )
