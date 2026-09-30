"""정책 설정 API — 관리자 전용 (S3-1 ADR-0065). 게이트 응답이 실효 값·출처를 전 역할에 읽기로 싣는다."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import AdminUser, IdempotencyKey, require_roles
from app.core.pagination import Page, PageParams
from app.modules.identity.models import RoleCode
from app.modules.policies import service
from app.modules.policies.schemas import PolicySummary, PolicyUpdated, PolicyUpdateRequest

router = APIRouter(prefix="/policies", tags=["policies"])


@router.get(
    "",
    summary="정책 설정 목록 (관리자) — 미설정 항목도 포함",
    dependencies=[require_roles(RoleCode.ADMIN)],
)
def list_policies(params: Annotated[PageParams, Depends()]) -> Page[PolicySummary]:
    views = service.list_policies()  # 레지스트리 전건(고정 소수) — 규약상 페이지 봉투 형태 유지
    window = views[params.offset : params.offset + params.limit]
    return Page.of([PolicySummary.of(v) for v in window], len(views), params)


@router.put("/{policy_key}", summary="정책 값 저장 (관리자 — 사유 필수)")
def update_policy(
    policy_key: str, payload: PolicyUpdateRequest, current: AdminUser, key: IdempotencyKey
) -> PolicyUpdated:
    _, body = service.update_policy(
        actor=current,
        idempotency_key=key,
        policy_key=policy_key,
        payload=payload.model_dump(mode="json"),
    )
    return PolicyUpdated.model_validate(body)
