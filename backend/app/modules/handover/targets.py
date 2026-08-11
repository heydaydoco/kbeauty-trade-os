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
)
