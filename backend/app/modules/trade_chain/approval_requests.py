"""여신 초과 승인 요청 — `POST /sales-orders/{id}/approval-requests` (S3-1 PR-12a / design-E E5 / design-C X1 / ADR-0060·0070).

**승인 요청은 SO 확정 시도의 부작용이 아니라 사람의 명시 동작이다**(승인 폭주·알림 스팸 방지). 이 파일이 `request_approval`의 **유일한 호출처**고, 호출처는 라우터 1곳뿐이다
(자동 요청 생성 경로 0 — 확정 서비스·잡·이벤트 핸들러에서 부르지 않는다: `test_no_auto_confirm_code_path_exists`).

한 트랜잭션의 순서(전역 `LOCK_ORDER` — 확정과 같다): 역할 사전 검증(잠금 이전) → 멱등 claim → 거래처 잠금(`lock_buyer_for_credit`) → 사슬 잠금(QT→PI→SO, **version 대조 409**)
→ 잠근 뒤 거래처 재확인 → SO가 RECEIVED인지(409 `TRANSITION.NOT_ALLOWED`) → `approvals.request_approval`(호출 트랜잭션에 합류 — `TargetSpec.snapshot(lock=True)`가 같은 순서로 잠금을 **다시** 잡는다[재진입]):
같은 스냅샷의 활성 승인이 있으면 그대로 반환(`created=false`·알림·이벤트 재발생 없음), 입력이 바뀌었으면 기존을 VOID하고 새 요청 · **초과분 ≤ 0이면 422**(불필요한 승인 금지)
· **평가 불능(통화 비교 불가 등)이면 422**(승인으로 우회 불가) · 결재선 미설정이면 422 `APPROVALS.LINE.NOT_CONFIGURED`(fail-closed) · 자격자 공집합이면 422(롤백).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import (
    AppError,
    ForbiddenError,
    NotFoundError,
    VersionConflictError,
)
from app.modules.approvals import service as approvals_service
from app.modules.approvals.machine import ApprovalType
from app.modules.approvals.schemas import ApprovalView
from app.modules.approvals.views import approval_body
from app.modules.credit import (
    spec as _credit_spec,  # noqa: F401 — SO_CREDIT_EXCEEDED TargetSpec 등록
)
from app.modules.credit.locking import lock_buyer_for_credit
from app.modules.idempotency import service as idempotency
from app.modules.identity.models import RoleCode
from app.modules.identity.service import AuthenticatedUser
from app.modules.sales_orders.models import SalesOrder
from app.modules.trade_chain.chain_ops import lock_chain
from app.modules.trade_docs.constants import DocKind

KIND = DocKind.SALES_ORDER
REQUEST_ENDPOINT = "POST /api/v1/sales-orders/{so_id}/approval-requests"

#: 승인을 요청할 수 있는 역할 — 라우트 게이트(무역·관리자)와 같은 집합을 서비스가 한 번 더 확인한다. (승인 코어도 VIEWER만 구성된 행위자를 거부한다.)
REQUEST_ROLES = frozenset({RoleCode.TRADE, RoleCode.ADMIN})


def request_credit_approval(
    *, actor: AuthenticatedUser, idempotency_key: str, so_id: int, version: int
) -> tuple[int, dict[str, Any]]:
    """여신 초과 승인 요청 — 새 요청 201·같은 스냅샷의 기존 활성 승인 200(`created=false`). 본문은 승인 뷰 + `created`."""
    if not (actor.roles & REQUEST_ROLES):
        raise ForbiddenError(log_context={"actor_id": actor.id, "op": "request_credit_approval"})
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=REQUEST_ENDPOINT,
            key=idempotency_key,
            request_body={"so_id": so_id, "version": version},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        buyer_id = session.execute(
            select(SalesOrder.buyer_partner_id).where(
                SalesOrder.id == so_id, SalesOrder.deleted_at.is_(None)
            )
        ).scalar_one_or_none()
        if buyer_id is None:
            raise NotFoundError(log_context={"sales_order_id": so_id})
        locked = lock_buyer_for_credit(session, buyer_id)
        so = lock_chain(session, KIND, so_id, expected_version=version)[KIND]
        if so.buyer_partner_id != locked.partner.id:
            raise VersionConflictError(log_context={"sales_order_id": so_id})
        if so.status != "RECEIVED":
            raise AppError(
                ErrorCode.TRADE_DOCS_TRANSITION_NOT_ALLOWED,
                detail={"from": so.status, "to": "APPROVAL_REQUEST"},
                log_context={"sales_order_id": so_id},
            )
        requested = approvals_service.request_approval(
            session,
            approval_type=ApprovalType.SO_CREDIT_EXCEEDED.value,
            target_id=so_id,
            actor=actor,
        )
        body = {
            **approval_body(session, requested.approval, actor=actor),
            "created": requested.created,
        }
        status_code = 201 if requested.created else 200
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=status_code, body=_json(body))
        return status_code, body


def _json(body: dict[str, Any]) -> dict[str, Any]:
    """멱등 응답 본문 — JSON 직렬화 가능한 값만(승인 뷰는 시각을 이미 ISO 문자열로 준다)."""
    out = ApprovalView.model_validate(body).model_dump(mode="json")
    out["created"] = body["created"]
    return out
