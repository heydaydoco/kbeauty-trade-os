"""담당 이관 대상 등록소 (DESIGN.md §2 "담당 이관" / ADR-0015).

★ 여기 없는 테이블은 이관되지 않는다 — 그리고 **그 누락은 조용하다**.
  퇴사자의 담당 건이 일부만 넘어가면, 남은 건들은 아무에게도 안 보이는 채로
  기일을 넘긴다. 그래서 "담당자 컬럼을 가진 모델은 전부 여기 등록됐는가"를
  tests/architecture/test_assignment_coverage.py가 기계로 검사한다.

새 테이블에 담당자 컬럼을 추가하는 세션(Phase 3의 전표, Phase 2의 인증 등)은
이 목록에 한 줄을 더한다. 안 더하면 테스트가 실패한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import InstrumentedAttribute

from app.modules.certifications.models import Certification
from app.modules.order_intake.models import OrderIntake
from app.modules.proforma_invoices.models import ProformaInvoice
from app.modules.purchase_orders.models import PurchaseOrder
from app.modules.quotations.models import Quotation
from app.modules.sales_orders.models import SalesOrder
from app.modules.shipments.models import Shipment
from app.modules.worklist.models import Alert, AlertRule, Task

#: 담당자를 가리키는 컬럼 이름들. 감사 컬럼(created_by_id·updated_by_id)과
#: 행위 기록(audit_log.actor_user_id)은 **담당이 아니라 이력**이라 제외한다 —
#: 과거에 누가 했는지는 이관으로 바뀌지 않는다.
ASSIGNMENT_COLUMN_NAMES: frozenset[str] = frozenset(
    {
        "assignee_id",
        "recipient_user_id",
        "owner_user_id",
        "in_charge_user_id",
    }
)


@dataclass(frozen=True, slots=True)
class AssignmentTarget:
    """이관 대상 한 곳 — 모델과 담당자 컬럼."""

    label: str
    model: type[Any]
    column: InstrumentedAttribute[Any]


ASSIGNMENT_TARGETS: tuple[AssignmentTarget, ...] = (
    AssignmentTarget("tasks", Task, Task.assignee_id),
    AssignmentTarget("alert_rules", AlertRule, AlertRule.recipient_user_id),
    AssignmentTarget("alerts", Alert, Alert.recipient_user_id),
    # S2-2 — §2 "담당 건(전표·인증·태스크·알림)"의 인증 명시분.
    AssignmentTarget("certifications", Certification, Certification.assignee_id),
    # S3-1 — 전표 담당자(§2 "담당 건(전표…)"). 동결 후에도 FREE 열이라 이관이 통과한다(ADR-0053).
    # 오더 인테이크(PR-13a) — 담당자는 이관 단위다(PENDING 건만 의미가 있으나 종결 건도 담당 이력 표시를 위해 대상에 둔다). 일괄 UPDATE는 version을 올리지 않는다.
    # ★ 순서가 곧 잠금 순서다(이관은 표마다 UPDATE로 행을 잠근다): **order_intakes를 전표보다 먼저**(전역 LOCK_ORDER (1) 인테이크 → … → (5) SO → (6) PO) —
    # 복제 인테이크 확정(인테이크 → 원본 SO 잠금)과 교차해도 사이클이 없고, 이관이 확정이 만든 SO를 놓치지 않는다(test_handover_follows_the_global_lock_order).
    AssignmentTarget("order_intakes", OrderIntake, OrderIntake.assignee_id),
    AssignmentTarget("quotations", Quotation, Quotation.assignee_id),
    AssignmentTarget("proforma_invoices", ProformaInvoice, ProformaInvoice.assignee_id),
    AssignmentTarget("sales_orders", SalesOrder, SalesOrder.assignee_id),
    AssignmentTarget("purchase_orders", PurchaseOrder, PurchaseOrder.assignee_id),
    # S3-2 PR-3a — 선적 담당자(FREE 열, 원천 담당자 사본). LOCK_ORDER (6) PO → (7) shipments 순서 그대로 PO 다음(ADR-0078).
    AssignmentTarget("shipments", Shipment, Shipment.assignee_id),
)


#: 담당 이관 대상이 **아닌** users FK의 분류 (S3-1 ADR-0067).
#: 새 테이블이 users를 가리키는 컬럼을 만들면 이관 대상(위 ASSIGNMENT_TARGETS)인지, 아래 둘 중
#: 하나(이력·신원 연결)인지 반드시 정하게 한다 — `approver_id`·`delegate_id`처럼 이름이 담당
#: 탐지 집합 밖인 컬럼이 조용히 이관에서 빠지는 것을 막는다(tests/architecture/
#: test_user_fk_classification.py).
#:   ACTOR_LOG     — 누가 했는가의 이력. 이관으로 바뀌지 않는다(예: audit_log.actor_user_id).
#:   IDENTITY_LINK — 그 사용자 자신에 속한 행(예: user_roles.user_id). 이관 대상이 아니다.
USER_FK_CLASSIFICATION: dict[tuple[str, str], str] = {
    ("audit_log", "actor_user_id"): "ACTOR_LOG",
    ("idempotency_keys", "actor_user_id"): "ACTOR_LOG",
    ("certification_status_log", "actor_user_id"): "ACTOR_LOG",
    ("quotation_status_log", "actor_user_id"): "ACTOR_LOG",
    ("proforma_invoice_status_log", "actor_user_id"): "ACTOR_LOG",
    ("sales_order_status_log", "actor_user_id"): "ACTOR_LOG",
    (
        "order_intakes",
        "decided_by_id",
    ): "ACTOR_LOG",  # PR-13a — 확정·거부 결정자(이력 — 이관으로 바뀌지 않는다)
    ("purchase_order_status_log", "actor_user_id"): "ACTOR_LOG",
    (
        "shipment_status_log",
        "actor_user_id",
    ): "ACTOR_LOG",  # S3-2 PR-3a — 선적 상태이력 행위자(이력)
    # S3-2 PR-4a — 마일스톤 변경 이력·통보 연결의 행위자(불변 이력 — 이관으로 바뀌지 않는다)
    ("milestone_changes", "actor_user_id"): "ACTOR_LOG",
    ("milestone_change_notices", "actor_user_id"): "ACTOR_LOG",
    ("import_staging", "confirmed_by_id"): "ACTOR_LOG",
    # S3-1 PR-9a — 승인 4표(ADR-0061). 승인 대기 건은 **역할 기반**이라 이관 대상이 아니다(requested_by·decided_by는 이력이고,
    # 대결은 개인 결재 권한의 임시 위탁이라 이관하지 않는다 — 이관 도구가 대상 계정을 비활성화하면 대결은 계산 술어로 즉시 무효).
    # 기안자 퇴사로 남은 REQUESTED는 결재자 반려·ADMIN 회수·정체 독촉으로 정리한다. ASSIGNMENT 이름 4종 컬럼을 두지 않는다.
    ("approvals", "requested_by_id"): "ACTOR_LOG",
    ("approvals", "decided_by_id"): "ACTOR_LOG",
    ("approvals", "decided_on_behalf_of_id"): "ACTOR_LOG",
    ("approvals", "consumed_by_id"): "ACTOR_LOG",
    (
        "payments",
        "recorded_by_id",
    ): "ACTOR_LOG",  # PR-10a — 입금 기록자(불변 원장의 행위자 — 이관으로 바뀌지 않는다)
    # PR-11a — 게이트 증적(불변): 평가자·override 부여자는 누가 했는가의 이력이다(이관으로 바뀌지 않는다).
    ("gate_evaluations", "evaluated_by_id"): "ACTOR_LOG",
    ("gate_overrides", "granted_by_id"): "ACTOR_LOG",
    ("approval_events", "actor_user_id"): "ACTOR_LOG",
    ("approval_events", "on_behalf_of_user_id"): "ACTOR_LOG",
    (
        "delegations",
        "delegator_user_id",
    ): "IDENTITY_LINK",  # 개인 결재 권한의 위탁자 — 그 사람 자신에 속한 행
    ("delegations", "delegate_user_id"): "IDENTITY_LINK",  # 수임자 — 개인 권한(이관 대상 아님)
    ("delegations", "revoked_by_id"): "ACTOR_LOG",
    ("user_roles", "user_id"): "IDENTITY_LINK",
    ("user_sessions", "user_id"): "IDENTITY_LINK",
    # S3-1 PR-15a — 오더 보드 저장 필터의 소유자(개인 설정 — 그 사람 자신에 속한 행, 이관 대상 아님. 설계 PERSONAL 분류).
    ("board_saved_filters", "user_id"): "IDENTITY_LINK",
}

#: 모든 테이블에 붙는 감사 컬럼 — 분류 대상이 아니다.
AUDIT_USER_FK_COLUMNS: frozenset[str] = frozenset({"created_by_id", "updated_by_id"})
