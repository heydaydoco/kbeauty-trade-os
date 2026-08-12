"""인증 인스턴스 엔드포인트 (§5.1·§5.2 / §18.4 페이지네이션 / 판정 안건 ⑫).

권한(판정 ⑫ — §2 계보): 편집 계열(등록·전이·편집·태스크)=**인증**(관리자는
상시 통과 — require_roles 규약), 무역·물류·조회=열람. 원가·마진 필드가 없어
마스킹 비대상(신규 마스킹 컬럼 0 — 판정 ⑫ 확인분). 날짜 스윕은 API가 없다
(CLI 전용 — 수동 실행 버튼은 S2-3 레지스트리 화면 몫).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import CurrentUser, IdempotencyKey, require_roles
from app.core.pagination import Page, PageParams
from app.modules.certifications import service
from app.modules.certifications.schemas import (
    CertificationCreateRequest,
    CertificationSummary,
    CertificationTransitionRequest,
    CertificationUpdateRequest,
    StatusLogSummary,
    TargetType,
    TaskAddRequest,
    TaskSummary,
    TaskUpdateRequest,
)
from app.modules.identity.models import RoleCode

#: 인증 인스턴스 편집은 인증 역할이 한다(관리자는 항상 통과) — 판정 ⑫.
CAN_EDIT = (RoleCode.CERT,)

router = APIRouter(prefix="/certifications", tags=["certifications"])


@router.post(
    "",
    summary="인증 인스턴스 등록 (확정 템플릿에서만 — 항상 미착수로 시작)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_EDIT)],
)
def create_certification(
    payload: CertificationCreateRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> CertificationSummary:
    status_code, body = service.create_certification(
        actor=current, idempotency_key=key, payload=payload.model_dump(mode="json")
    )
    response.status_code = status_code
    return CertificationSummary.model_validate(body)


@router.get("", summary="인증 인스턴스 목록")
def list_certifications(
    current: CurrentUser,
    params: Annotated[PageParams, Depends()],
    template_id: Annotated[int | None, Query(ge=1)] = None,
    target_type: Annotated[TargetType | None, Query()] = None,
    certification_status: Annotated[str | None, Query(alias="status")] = None,
) -> Page[CertificationSummary]:
    views, total = service.list_certifications(
        template_id=template_id,
        target_type=target_type,
        status=certification_status,
        offset=params.offset,
        limit=params.limit,
    )
    return Page.of([CertificationSummary.of(view) for view in views], total, params)


@router.get("/{certification_id}", summary="인증 인스턴스 상세")
def get_certification(certification_id: int, current: CurrentUser) -> CertificationSummary:
    return CertificationSummary.of(service.get_certification(certification_id))


@router.post(
    "/{certification_id}/transitions",
    summary="상태 전이 (허용 전이 밖은 409 — §5.2 전이 외 변경 거부)",
    dependencies=[require_roles(*CAN_EDIT)],
)
def transition_certification(
    certification_id: int,
    payload: CertificationTransitionRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> CertificationSummary:
    status_code, body = service.transition_certification(
        actor=current,
        idempotency_key=key,
        certification_id=certification_id,
        payload=payload.model_dump(mode="json", exclude_unset=True),
    )
    response.status_code = status_code
    return CertificationSummary.model_validate(body)


@router.patch(
    "/{certification_id}",
    summary="비상태 필드 편집 (상태는 전이 액션으로만 — 만료일 정정은 3태 한정)",
    dependencies=[require_roles(*CAN_EDIT)],
)
def update_certification(
    certification_id: int, payload: CertificationUpdateRequest, current: CurrentUser
) -> CertificationSummary:
    view = service.update_certification(
        actor=current,
        certification_id=certification_id,
        payload=payload.model_dump(mode="json", exclude_unset=True),
    )
    return CertificationSummary.of(view)


@router.get("/{certification_id}/status-log", summary="상태 변경 이력 (불변 — §17.5 확장)")
def list_certification_status_log(
    certification_id: int, current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[StatusLogSummary]:
    views, total = service.list_status_log(
        certification_id=certification_id, offset=params.offset, limit=params.limit
    )
    return Page.of([StatusLogSummary.of(view) for view in views], total, params)


# ── 태스크 (ADR-11 "태스크는 자유") ─────────────────────────────────────────


@router.get("/{certification_id}/tasks", summary="인증 태스크 목록")
def list_certification_tasks(
    certification_id: int, current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[TaskSummary]:
    views, total = service.list_tasks(
        certification_id=certification_id, offset=params.offset, limit=params.limit
    )
    return Page.of([TaskSummary.of(view) for view in views], total, params)


@router.post(
    "/{certification_id}/tasks",
    summary="태스크 추가",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_EDIT)],
)
def add_certification_task(
    certification_id: int,
    payload: TaskAddRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> TaskSummary:
    status_code, body = service.add_task(
        actor=current,
        idempotency_key=key,
        certification_id=certification_id,
        payload=payload.model_dump(mode="json"),
    )
    response.status_code = status_code
    return TaskSummary.model_validate(body)


@router.patch(
    "/{certification_id}/tasks/{task_id}",
    summary="태스크 편집 (체크 토글 — done↔done_at 쌍은 서버가 움직인다)",
    dependencies=[require_roles(*CAN_EDIT)],
)
def update_certification_task(
    certification_id: int, task_id: int, payload: TaskUpdateRequest, current: CurrentUser
) -> TaskSummary:
    view = service.update_task(
        actor=current,
        certification_id=certification_id,
        task_id=task_id,
        payload=payload.model_dump(mode="json", exclude_unset=True),
    )
    return TaskSummary.of(view)


@router.delete(
    "/{certification_id}/tasks/{task_id}",
    summary="태스크 제거 (soft delete — 재추가는 신규)",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_roles(*CAN_EDIT)],
)
def remove_certification_task(certification_id: int, task_id: int, current: CurrentUser) -> None:
    service.remove_task(actor=current, certification_id=certification_id, task_id=task_id)
