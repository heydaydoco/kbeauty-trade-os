"""마일스톤 쓰기 — 선적 계획(롤오버)·실적·계획 초안·통보 기록 + OEM 생산 일정 계획·실적
(S3-2 PR-4a·PR-4c / ADR-0078·0079·0080·0083·0085 / design-C T6·T7·T8 + design-integrated N-07 T13 / design-B B8·B9·B15·B16).

모든 동작은 **사람 1클릭 + 한 트랜잭션**이다(외부 호출 0 — 알림은 아웃박스 `shipments.milestone.changed`(선적)·
`purchase_orders.milestone.changed`(OEM)뿐, 통보는 기록이지 발송이 아니다).

■ 잠금 순서(ADR-0078 LOCK_ORDER): 계획·실적·초안 = 멱등 claim → 선적 `FOR UPDATE`(헤더 version 대조 없음 — 헤더 내용 불변) → 마일스톤 행
  `FOR UPDATE`(shipment_children) + **행 version** 대조. 통보 = 멱등 → (상대 거래처) partners `FOR KEY SHARE` → 선적 `FOR SHARE`(R-08).
  선적 취소도 같은 헤더를 `FOR UPDATE`로 잡으므로 "실적 기록 vs 취소"는 직렬화된다(실적이 먼저면 취소 409 ACTUAL_RECORDED, 취소가 먼저면
  실적 409 OWNER_NOT_ACTIVE).
■ 오류 우선순위(ADR-0079 ⑧ 401→403→404→409→422): 선적 404 → 취소된 선적 409(OWNER_NOT_ACTIVE) → 행 version 409 → 종류 자체의 쓰기 불가
  422(파생·비적용·신고수리 실적) → 값 검증 422(범위·형태·시간대·미래·출고 전 실적·사유). (적대 검토 반영 ⑦ — 종전엔 종류 422가 version 409보다 먼저)
■ **덮어쓰기 금지 2중**: 파생 3종 쓰기 = 422 `DERIVED_NOT_EDITABLE`(여기) + DB `ck_milestones_type_valid`(파생 값 공간 없음 — 번역표).
■ 재계산 = 읽기 결과의 변화(파생 비저장) — 이 TX는 마일스톤 행 1·이력 1·아웃박스 1만 쓴다. **상태 전이 0**(`record_transition` 미임포트 — B8 ⑦).
■ 응답 = `{board, change: {id, change_kind} | null}`(R-19) — 이력 행이 안 생기는 no-op이면 null. 같은 Idempotency-Key 재요청 = 같은 change.id.
  **멱등 저장 본문은 결정적인 `change`만**이고 재생 때 보드를 새로 조립한다(재생 시점의 today_kst·판정 — 적대 검토 반영 ⑨).
■ 통보는 변경 1건당 `NOTICE_LIMIT_PER_CHANGE`건까지(초과 422 NOTICE_LIMIT_REACHED — 소유 마일스톤 행 `FOR UPDATE` 아래에서 센다,
  적대 검토 반영 ⑧). 잠금 순서: 멱등 → 거래처 → 선적(SHARE) → shipment_children(마일스톤 행).
■ **계획·실적 본체는 소유자 공통**(`_plan_change`·`_actual_change` — PR-4c): 행 `FOR UPDATE` → 행 version 409 → `require_writable`(소유자별
  종류 422) → 범위·형태·시간대·미래·사유 422 → 대입·이력·아웃박스. 선적과 OEM이 **같은 함수**를 쓴다(소유자만 다르다 — 복제 0).
■ **OEM 생산 일정(T13 — PR-4c)**: 멱등 claim → `purchase_orders` **`FOR SHARE`**(상태·구분 확인, PO 무수정 — PO 취소의 `FOR UPDATE`와
  직렬화) → 마일스톤 행 `FOR UPDATE`(shipment_children 슬롯 — X-09) + 행 version → UPDATE + 이력 + 아웃박스(payload `owner_type=PURCHASE_ORDER`).
  오류 순서: PO 404 → 취소 409 OWNER_NOT_ACTIVE → 행 version 409 → 일반 구매 PO 422 OWNER_NOT_OEM → 파생 422 DERIVED_NOT_EDITABLE·선적
  종류 422 TYPE_NOT_APPLICABLE → 값 422(4a 개정 규율과 같은 순서). 멱등 재생 = 저장된 change + 지금 보드. 쓰기 역할 = 무역(관리자 상시
  통과 — ADR-0079 ④). 알림·휴일 경고 없음(B15 — 표시만), 롤오버 배지 대상 아님(ROLLOVER_TYPES 밖 — B9).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, NotFoundError, VersionConflictError
from app.core.text import invisible_char_problem, is_invisible_char
from app.core.time import today_kst, utcnow
from app.core.tzdb import canonical_zone_name
from app.modules.catalog.models import Sku
from app.modules.collaboration import service as collaboration
from app.modules.idempotency import service as idempotency
from app.modules.identity.service import AuthenticatedUser
from app.modules.outbox import service as outbox
from app.modules.partners import service as partners
from app.modules.shipments import service as shipments
from app.modules.shipments.models import Milestone, Shipment, ShipmentLine
from app.modules.shipments.service import MilestoneOwner, MilestoneValue
from app.modules.trade_chain.milestone_view import (
    OEM_RECORD_EDITABLE_STATES,
    RECORD_EDITABLE_STATES,
    PoOwner,
    board_body,
    change_body,
    oem_board_body,
    po_owner,
    require_oem,
)
from app.modules.trade_docs import schedule
from app.modules.trade_docs.constants import (
    BUSINESS_DATE_MAX,
    BUSINESS_DATE_MESSAGE,
    BUSINESS_DATE_MIN,
    DATETIME_MILESTONES,
    DERIVED_MILESTONES,
    DOC_TABLES,
    OEM_MILESTONES,
    RELEASE_BOUND_ACTUALS,
    SHIPMENT_MILESTONES_BY_KIND,
    DocKind,
    MilestoneChangeKind,
    MilestoneType,
)
from app.modules.trade_docs.locking import lock_document
from app.modules.trade_docs.validation import invalid

PLAN_ENDPOINT = "POST /api/v1/shipments/{id}/milestones/{type}/plan"
ACTUAL_ENDPOINT = "POST /api/v1/shipments/{id}/milestones/{type}/actual"
DRAFT_ENDPOINT = "POST /api/v1/shipments/{id}/milestones/plan-draft"
NOTICE_ENDPOINT = "POST /api/v1/shipments/{id}/milestone-changes/{change_id}/notices"
OEM_PLAN_ENDPOINT = "POST /api/v1/purchase-orders/{id}/milestones/{type}/plan"
OEM_ACTUAL_ENDPOINT = "POST /api/v1/purchase-orders/{id}/milestones/{type}/actual"

#: 아웃박스 이벤트(`<도메인>.<대상>.<사건>`) — payload 화이트리스트: 소유자·종류·변경 종류·전후 값(금액·원가 0).
CHANGED_EVENT = "shipments.milestone.changed"
#: OEM 생산 일정 변경 이벤트 — 선적과 **다른 event_type**(PR-4c 적대 검토 반영 ⑤ / N-07 정정). 알림 규칙은 event_type으로만 매칭하므로
#: 같은 이름이면 선적용 규칙이 OEM 변경에도 발화해 B15 'OEM 알림 없음'을 어긴다 — 이름을 갈라 선적 규칙이 OEM에 절대 닿지 않게 한다(fail-closed).
OEM_CHANGED_EVENT = "purchase_orders.milestone.changed"
#: 소유자 종류 → 변경 이벤트 이름(아웃박스 aggregate_type은 소유 전표 표 이름 — DOC_TABLES).
CHANGED_EVENTS: dict[str, str] = {
    DocKind.SHIPMENT.value: CHANGED_EVENT,
    DocKind.PURCHASE_ORDER.value: OEM_CHANGED_EVENT,
}
#: ETD·B/L·ETA 실적을 받는 선적 상태(R-01 — 출고지시 뒤에만). S4-2가 피킹~종결을 열면 함께 재판정(인계 계약).
ACTUAL_RELEASE_STATES = frozenset({"RELEASE_ORDERED"})
#: 날짜형 실적의 미래 여유(현지 날짜가 KST보다 하루 앞설 수 있는 UTC+10 이상 지역 — B8 ④). 시각형은 여유 0(R-18).
ACTUAL_DATE_SLACK = timedelta(days=1)
#: 통보 상대 거래처 유형 — 물류(포워더·관세사·3PL)와 거래 상대(바이어·공급사·OEM). 인증 대행·RP류는 선적 통보 상대가 아니다(자율 확정).
NOTICE_PARTNER_TYPES: tuple[str, ...] = (
    "FORWARDER",
    "CUSTOMS_BROKER",
    "THREE_PL",
    "BUYER",
    "SUPPLIER",
    "OEM",
)
#: 변경 1건에 연결할 수 있는 통보 기록 상한(무상한 내장 집합 방지 — 이력 응답·보드 배지가 한 변경의 통보를 모두 싣는다. 적대 검토 반영 ⑧).
NOTICE_LIMIT_PER_CHANGE = 20
#: 통보 요지에서만 허용하는 줄 구분 문자(여러 줄 요지) — 그 밖의 보이지 않는 글자는 422.
_SUMMARY_LINE_BREAKS = frozenset("\t\n\r")
#: 시각형 값의 업무 범위 — [2000-01-01T00:00Z, 3000-01-01T00:00Z)(날짜형 BUSINESS_DATE_MIN~MAX와 같은 연도 범위, DB CHECK `value_range`).
_INSTANT_MIN = datetime(2000, 1, 1, tzinfo=UTC)
_INSTANT_END = datetime(3000, 1, 1, tzinfo=UTC)


# ── 공통 검증 ─────────────────────────────────────────────────────────────────


#: 소유자별 OWNER_NOT_ACTIVE 조치 문구(카탈로그는 소유자 중립 — 경로가 정확한 조치로 덮는다, PR-4c 적대 검토 반영 ③).
SHIPMENT_INACTIVE_MESSAGE = (
    "취소된 선적의 마일스톤은 수정할 수 없습니다. 필요하면 수주에서 새 선적을 만들어 주세요."
)
PO_INACTIVE_MESSAGE = (
    "취소된 발주의 생산 일정은 수정할 수 없습니다. 필요하면 새 발주를 만들어 주세요."
)


def _require_owner_active(row: Shipment) -> None:
    if row.status not in RECORD_EDITABLE_STATES:
        raise AppError(
            ErrorCode.SHIPMENTS_MILESTONE_OWNER_NOT_ACTIVE,
            detail={"owner_type": MilestoneOwner.of_shipment(row.id).owner_type},
            log_context={"shipment_id": row.id, "status": row.status},
            message_override=SHIPMENT_INACTIVE_MESSAGE,
        )


def _require_type_writable(
    milestone_type: str, applicable: frozenset[str], scope: dict[str, str]
) -> None:
    """종류 자체의 쓰기 가능 여부(행이 생길 수 없는 종류) — 파생 422 DERIVED_NOT_EDITABLE / 소유자 비적용 422 TYPE_NOT_APPLICABLE
    (선적: 구분별 적용 집합 밖·OEM 종류 / OEM PO: OEM 4종 밖). `scope`는 detail에 싣는 소유자 범위(구분)."""
    if milestone_type in DERIVED_MILESTONES:
        raise AppError(
            ErrorCode.SHIPMENTS_MILESTONE_DERIVED_NOT_EDITABLE,
            detail={"milestone_type": milestone_type},
        )
    if milestone_type not in applicable:
        raise AppError(
            ErrorCode.SHIPMENTS_MILESTONE_TYPE_NOT_APPLICABLE,
            detail={"milestone_type": milestone_type, **scope},
        )


def _require_shipment_type_writable(row: Shipment, milestone_type: str) -> None:
    _require_type_writable(
        milestone_type,
        SHIPMENT_MILESTONES_BY_KIND.get(row.shipment_kind, frozenset()),
        {"shipment_kind": row.shipment_kind},
    )


def _require_shipment_actual_writable(row: Shipment, milestone_type: str) -> None:
    """선적 실적 쓰기 가능 종류 — 종류 판정 + 신고수리 실적은 통관 기록에서만(422 ACTUAL_FROM_CUSTOMS_RECORD, X-02)."""
    _require_shipment_type_writable(row, milestone_type)
    if milestone_type == MilestoneType.CUSTOMS_CLEARED.value:
        raise AppError(
            ErrorCode.SHIPMENTS_MILESTONE_ACTUAL_FROM_CUSTOMS_RECORD,
            detail={"milestone_type": "신고수리 실적은 통관 기록의 수리일로 입력합니다."},
        )


def _require_version(milestone: Milestone | None, version: int | None) -> None:
    """행 version 대조 — 행이 있으면 그 version, 없으면 생략(None)이어야 한다(화면이 본 상태와 다르면 겹친 편집 409)."""
    expected = milestone.version if milestone is not None else None
    if version != expected:
        raise VersionConflictError(
            log_context={"milestone_id": milestone.id if milestone else None, "sent": version}
        )


def _reason(raw: object) -> str | None:
    """사유 — strip 전 원문의 보이지 않는 글자는 422(하우스 규칙), 공백뿐이면 None(사유 필수 판정은 호출자)."""
    if raw is None:
        return None
    text = str(raw)
    problem = invisible_char_problem(text, label="사유")
    if problem is not None:
        raise invalid("reason", problem)
    return text.strip() or None


def _shape_error(field: str, milestone_type: str) -> AppError:
    shape = "시각(UTC 오프셋 포함)과 시간대" if milestone_type in DATETIME_MILESTONES else "날짜"
    return AppError(
        ErrorCode.SHIPMENTS_MILESTONE_VALUE_SHAPE_MISMATCH,
        detail={field: f"이 종류는 {shape}로 입력합니다."},
    )


def _zone_name(raw: object) -> str:
    """시간대 이름 검증(앱 버전 고정 tzdata) + **정규 이름으로 저장**(폐지·별칭 이름 → 국가 대표 이름 — 예: Asia/Saigon → Asia/Ho_Chi_Minh).

    정규 이름은 tzdb가 오래 유지하는 쪽이라, tzdata 갱신으로 별칭이 빠져도 저장 행이 해석 불가(UNKNOWN `TZ_UNRESOLVED`)가 될 여지를 줄인다.
    """
    try:
        schedule.zone(str(raw) if raw is not None else "")
        return canonical_zone_name(str(raw))
    except (ValueError, KeyError):
        raise AppError(
            ErrorCode.SHIPMENTS_MILESTONE_TIMEZONE_INVALID,
            detail={"tz": "IANA 시간대 이름이 아닙니다(예: Asia/Seoul)."},
        ) from None


def _value(
    milestone_type: str, payload: dict[str, Any], *, on_key: str, at_key: str, required: bool
) -> MilestoneValue:
    """요청 값 → MilestoneValue(형태 검증) — 날짜형은 `*_on`만, 시각형은 `*_at`(UTC 오프셋)+`tz`만. `required=False`면 값 없음(지우기) 허용."""
    on, at, tz = payload.get(on_key), payload.get(at_key), payload.get("tz")
    if milestone_type in DATETIME_MILESTONES:
        if on is not None:
            raise _shape_error(on_key, milestone_type)
        if at is None:
            if tz is not None:
                raise _shape_error("tz", milestone_type)
            if required:
                raise _shape_error(at_key, milestone_type)
            return MilestoneValue()
        # 범위 검사는 UTC 변환 **전에**(aware 비교는 변환 없이 안전 — 0001년 값의 astimezone이 OverflowError 500을 내던 구멍)
        if not _INSTANT_MIN <= at < _INSTANT_END:
            raise invalid(at_key, BUSINESS_DATE_MESSAGE)
        return MilestoneValue(at=at.astimezone(UTC), tz=_zone_name(tz))
    if at is not None or tz is not None:
        raise _shape_error(at_key if at is not None else "tz", milestone_type)
    if on is None and required:
        raise _shape_error(on_key, milestone_type)
    if on is not None and not BUSINESS_DATE_MIN <= on <= BUSINESS_DATE_MAX:
        raise invalid(on_key, BUSINESS_DATE_MESSAGE)
    return MilestoneValue(on=on)


def _require_shared_zone(
    milestone: Milestone | None, value: MilestoneValue, *, other_at: Any
) -> None:
    """시각형 계획·실적은 시간대 열 하나를 공유한다(같은 장소의 사건) — 다른 쪽 값이 있으면 같은 tz만(422).

    예외: 저장된 tz를 앱의 tzdata가 해석하지 못하면(UNKNOWN `TZ_UNRESOLVED` 행) 새 tz로 갈아끼우는 것을 막지 않는다 — 탈출로
    (시각 값은 UTC 절대 시각이라 tz 교체는 현지 날짜 표시만 바꾼다).
    """
    if milestone is None or value.at is None or other_at is None:
        return
    if milestone.tz != value.tz and _resolvable(milestone.tz):
        raise AppError(
            ErrorCode.SHIPMENTS_MILESTONE_TIMEZONE_INVALID,
            detail={"tz": f"계획·실적은 같은 시간대여야 합니다(현재 {milestone.tz})."},
        )


def _resolvable(tz: str | None) -> bool:
    try:
        schedule.zone(tz or "")
    except ValueError:
        return False
    return True


def _same(old: MilestoneValue, new: MilestoneValue) -> bool:
    return old.on == new.on and old.at == new.at and (old.at is None or old.tz == new.tz)


def _payload_value(value: MilestoneValue) -> dict[str, str | None]:
    return {
        "on": value.on.isoformat() if value.on else None,
        "at": value.at.isoformat() if value.at else None,
        "tz": value.tz if value.at else None,
    }


def _publish(
    session: Session,
    owner: MilestoneOwner,
    milestone_type: str,
    change: Any,
    old: MilestoneValue,
    new: MilestoneValue,
) -> None:
    """event_type·aggregate = 소유자별(선적 = `shipments.milestone.changed`·shipments / OEM = `purchase_orders.milestone.changed`·
    purchase_orders) — payload `owner_type`·`owner_id`(design-integrated §2.7, OEM 이름 분리 = 적대 검토 반영 ⑤)."""
    outbox.publish(
        session,
        event_type=CHANGED_EVENTS[owner.owner_type],
        aggregate_type=DOC_TABLES[DocKind(owner.owner_type)],
        aggregate_id=owner.owner_id,
        payload={
            "owner_type": owner.owner_type,
            "owner_id": owner.owner_id,
            "milestone_type": milestone_type,
            "change_id": change.id,
            "change_kind": change.change_kind,
            "old": _payload_value(old),
            "new": _payload_value(new),
        },
    )


def _write(
    session: Session,
    *,
    actor: AuthenticatedUser,
    owner: MilestoneOwner,
    milestone: Milestone | None,
    milestone_type: str,
    kind: str,
    old: MilestoneValue,
    new: MilestoneValue,
    reason: str | None,
    planned: bool,
) -> dict[str, Any]:
    """값 대입 + 이력 1행 + 아웃박스 1건(같은 TX) → 응답의 change 조각."""
    if milestone is None:
        milestone = shipments.insert_milestone(
            session, owner=owner, milestone_type=milestone_type, actor_id=actor.id
        )
    if planned:
        shipments.set_planned(milestone, new, actor_id=actor.id)
    else:
        shipments.set_actual(milestone, new, actor_id=actor.id)
    shipments.flush_translated(session)
    change = shipments.insert_change(
        session,
        milestone=milestone,
        change_kind=kind,
        old=old,
        new=new,
        reason=reason,
        actor_id=actor.id,
    )
    _publish(session, owner, milestone_type, change, old, new)
    return {"id": change.id, "change_kind": change.change_kind}


def _finish(
    session: Session,
    claim: idempotency.Claim,
    board: Callable[[], dict[str, Any]],
    change: dict[str, Any] | None,
) -> tuple[int, dict[str, Any]]:
    """멱등 저장 본문 = 결정적인 `change`만(보드는 재생 때 새로 조립 — 적대 검토 반영 ⑨). `board` = 소유자별 보드 조립기(쓰기 뒤 호출)."""
    assert claim.record is not None
    idempotency.complete(session, claim.record, status_code=200, body={"change": change})
    return 200, {"board": board(), "change": change}


def _replay_with_board(
    session: Session, shipment_id: int, replay: idempotency.Replay
) -> tuple[int, dict[str, Any]]:
    """같은 Idempotency-Key 재요청 — 저장된 `change`(같은 id)에 **지금 시점의 보드**를 붙인다(낡은 today_kst·판정 재생 0)."""
    row = shipments.require_shipment(session, shipment_id)
    return replay.status_code, {
        "board": board_body(session, row),
        "change": replay.body.get("change"),
    }


# ── 계획·실적 본체 (소유자 공통 — 선적 T6·T7 / OEM T13) ─────────────────────────────


def _plan_change(
    session: Session,
    *,
    actor: AuthenticatedUser,
    owner: MilestoneOwner,
    milestone_type: str,
    payload: dict[str, Any],
    require_writable: Callable[[], None],
) -> dict[str, Any] | None:
    """계획 설정(PLAN_SET)·변경(PLAN_CHANGED = 롤오버, 사유 필수) 본체 — 소유 헤더 잠금·소유 상태 409를 끝낸 호출자가 부른다.
    행 `FOR UPDATE` → version 409 → `require_writable`(소유자별 종류 422) → 범위·형태·시간대 422 → 사유 422 → 대입·이력·아웃박스.
    같은 값 = no-op(None — 이력 0)."""
    milestone = shipments.find_milestone(session, owner, milestone_type, for_update=True)
    _require_version(milestone, payload.get("version"))
    require_writable()
    new = _value(milestone_type, payload, on_key="planned_on", at_key="planned_at", required=True)
    _require_shared_zone(milestone, new, other_at=milestone.actual_at if milestone else None)
    reason = _reason(payload.get("reason"))
    old = (
        MilestoneValue(milestone.planned_on, milestone.planned_at, milestone.tz)
        if milestone is not None
        else MilestoneValue()
    )
    if _same(old, new):
        return None
    kind = (
        MilestoneChangeKind.PLAN_SET.value if old.empty else MilestoneChangeKind.PLAN_CHANGED.value
    )
    if kind == MilestoneChangeKind.PLAN_CHANGED.value and reason is None:
        raise AppError(
            ErrorCode.SHIPMENTS_MILESTONE_REASON_REQUIRED,
            detail={"reason": "계획 변경(롤오버) 사유를 입력해 주세요."},
        )
    return _write(
        session,
        actor=actor,
        owner=owner,
        milestone=milestone,
        milestone_type=milestone_type,
        kind=kind,
        old=old,
        new=new,
        reason=reason,
        planned=True,
    )


def _actual_change(
    session: Session,
    *,
    actor: AuthenticatedUser,
    owner: MilestoneOwner,
    milestone_type: str,
    payload: dict[str, Any],
    allow: Callable[[MilestoneValue], None],
    require_writable: Callable[[], None],
) -> dict[str, Any] | None:
    """실적 기록(ACTUAL_RECORDED)·정정/삭제(ACTUAL_CORRECTED — 사유 필수) 본체. 행 `FOR UPDATE` → version 409 → `require_writable`(종류 422)
    → 값 키 422 → 범위·형태·시간대 422 → `allow`(소유자별 실적 규율 — 미래 금지 공통 + 선적 출고 결속) → 사유 422 → 대입·이력·아웃박스.
    값 필드는 명시해야 한다(키 없음 = 422 — 실수로 실적을 지우는 빈 본문 차단, 지우기 = 명시적 null + 사유)."""
    milestone = shipments.find_milestone(session, owner, milestone_type, for_update=True)
    _require_version(milestone, payload.get("version"))
    require_writable()
    if "actual_on" not in payload and "actual_at" not in payload:
        field = "actual_at" if milestone_type in DATETIME_MILESTONES else "actual_on"
        raise invalid(field, "실적 값을 보내 주세요(지우려면 null과 사유).")
    new = _value(milestone_type, payload, on_key="actual_on", at_key="actual_at", required=False)
    _require_shared_zone(milestone, new, other_at=milestone.planned_at if milestone else None)
    allow(new)
    reason = _reason(payload.get("reason"))
    old = (
        MilestoneValue(milestone.actual_on, milestone.actual_at, milestone.tz)
        if milestone is not None
        else MilestoneValue()
    )
    if _same(old, new):
        return None
    kind = (
        MilestoneChangeKind.ACTUAL_RECORDED.value
        if old.empty
        else MilestoneChangeKind.ACTUAL_CORRECTED.value
    )
    if kind == MilestoneChangeKind.ACTUAL_CORRECTED.value and reason is None:
        raise AppError(
            ErrorCode.SHIPMENTS_MILESTONE_REASON_REQUIRED,
            detail={"reason": "실적 정정 사유를 입력해 주세요."},
        )
    return _write(
        session,
        actor=actor,
        owner=owner,
        milestone=milestone,
        milestone_type=milestone_type,
        kind=kind,
        old=old,
        new=new,
        reason=reason,
        planned=False,
    )


# ── 계획 (T6 — 설정·롤오버) ────────────────────────────────────────────────────


def record_milestone_plan(
    *,
    actor: AuthenticatedUser,
    idempotency_key: str,
    shipment_id: int,
    milestone_type: str,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    """계획 설정(PLAN_SET)·변경(PLAN_CHANGED = 롤오버, 사유 필수). 같은 값이면 no-op(change = null, 이력 0)."""
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=PLAN_ENDPOINT,
            key=idempotency_key,
            request_body={"shipment_id": shipment_id, "milestone_type": milestone_type, **payload},
        )
        if claim.replay is not None:
            return _replay_with_board(session, shipment_id, claim.replay)
        row = lock_document(session, Shipment, shipment_id)  # 404 · shipments FOR UPDATE
        _require_owner_active(row)
        # 409(행 version)가 422(종류 쓰기 불가)보다 먼저 — ADR-0079 ⑧(적대 검토 반영 ⑦). 쓸 수 없는 종류는 행이 없어 version 생략이 정답
        change = _plan_change(
            session,
            actor=actor,
            owner=MilestoneOwner.of_shipment(row.id),
            milestone_type=milestone_type,
            payload=payload,
            require_writable=lambda: _require_shipment_type_writable(row, milestone_type),
        )
        return _finish(session, claim, lambda: board_body(session, row), change)


# ── 실적 (T7 — 기록·정정) ─────────────────────────────────────────────────────


def _require_not_future(value: MilestoneValue) -> None:
    """실적 미래 금지(소유자 공통) — 날짜형 KST 오늘+1일까지(현지 날짜 여유), 시각형 현재 UTC까지(여유 0 — R-18)."""
    if value.empty:
        return
    if value.at is not None and value.at > utcnow():
        raise AppError(
            ErrorCode.SHIPMENTS_MILESTONE_ACTUAL_IN_FUTURE,
            detail={"actual_at": "아직 오지 않은 시각입니다."},
        )
    if value.on is not None and value.on > today_kst() + ACTUAL_DATE_SLACK:
        raise AppError(
            ErrorCode.SHIPMENTS_MILESTONE_ACTUAL_IN_FUTURE,
            detail={"actual_on": "아직 오지 않은 날짜입니다."},
        )


def _require_actual_allowed(row: Shipment, milestone_type: str, value: MilestoneValue) -> None:
    """선적 실적 값 규율 — 미래 금지(공통) · ETD·B/L·ETA는 출고지시 뒤에만(R-01)."""
    if value.empty:
        return
    _require_not_future(value)
    if milestone_type in RELEASE_BOUND_ACTUALS and row.status not in ACTUAL_RELEASE_STATES:
        raise AppError(
            ErrorCode.SHIPMENTS_MILESTONE_ACTUAL_BEFORE_RELEASE,
            detail={"milestone_type": milestone_type},
            log_context={"shipment_id": row.id, "status": row.status},
        )


def record_milestone_actual(
    *,
    actor: AuthenticatedUser,
    idempotency_key: str,
    shipment_id: int,
    milestone_type: str,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    """실적 기록(ACTUAL_RECORDED)·정정/삭제(ACTUAL_CORRECTED — 사유 필수). 값 필드는 명시해야 한다(지우기 = 명시적 null).

    `payload`는 **보낸 필드만**(exclude_unset) — 값 필드를 아예 안 보낸 요청은 422(실수로 실적을 지우는 빈 본문 차단).
    """
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=ACTUAL_ENDPOINT,
            key=idempotency_key,
            request_body={"shipment_id": shipment_id, "milestone_type": milestone_type, **payload},
        )
        if claim.replay is not None:
            return _replay_with_board(session, shipment_id, claim.replay)
        row = lock_document(session, Shipment, shipment_id)
        _require_owner_active(row)
        # 409(행 version)가 422(종류·신고수리 실적)보다 먼저 — ADR-0079 ⑧(적대 검토 반영 ⑦)
        change = _actual_change(
            session,
            actor=actor,
            owner=MilestoneOwner.of_shipment(row.id),
            milestone_type=milestone_type,
            payload=payload,
            allow=lambda value: _require_actual_allowed(row, milestone_type, value),
            require_writable=lambda: _require_shipment_actual_writable(row, milestone_type),
        )
        return _finish(session, claim, lambda: board_body(session, row), change)


# ── 계획 초안 (M4 — 사람 1클릭, 자동 생성 0) ─────────────────────────────────────


def _draft_types(session: Session, row: Shipment) -> frozenset[str]:
    """적용 종류 = 구분별 적용 집합 ∩ 라인 SKU 품목군 세트 합집합(B16). SKU에 품목군이 없거나 세트 미정의가 하나라도 있으면 구분별 전부
    (누락보다 과다가 안전하다 — fail-closed)."""
    applicable = SHIPMENT_MILESTONES_BY_KIND.get(row.shipment_kind, frozenset())
    profiles = list(
        session.execute(
            select(Sku.item_profile_id)
            .join(ShipmentLine, ShipmentLine.sku_id == Sku.id)
            .where(ShipmentLine.shipment_id == row.id, ShipmentLine.deleted_at.is_(None))
        ).scalars()
    )
    if not profiles or any(profile is None for profile in profiles):
        return applicable
    sets = shipments.profile_milestone_sets(session, {int(p) for p in profiles if p is not None})
    if any(int(p) not in sets for p in profiles if p is not None):
        return applicable
    union: set[str] = set()
    for found in sets.values():
        union |= found
    return applicable & frozenset(union)


def draft_milestone_plan(
    *, actor: AuthenticatedUser, idempotency_key: str, shipment_id: int
) -> tuple[int, dict[str, Any]]:
    """계획 초안 — 적용 종류의 **빈 계획 행**을 만든다(이미 있는 종류는 건너뜀, 값 0 → 이력·이벤트 0). 같은 키 1회."""
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=DRAFT_ENDPOINT,
            key=idempotency_key,
            request_body={"shipment_id": shipment_id},
        )
        if claim.replay is not None:
            # 재생 = 지금 시점의 보드(저장 본문은 결정적 표지뿐 — 적대 검토 반영 ⑨)
            return claim.replay.status_code, board_body(
                session, shipments.require_shipment(session, shipment_id)
            )
        row = lock_document(session, Shipment, shipment_id)
        _require_owner_active(row)
        existing = set(
            session.execute(
                select(Milestone.milestone_type).where(
                    Milestone.shipment_id == row.id, Milestone.deleted_at.is_(None)
                )
            ).scalars()
        )
        created = sorted(_draft_types(session, row) - existing)
        owner = MilestoneOwner.of_shipment(row.id)
        for milestone_type in created:
            shipments.insert_milestone(
                session, owner=owner, milestone_type=milestone_type, actor_id=actor.id
            )
        assert claim.record is not None
        idempotency.complete(
            session, claim.record, status_code=200, body={"created_types": created}
        )
        return 200, board_body(session, row)


# ── 통보 기록 (T8 — comm_logs SHIPMENT 1행 + 연결 1행, 발송 0) ────────────────────────


def _lock_shipment_for_share(session: Session, shipment_id: int) -> Shipment:
    row = session.execute(
        select(Shipment)
        .where(Shipment.id == shipment_id, Shipment.deleted_at.is_(None))
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(log_context={"shipment_id": shipment_id})
    return row


def _summary(raw: object) -> str:
    text = str(raw or "")
    if any(is_invisible_char(ch) for ch in text if ch not in _SUMMARY_LINE_BREAKS):
        raise invalid(
            "summary",
            "요지에 보이지 않는 글자(제어·서식·채움 문자)가 있습니다 — 줄바꿈·탭만 쓸 수 있습니다.",
        )
    cleaned = text.strip()
    if not cleaned:
        raise invalid("summary", "요지를 입력해 주세요.")
    return cleaned


def record_milestone_notice(
    *,
    actor: AuthenticatedUser,
    idempotency_key: str,
    shipment_id: int,
    change_id: int,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    """롤오버(변경) 통보 기록 — comm_logs(SHIPMENT 주제) 1행 + `milestone_change_notices` 1행을 1TX로. **메일·메신저 발송 코드 0**.

    오류 우선순위: 선적·변경 404(무잠금 peek) → 취소 선적 409 → 입력 422(오간 날·요지·상대 거래처 유형) → 잠금 뒤 재확인(TOCTOU).
    """
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=NOTICE_ENDPOINT,
            key=idempotency_key,
            request_body={"shipment_id": shipment_id, "change_id": change_id, **payload},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body
        peek = shipments.require_shipment(session, shipment_id)  # 무잠금 peek(404)
        change = shipments.require_change(session, peek.id, change_id)  # 부모-자식 404
        _require_owner_active(peek)
        occurred_on: date = payload["occurred_on"]
        if occurred_on < BUSINESS_DATE_MIN:
            raise invalid("occurred_on", BUSINESS_DATE_MESSAGE)
        if occurred_on > today_kst():
            raise invalid("occurred_on", "실제로 알린 날은 오늘 이후일 수 없습니다.")
        summary = _summary(payload.get("summary"))
        partner_id = payload.get("counterpart_partner_id")
        if partner_id is not None:
            partners.require_partner_of_any_type(
                session,
                int(partner_id),
                NOTICE_PARTNER_TYPES,
                field="counterpart_partner_id",
                type_label="포워더·관세사·3PL·바이어·공급사·OEM",
                lock=True,  # partners FOR KEY SHARE — 선적 잠금보다 먼저(R-08)
            )
        row = _lock_shipment_for_share(session, shipment_id)
        _require_owner_active(row)  # 잠금 뒤 재확인(peek와 잠금 사이 취소)
        # 변경 1건당 통보 상한 — 소유 마일스톤 행 FOR UPDATE 아래에서 센다(동시 통보 경합에 안전 — 적대 검토 반영 ⑧)
        if shipments.lock_notice_slot(session, change) >= NOTICE_LIMIT_PER_CHANGE:
            raise AppError(
                ErrorCode.SHIPMENTS_MILESTONE_NOTICE_LIMIT_REACHED,
                detail={
                    "change_id": f"이 변경의 통보 기록은 {NOTICE_LIMIT_PER_CHANGE}건까지입니다."
                },
            )
        log = collaboration.record_shipment_comm_log(
            session,
            shipment_id=row.id,
            partner_id=int(partner_id) if partner_id is not None else None,
            occurred_on=occurred_on,
            summary=summary,
            actor_id=actor.id,
        )
        shipments.flush_translated(session)
        shipments.insert_notice(session, change=change, comm_log_id=log.id, actor_id=actor.id)
        body = change_body(session, change)
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


# ── OEM 생산 일정 (T13 — PO 소유 4종: 원료수급·충진·포장·출하검사, 무역·관리자) ─────────────────


def _require_po_active(po: PoOwner) -> None:
    """취소된 PO(또는 S4-1 전까지 닿을 수 없는 입고·종결 상태)의 생산 일정 쓰기 = 409 OWNER_NOT_ACTIVE(N-05 — 선적과 같은 코드)."""
    if po.status not in OEM_RECORD_EDITABLE_STATES:
        raise AppError(
            ErrorCode.SHIPMENTS_MILESTONE_OWNER_NOT_ACTIVE,
            detail={"owner_type": po.owner.owner_type},
            log_context={"purchase_order_id": po.id, "status": po.status},
            message_override=PO_INACTIVE_MESSAGE,
        )


def _lock_oem_owner(session: Session, po_id: int) -> PoOwner:
    """T13 소유 잠금 — PO `FOR SHARE`(없으면 404) → 취소 409 OWNER_NOT_ACTIVE. PO 무수정(상태·구분만 읽는다)."""
    po = po_owner(session, po_id, lock=True)
    _require_po_active(po)
    return po


def _require_oem_writable(po: PoOwner, milestone_type: str) -> None:
    """소유자·종류 422 — 일반 구매 PO = OWNER_NOT_OEM → 파생 = DERIVED_NOT_EDITABLE → OEM 4종 밖 = TYPE_NOT_APPLICABLE.
    행 version 409 **뒤**에 판정한다(선적 경로와 같은 4a 개정 순서 — 일반 구매 PO에는 행이 없어 version 생략이면 곧장 422)."""
    require_oem(po)
    _require_type_writable(milestone_type, OEM_MILESTONES, {"po_kind": po.po_kind})


def _oem_replay(
    session: Session, replay: idempotency.Replay, po_id: int, actor: AuthenticatedUser
) -> tuple[int, dict[str, Any]]:
    """멱등 재생 = 저장된 change + **지금** 보드(재조립 — 선적 `_replay_with_board`와 같은 규율: 그사이 다른 변경·PO 취소가 보드·버튼에 보인다)."""
    po = po_owner(session, po_id, lock=False)
    return replay.status_code, {
        "board": oem_board_body(session, po, actor.roles),
        "change": replay.body.get("change"),
    }


def record_oem_milestone_plan(
    *,
    actor: AuthenticatedUser,
    idempotency_key: str,
    po_id: int,
    milestone_type: str,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    """OEM 생산 일정 계획 설정(PLAN_SET)·변경(PLAN_CHANGED, 사유 필수) — 본체는 선적과 같은 `_plan_change`(범위·사유 정화 공유)."""
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=OEM_PLAN_ENDPOINT,
            key=idempotency_key,
            request_body={"po_id": po_id, "milestone_type": milestone_type, **payload},
        )
        if claim.replay is not None:
            return _oem_replay(session, claim.replay, po_id, actor)
        po = _lock_oem_owner(session, po_id)
        change = _plan_change(
            session,
            actor=actor,
            owner=po.owner,
            milestone_type=milestone_type,
            payload=payload,
            require_writable=lambda: _require_oem_writable(po, milestone_type),
        )
        return _finish(session, claim, lambda: oem_board_body(session, po, actor.roles), change)


def record_oem_milestone_actual(
    *,
    actor: AuthenticatedUser,
    idempotency_key: str,
    po_id: int,
    milestone_type: str,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    """OEM 생산 일정 실적 기록·정정(사유 필수) — 미래 금지(KST 오늘+1일 여유)만, 출고 결속 없음. 본체는 선적과 같은 `_actual_change`."""
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=OEM_ACTUAL_ENDPOINT,
            key=idempotency_key,
            request_body={"po_id": po_id, "milestone_type": milestone_type, **payload},
        )
        if claim.replay is not None:
            return _oem_replay(session, claim.replay, po_id, actor)
        po = _lock_oem_owner(session, po_id)
        change = _actual_change(
            session,
            actor=actor,
            owner=po.owner,
            milestone_type=milestone_type,
            payload=payload,
            allow=_require_not_future,
            require_writable=lambda: _require_oem_writable(po, milestone_type),
        )
        return _finish(session, claim, lambda: oem_board_body(session, po, actor.roles), change)
