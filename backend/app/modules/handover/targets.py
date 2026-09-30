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
from app.modules.proforma_invoices.models import ProformaInvoice
from app.modules.quotations.models import Quotation
from app.modules.sales_orders.models import SalesOrder
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
    # 나머지 전표(PO)와 order_intakes는 각 PR이 자기 행을 더한다.
    AssignmentTarget("quotations", Quotation, Quotation.assignee_id),
    AssignmentTarget("proforma_invoices", ProformaInvoice, ProformaInvoice.assignee_id),
    AssignmentTarget("sales_orders", SalesOrder, SalesOrder.assignee_id),
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
    ("import_staging", "confirmed_by_id"): "ACTOR_LOG",
    ("user_roles", "user_id"): "IDENTITY_LINK",
    ("user_sessions", "user_id"): "IDENTITY_LINK",
}

#: 모든 테이블에 붙는 감사 컬럼 — 분류 대상이 아니다.
AUDIT_USER_FK_COLUMNS: frozenset[str] = frozenset({"created_by_id", "updated_by_id"})
