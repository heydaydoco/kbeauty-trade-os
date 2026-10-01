"""승인 코어 테스트 팩토리 — 사용자·행위자·결재선·여신 거래처·SO·승인 상태 만들기 (S3-1 PR-9a / design-integrated G-06).

**테스트 데이터는 전부 익명 합성**이다. 승인 요청 생성은 HTTP에 없으므로(PR-12 노출) 서비스(`request_approval`)를 UoW 안에서 직접 부른다 —
요청·결정 통로의 동작을 실제 DB·실제 잠금으로 시험한다. 결정은 `decide_approval`(자기 UoW)을 그대로 부른다.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any

from sqlalchemy import text

import app.main  # noqa: F401 — api_router 조립이 credit TargetSpec을 등록한다
from app.core.db.session import owner_engine
from app.core.db.uow import unit_of_work
from app.modules.approvals import service
from app.modules.approvals.machine import DecisionVerb
from app.modules.approvals.models import Approval, ApprovalLine
from app.modules.identity.models import RoleCode
from app.modules.identity.service import AuthenticatedUser
from app.modules.partners.models import Partner
from tests.factories.trade import create_buyer, create_direct_so, create_priced_sku, unique
from tests.support.factories import create_user

TYPE = "SO_CREDIT_EXCEEDED"


def make_user(*roles: RoleCode, active: bool = True, name: str | None = None) -> AuthenticatedUser:
    """사용자를 만들고 그 사람의 AuthenticatedUser(서비스에 넘기는 행위자)를 돌려준다."""
    email = f"{unique('apv')}@example.com"
    user_id = create_user(
        email, roles=roles, is_active=active, display_name=name or f"사용자-{unique('n')}"
    )
    return AuthenticatedUser(
        id=user_id,
        email=email,
        display_name="합성 사용자",
        roles=frozenset(roles),
        session_id=0,
    )


def actor_of(user_id: int, *roles: RoleCode) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=user_id, email="x@example.com", display_name="합성", roles=frozenset(roles), session_id=0
    )


def add_line(
    threshold: int = 0,
    *,
    currency: str = "USD",
    role: str = "TRADE",
    approval_type: str = TYPE,
) -> int:
    """결재선 1행(정수 최소단위 임계) — 서비스를 거치지 않고 ORM으로 만든다."""
    with unit_of_work() as uow:
        row = ApprovalLine(
            approval_type=approval_type,
            threshold_amount=threshold,
            threshold_currency=currency,
            approver_role=role,
        )
        uow.session.add(row)
        uow.session.flush()
        return int(row.id)


def set_credit_limit(partner_id: int, amount: int | None, currency: str | None = "USD") -> None:
    with unit_of_work() as uow:
        partner = uow.session.get(Partner, partner_id)
        assert partner is not None
        partner.credit_limit_amount = amount
        partner.credit_limit_currency = currency if amount is not None else None


def credit_so(
    *,
    limit: int = 100_000,
    unit_price: int = 40_000,
    quantity: int = 5,
    buyer: int | None = None,
    currency: str = "USD",
) -> dict[str, Any]:
    """한도 `limit`인 바이어의 RECEIVED SO(총액 = 단가×수량, 기본 200,000 → 초과분 100,000). SO 상세 본문을 돌려준다."""
    buyer_id = buyer or create_buyer()
    set_credit_limit(buyer_id, limit, currency)
    sku = create_priced_sku(amount=unit_price, currency=currency)
    return create_direct_so(
        buyer_id, [sku], quantity=quantity, currency=currency, price_amount=unit_price
    )


def request(
    so_id: int, actor: AuthenticatedUser, *, approval_type: str = TYPE, today: date | None = None
) -> service.RequestResult:
    """승인 요청 — 호출 UoW 안에서(소비 전표 엔드포인트가 하는 방식 그대로)."""
    with unit_of_work() as uow:
        result = service.request_approval(
            uow.session, approval_type=approval_type, target_id=so_id, actor=actor, today=today
        )
        uow.session.expunge(result.approval)
        return result


def decide(
    approval_id: int,
    actor: AuthenticatedUser,
    verb: DecisionVerb | str,
    *,
    reason: str | None = None,
    version: int | None = None,
    key: str | None = None,
) -> dict[str, Any]:
    """결정 — `decide_approval`을 그대로 부른다(version을 안 주면 DB의 현재 값)."""
    if version is None:
        version = approval_row(approval_id)["version"]
    _, body = service.decide_approval(
        approval_id=approval_id,
        actor=actor,
        verb=DecisionVerb(verb),
        reason=reason,
        version=version,
        idempotency_key=key or unique("dec"),
    )
    return body


def approval_row(approval_id: int) -> dict[str, Any]:
    with owner_engine.connect() as connection:
        row = (
            connection.execute(text("SELECT * FROM approvals WHERE id = :i"), {"i": approval_id})
            .mappings()
            .one()
        )
        return dict(row)


def events_of(approval_id: int) -> list[dict[str, Any]]:
    with owner_engine.connect() as connection:
        return [
            dict(r)
            for r in connection.execute(
                text("SELECT * FROM approval_events WHERE approval_id = :i ORDER BY id"),
                {"i": approval_id},
            ).mappings()
        ]


def count(table: str, where: str = "true", **params: Any) -> int:
    with owner_engine.connect() as connection:
        return int(
            connection.execute(
                text(f"SELECT count(*) FROM {table} WHERE {where}"), params
            ).scalar_one()
        )


def audit_actions(action: str) -> list[dict[str, Any]]:
    with owner_engine.connect() as connection:
        return [
            dict(r)
            for r in connection.execute(
                text("SELECT * FROM audit_log WHERE action = :a ORDER BY id"), {"a": action}
            ).mappings()
        ]


def approved_for(
    so: dict[str, Any],
    *,
    requester: AuthenticatedUser | None = None,
    approver: AuthenticatedUser | None = None,
    line_role: str = "TRADE",
) -> tuple[int, AuthenticatedUser, AuthenticatedUser]:
    """결재선 임계 0 + 요청 + 다른 사람의 승인까지 마친 상태를 만든다 → (승인 id, 요청자, 승인자)."""
    if count("approval_lines") == 0:
        add_line(0, role=line_role)
    requester = requester or make_user(RoleCode.TRADE)
    approver = approver or make_user(RoleCode.TRADE)
    approval = request(so["id"], requester).approval
    decide(approval.id, approver, "APPROVE")
    return int(approval.id), requester, approver


def so_set(so_id: int, **columns: Any) -> None:
    """SO 행을 소유자 권한으로 직접 바꾼다(서비스 훅·version을 우회한 변조 — 지연 검증 백스톱 시험용)."""
    sets = ", ".join(f"{k} = :{k}" for k in columns)
    with owner_engine.begin() as connection:
        connection.execute(
            text(f"UPDATE sales_orders SET {sets} WHERE id = :i"), {"i": so_id, **columns}
        )


def line_set(so_id: int, **columns: Any) -> None:
    """SO 라인을 소유자 권한으로 직접 바꾼다(헤더 version이 오르지 않는 변조)."""
    sets = ", ".join(f"{k} = :{k}" for k in columns)
    with owner_engine.begin() as connection:
        connection.execute(
            text(f"UPDATE sales_order_lines SET {sets} WHERE so_id = :i"), {"i": so_id, **columns}
        )


@contextmanager
def fixed_today(monkeypatch: Any, today: date) -> Iterator[None]:
    """결재·대결 판정의 "오늘(KST)"을 고정한다 — 단일 진입점 `authority.kst_today`를 패치."""
    from app.modules.approvals import authority, delegations

    monkeypatch.setattr(authority, "kst_today", lambda: today)
    monkeypatch.setattr(delegations, "kst_today", lambda: today)
    from app.modules.approvals import alerts, stagnation, views

    for module in (alerts, stagnation, views):
        monkeypatch.setattr(module, "kst_today", lambda: today)
    from app.modules.approvals import service as svc

    monkeypatch.setattr(svc, "kst_today", lambda: today)
    yield


__all__ = ["Approval"]
