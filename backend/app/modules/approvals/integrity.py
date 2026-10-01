"""승인 무결성 대사 — 승인 본체가 이력(approval_events)으로 재구성되는가 (S3-1 PR-9a 적대 검토 반영 / ADR-0060 부기).

앱 계정은 `approvals.status`·`decided_*`·`consumed_*`를 UPDATE할 수 있어야 한다(전이 통로가 쓴다) — DB 권한만으로는 "이력 없이 상태를 바꾸는" 위조(INSERT·UPDATE로
APPROVED를 직접 만들기)를 막지 못하고, 트리거는 이 리포가 채택하지 않는다(ADR-0028·0040). 그래서 **탐지**를 둔다: 모든 승인 행은 이력 이벤트만으로 다시 만들어져야 한다.
  ① 첫 이벤트는 `NULL→REQUESTED` ② 이벤트 사슬이 끊김 없이 이어진다(각 from = 직전 to) ③ 마지막 이벤트의 to = 현재 status
  ④ 승인·반려 이벤트의 행위자·위임자·대결 id = 승인 행의 decided_* ⑤ 소비 이벤트의 행위자 = consumed_by_id(소비 컬럼은 소비 이벤트가 있을 때만).
읽기 전용이다 — 상태를 고치지 않는다. 주기 실행(잡 배선)은 부채로 등재했다(PROGRESS: DB 레벨 승인 위조 방어).
"""

from __future__ import annotations

from itertools import pairwise
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.approvals.machine import ApprovalStatus
from app.modules.approvals.models import Approval, ApprovalEvent

DECISION_TARGETS = {ApprovalStatus.APPROVED.value, ApprovalStatus.REJECTED.value}


def check_integrity(
    session: Session, *, limit: int = 1000, after_id: int = 0
) -> list[dict[str, Any]]:
    """`after_id` 뒤 승인 `limit`건을 이력과 대사해 어긋난 행 `[{approval_id, problem}]`을 돌려준다(없으면 빈 목록)."""
    approvals = list(
        session.execute(
            select(Approval).where(Approval.id > after_id).order_by(Approval.id).limit(limit)
        ).scalars()
    )
    if not approvals:
        return []
    events: dict[int, list[ApprovalEvent]] = {a.id: [] for a in approvals}
    for event in session.execute(
        select(ApprovalEvent)
        .where(ApprovalEvent.approval_id.in_(list(events)))
        .order_by(ApprovalEvent.approval_id, ApprovalEvent.id)
    ).scalars():
        events[event.approval_id].append(event)

    problems: list[dict[str, Any]] = []
    for approval in approvals:
        chain = events[approval.id]

        def bad(problem: str, approval_id: int = approval.id) -> None:
            problems.append({"approval_id": approval_id, "problem": problem})

        if not chain:
            bad("NO_EVENTS")
            continue
        if chain[0].from_status is not None or chain[0].to_status != ApprovalStatus.REQUESTED.value:
            bad("BAD_BIRTH")
        for previous, current in pairwise(chain):
            if current.from_status != previous.to_status:
                bad("BROKEN_CHAIN")
                break
        if chain[-1].to_status != approval.status:
            bad("STATUS_MISMATCH")
        decisions = [e for e in chain if e.to_status in DECISION_TARGETS]
        if decisions:
            d = decisions[-1]
            if (d.actor_user_id, d.on_behalf_of_user_id, d.delegation_id) != (
                approval.decided_by_id,
                approval.decided_on_behalf_of_id,
                approval.decided_delegation_id,
            ):
                bad("DECISION_MISMATCH")
        elif approval.decided_by_id is not None or approval.decided_at is not None:
            bad("DECISION_WITHOUT_EVENT")
        consumed = [e for e in chain if e.to_status == ApprovalStatus.CONSUMED.value]
        if consumed:
            if consumed[-1].actor_user_id != approval.consumed_by_id:
                bad("CONSUMED_MISMATCH")
        elif approval.consumed_by_id is not None or approval.consumed_at is not None:
            bad("CONSUMED_WITHOUT_EVENT")
    return problems
