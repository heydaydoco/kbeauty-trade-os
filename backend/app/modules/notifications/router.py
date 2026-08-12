"""알림센터·알림 규칙 엔드포인트 (§18.1 인가·소유권 / §18.4 페이지네이션).

★ 알림함은 **개인 수신함**이다 — 목록·확인 모두 자기 것만이고, 관리자도
  예외가 아니다(§18.1 IDOR). 남의 알림을 확인 처리하면 그 사람 대신 "봤다"고
  기록되는 셈이라, 재발송 금지(ADR-07)의 기준이 남의 손에 넘어간다.

★ 알림 규칙 관리는 **관리자 전용**이다(판정 요청 10) — 수신자·채널 결정은
  조직 관리 계열이고, 기일 계열의 관리자 폴백 게이트가 이 규칙에 걸린다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import AdminUser, CurrentUser, IdempotencyKey, require_roles
from app.core.pagination import Page, PageParams
from app.modules.identity.models import RoleCode
from app.modules.notifications import service
from app.modules.notifications.schemas import (
    AlertRuleCreateRequest,
    AlertRuleSummary,
    AlertRuleUpdateRequest,
    AlertSummary,
    UnreadCount,
)

router = APIRouter(prefix="/alerts", tags=["alerts"])
rules_router = APIRouter(prefix="/alert-rules", tags=["alert-rules"])


@router.get("", summary="내 알림 목록")
def list_alerts(
    current: CurrentUser,
    params: Annotated[PageParams, Depends()],
    unacknowledged_only: Annotated[bool, Query(description="미확인만 보기")] = False,
) -> Page[AlertSummary]:
    views, total = service.list_alerts(
        actor=current,
        offset=params.offset,
        limit=params.limit,
        unacknowledged_only=unacknowledged_only,
    )
    return Page.of([AlertSummary.of(view) for view in views], total, params)


@router.get("/unread-count", summary="내 미확인 알림 수")
def unread_count(current: CurrentUser) -> UnreadCount:
    return UnreadCount(count=service.unread_count(actor=current))


@router.post("/{alert_id}/ack", summary="알림 확인 (재발송 금지의 기준)")
def acknowledge(alert_id: int, current: CurrentUser) -> AlertSummary:
    return AlertSummary.of(service.acknowledge(actor=current, alert_id=alert_id))


@rules_router.post(
    "",
    summary="알림 규칙 등록",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(RoleCode.ADMIN)],
)
def create_rule(
    payload: AlertRuleCreateRequest,
    current: AdminUser,
    key: IdempotencyKey,
    response: Response,
) -> AlertRuleSummary:
    status_code, body = service.create_rule(
        actor=current,
        idempotency_key=key,
        payload=payload.model_dump(mode="json"),
    )
    response.status_code = status_code
    return AlertRuleSummary.model_validate(body)


@rules_router.get("", summary="알림 규칙 목록", dependencies=[require_roles(RoleCode.ADMIN)])
def list_rules(params: Annotated[PageParams, Depends()]) -> Page[AlertRuleSummary]:
    views, total = service.list_rules(offset=params.offset, limit=params.limit)
    return Page.of([AlertRuleSummary.of(view) for view in views], total, params)


@rules_router.patch("/{rule_id}", summary="알림 규칙 편집")
def update_rule(
    rule_id: int, payload: AlertRuleUpdateRequest, current: AdminUser
) -> AlertRuleSummary:
    changes = payload.model_dump(mode="json", exclude_unset=True)
    version = changes.pop("version")
    return AlertRuleSummary.of(
        service.update_rule(actor=current, rule_id=rule_id, version=version, changes=changes)
    )


@rules_router.delete("/{rule_id}", summary="알림 규칙 삭제", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(rule_id: int, version: int, current: AdminUser) -> Response:
    service.delete_rule(actor=current, rule_id=rule_id, version=version)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
