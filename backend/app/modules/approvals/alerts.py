"""승인 알림 — 요청·결과·대결 사용·정체 독촉 (S3-1 ADR-0061 / design-C C7).

■ **`notifications.notify()` 직접 호출**(아웃박스 비경유 — 기일 엔진·정체 스캔 선례). 요청 트랜잭션 안에서 만든다: "승인 행이 있으면
  알림도 있다"(롤백되면 알림도 없다). `Routing`은 3값 그대로 쓴다.
■ **요청·독촉은 `Routing.DEADLINE` + 수신자별 `assignee_id`** — 수신자를 이미 활성으로 골라 넘기므로 폴백은 평시 발동하지 않고,
  넘긴 뒤 비활성이 되는 경합에서만 ADMIN 폴백으로 관통한다(fail-visible). `alert_rules`가 0행이어도 발송된다.
■ **결과 통지(승인·반려·무효·회수·대결 사용)는 `Routing.EVENT`** — 기안자·위임자가 비활성이면 **생략**이다(폴백 없음 — 퇴사자 앞으로
  쌓이는 알림은 아무도 읽지 않는다).
■ 알림 **제목·본문에 금액·이메일·원가를 싣지 않는다**(확장 시 누출 방지) — 전표번호(`target_label`)와 상태만. dedup 키 꼬리(수신자 id)는
  코어가 붙인다(handover 재작성 호환).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.modules.approvals.authority import kst_today, notification_recipients
from app.modules.approvals.machine import ApprovalStatus
from app.modules.approvals.models import Approval
from app.modules.identity.service import active_roles_of
from app.modules.notifications import service as notifications
from app.modules.notifications.service import Routing

#: `alerts.title` 컬럼 길이(deadlines.service.ALERT_TITLE_MAX와 같은 값 — 테스트가 대사한다).
ALERT_TITLE_MAX = 200

#: 정체 독촉 규칙 event_type(관리자가 이 종류로 규칙을 만들면 N을 `config.days`로 덮어쓴다).
STAGNATION_EVENT = "approvals.stagnation"

#: 승인 폴백 문구 — 코어의 `FALLBACK_NOTE`("규칙이 없어서")와 뜻이 달라 쓰지 않는다.
ROLE_FALLBACK_NOTE = "※ 결재 역할 보유자가 없어 관리자에게 전달되었습니다."

_RESULT_TITLES: dict[str, str] = {
    "approved": "승인되었습니다",
    "rejected": "반려되었습니다",
    "voided": "무효 처리되었습니다",
    "withdrawn": "회수되었습니다",
}


def _title(text: str) -> str:
    return text if len(text) <= ALERT_TITLE_MAX else text[: ALERT_TITLE_MAX - 1] + "…"


def notify_requested(session: Session, approval: Approval, *, today: date | None = None) -> int:
    """결재 자격자에게 요청 알림 — 역할 보유자·유효 수임자, 없으면 ADMIN 폴백(본문에 안내). 생성된 알림 수를 돌려준다."""
    recipients, fallback = notification_recipients(session, approval, today=today or kst_today())
    body = f"승인 요청이 도착했습니다 — {approval.target_label}. 결재함에서 내용을 확인하고 결정해 주세요."
    if fallback:
        body = f"{body}\n{ROLE_FALLBACK_NOTE}"
    created = 0
    for user_id in recipients:
        created += len(
            notifications.notify(
                session,
                subject_key=f"approval:{approval.id}:requested",
                title=_title(f"승인 요청 — {approval.target_label}"),
                body=body,
                severity="WARN",
                event_type="approvals.approval.requested",
                assignee_id=user_id,
                routing=Routing.DEADLINE,
                entity_type="approvals",
                entity_id=approval.id,
            )
        )
    return created


def notify_result(session: Session, approval: Approval, *, outcome: str, actor_user_id: int) -> int:
    """결과(승인·반려·무효·회수)를 기안자에게 — 기안자 비활성이면 생략, 본인이 한 행위(자기 회수·자기 편집 무효)는 알리지 않는다."""
    if outcome not in _RESULT_TITLES or approval.requested_by_id == actor_user_id:
        return 0
    if active_roles_of(session, approval.requested_by_id) is None:
        return 0
    return len(
        notifications.notify(
            session,
            subject_key=f"approval:{approval.id}:{outcome}",
            title=_title(f"승인 {_RESULT_TITLES[outcome]} — {approval.target_label}"),
            body=f"요청하신 승인({approval.target_label})이 {_RESULT_TITLES[outcome]}. 승인 현황에서 내용을 확인해 주세요.",
            severity="INFO",
            event_type=f"approvals.approval.{outcome}",
            assignee_id=approval.requested_by_id,
            routing=Routing.EVENT,
            entity_type="approvals",
            entity_id=approval.id,
        )
    )


def notify_delegated(session: Session, approval: Approval, *, delegator_id: int) -> int:
    """대결로 결정이 내려졌음을 위임자에게 — 위임자가 모르는 행사를 막는다(fail-visible). 위임자 비활성이면 생략."""
    if active_roles_of(session, delegator_id) is None:
        return 0
    return len(
        notifications.notify(
            session,
            subject_key=f"approval:{approval.id}:delegated",
            title=_title(f"대결로 결재되었습니다 — {approval.target_label}"),
            body=f"위임하신 대결로 승인({approval.target_label})이 결정되었습니다. 결정 내용은 승인 이력에서 확인해 주세요.",
            severity="INFO",
            event_type="approvals.approval.delegated",
            assignee_id=delegator_id,
            routing=Routing.EVENT,
            entity_type="approvals",
            entity_id=approval.id,
        )
    )


def notify_stagnation(
    session: Session, approval: Approval, *, stagnant_days: int, period: int, rule: object | None
) -> int:
    """정체 독촉 — 요청 알림과 **같은 수신자 결정**. dedup k = 경과일÷N(N일마다 1건)."""
    if approval.status != ApprovalStatus.REQUESTED.value:
        return 0
    recipients, fallback = notification_recipients(session, approval)
    body = (
        f"결재 대기 {stagnant_days}일째입니다 — {approval.target_label}. 결재함에서 결정해 주세요."
    )
    if fallback:
        body = f"{body}\n{ROLE_FALLBACK_NOTE}"
    created = 0
    for user_id in recipients:
        created += len(
            notifications.notify(
                session,
                subject_key=f"approval:{approval.id}:stagnation#{period}",
                title=_title(f"결재 대기 {stagnant_days}일 — {approval.target_label}"),
                body=body,
                severity="WARN",
                event_type=STAGNATION_EVENT,
                rule=rule,  # type: ignore[arg-type]
                assignee_id=user_id,
                routing=Routing.DEADLINE,
                entity_type="approvals",
                entity_id=approval.id,
            )
        )
    return created
