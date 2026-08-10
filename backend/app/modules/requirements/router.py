"""요건 템플릿 엔드포인트 (§5.1 / §18.4 페이지네이션 / ADR-0033·0034·0035).

권한(S2-1 판정 ⑤·조건 9 — §2 명문화분 그대로): 편집(등록·수정·확정·초안
전환·체크리스트·선행요건·품목군 세트)=**인증**(관리자는 상시 통과 —
require_roles 규약), 무역·물류·조회=열람. markets와 같은 경계다.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import CurrentUser, IdempotencyKey, require_roles
from app.core.pagination import Page, PageParams
from app.modules.identity.models import RoleCode
from app.modules.requirements import service
from app.modules.requirements.schemas import (
    ChecklistItemAddRequest,
    ChecklistItemSummary,
    ChecklistItemUpdateRequest,
    PrerequisiteAddRequest,
    PrerequisiteSummary,
    ProfileRequirementTemplateAddRequest,
    ProfileRequirementTemplateSummary,
    RequirementTemplateCreateRequest,
    RequirementTemplateSummary,
    RequirementTemplateUpdateRequest,
    TemplateActionRequest,
)

#: 요건 템플릿 편집은 인증 역할이 한다(관리자는 항상 통과) — S2-1 판정 ⑤.
CAN_EDIT = (RoleCode.CERT,)

router = APIRouter(prefix="/requirement-templates", tags=["requirements"])
profile_requirement_templates_router = APIRouter(prefix="/item-profiles", tags=["requirements"])


# ── 템플릿 헤더 ─────────────────────────────────────────────────────────────


@router.post(
    "",
    summary="요건 템플릿 등록 (항상 초안으로 시작)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_EDIT)],
)
def create_requirement_template(
    payload: RequirementTemplateCreateRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> RequirementTemplateSummary:
    status_code, body = service.create_template(
        actor=current, idempotency_key=key, payload=payload.model_dump(mode="json")
    )
    response.status_code = status_code
    return RequirementTemplateSummary.model_validate(body)


@router.get("", summary="요건 템플릿 목록")
def list_requirement_templates(
    current: CurrentUser,
    params: Annotated[PageParams, Depends()],
    market_id: Annotated[int | None, Query(ge=1)] = None,
    template_status: Annotated[
        Literal["DRAFT", "CONFIRMED", "RETIRED"] | None, Query(alias="status")
    ] = None,
) -> Page[RequirementTemplateSummary]:
    views, total = service.list_templates(
        market_id=market_id,
        status=template_status,
        offset=params.offset,
        limit=params.limit,
    )
    return Page.of([RequirementTemplateSummary.of(view) for view in views], total, params)


@router.get("/{template_id}", summary="요건 템플릿 상세")
def get_requirement_template(template_id: int, current: CurrentUser) -> RequirementTemplateSummary:
    return RequirementTemplateSummary.of(service.get_template(template_id))


@router.patch(
    "/{template_id}",
    summary="요건 템플릿 편집 (확정 상태는 409 — 초안 전환 후 편집)",
    dependencies=[require_roles(*CAN_EDIT)],
)
def update_requirement_template(
    template_id: int, payload: RequirementTemplateUpdateRequest, current: CurrentUser
) -> RequirementTemplateSummary:
    view = service.update_template(
        actor=current, template_id=template_id, payload=payload.model_dump(mode="json")
    )
    return RequirementTemplateSummary.of(view)


@router.post(
    "/{template_id}/confirm",
    summary="요건 템플릿 확정 (사람 1클릭+멱등 키 — 근거 2필드 없으면 422)",
    dependencies=[require_roles(*CAN_EDIT)],
)
def confirm_requirement_template(
    template_id: int,
    payload: TemplateActionRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> RequirementTemplateSummary:
    status_code, body = service.confirm_template(
        actor=current,
        idempotency_key=key,
        template_id=template_id,
        payload=payload.model_dump(mode="json"),
    )
    response.status_code = status_code
    return RequirementTemplateSummary.model_validate(body)


@router.post(
    "/{template_id}/revert-to-draft",
    summary="확정 템플릿 초안 전환 (편집·재확정 경로의 입구 — 근거는 남는다)",
    dependencies=[require_roles(*CAN_EDIT)],
)
def revert_requirement_template_to_draft(
    template_id: int, payload: TemplateActionRequest, current: CurrentUser
) -> RequirementTemplateSummary:
    view = service.revert_template_to_draft(
        actor=current, template_id=template_id, payload=payload.model_dump(mode="json")
    )
    return RequirementTemplateSummary.of(view)


# ── 체크리스트 (§5.1) ──────────────────────────────────────────────────────


@router.get("/{template_id}/checklist", summary="템플릿 체크리스트 목록")
def list_template_checklist(
    template_id: int, current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[ChecklistItemSummary]:
    views, total = service.list_checklist(
        template_id=template_id, offset=params.offset, limit=params.limit
    )
    return Page.of([ChecklistItemSummary.of(view) for view in views], total, params)


@router.post(
    "/{template_id}/checklist",
    summary="체크리스트 항목 추가",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_EDIT)],
)
def add_template_checklist_item(
    template_id: int,
    payload: ChecklistItemAddRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> ChecklistItemSummary:
    status_code, body = service.add_checklist_item(
        actor=current,
        idempotency_key=key,
        template_id=template_id,
        payload=payload.model_dump(mode="json"),
    )
    response.status_code = status_code
    return ChecklistItemSummary.model_validate(body)


@router.patch(
    "/{template_id}/checklist/{item_id}",
    summary="체크리스트 항목 수정",
    dependencies=[require_roles(*CAN_EDIT)],
)
def update_template_checklist_item(
    template_id: int, item_id: int, payload: ChecklistItemUpdateRequest, current: CurrentUser
) -> ChecklistItemSummary:
    view = service.update_checklist_item(
        actor=current,
        template_id=template_id,
        item_id=item_id,
        payload=payload.model_dump(mode="json"),
    )
    return ChecklistItemSummary.of(view)


@router.delete(
    "/{template_id}/checklist/{item_id}",
    summary="체크리스트 항목 제거",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_roles(*CAN_EDIT)],
)
def remove_template_checklist_item(template_id: int, item_id: int, current: CurrentUser) -> None:
    service.remove_checklist_item(actor=current, template_id=template_id, item_id=item_id)


# ── 선행요건 (조건 1 — ADR-0034) ────────────────────────────────────────────


@router.get("/{template_id}/prerequisites", summary="선행요건 목록")
def list_template_prerequisites(
    template_id: int, current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[PrerequisiteSummary]:
    views, total = service.list_prerequisites(
        template_id=template_id, offset=params.offset, limit=params.limit
    )
    return Page.of([PrerequisiteSummary.of(view) for view in views], total, params)


@router.post(
    "/{template_id}/prerequisites",
    summary="선행요건 추가 (자기참조·순환은 422)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_EDIT)],
)
def add_template_prerequisite(
    template_id: int,
    payload: PrerequisiteAddRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> PrerequisiteSummary:
    status_code, body = service.add_prerequisite(
        actor=current,
        idempotency_key=key,
        template_id=template_id,
        payload=payload.model_dump(mode="json"),
    )
    response.status_code = status_code
    return PrerequisiteSummary.model_validate(body)


@router.delete(
    "/{template_id}/prerequisites/{link_id}",
    summary="선행요건 해제",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_roles(*CAN_EDIT)],
)
def remove_template_prerequisite(template_id: int, link_id: int, current: CurrentUser) -> None:
    service.remove_prerequisite(actor=current, template_id=template_id, link_id=link_id)


# ── 품목군 요건 세트 (§4.8 — ADR-0035) ─────────────────────────────────────


@profile_requirement_templates_router.get(
    "/{profile_id}/requirement-templates", summary="품목군 요건 세트 목록"
)
def list_item_profile_requirement_templates(
    profile_id: int, current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[ProfileRequirementTemplateSummary]:
    views, total = service.list_profile_requirement_templates(
        profile_id=profile_id, offset=params.offset, limit=params.limit
    )
    return Page.of([ProfileRequirementTemplateSummary.of(view) for view in views], total, params)


@profile_requirement_templates_router.post(
    "/{profile_id}/requirement-templates",
    summary="품목군 요건 세트에 템플릿 추가",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_EDIT)],
)
def add_item_profile_requirement_template(
    profile_id: int,
    payload: ProfileRequirementTemplateAddRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> ProfileRequirementTemplateSummary:
    status_code, body = service.add_profile_requirement_template(
        actor=current,
        idempotency_key=key,
        profile_id=profile_id,
        payload=payload.model_dump(mode="json"),
    )
    response.status_code = status_code
    return ProfileRequirementTemplateSummary.model_validate(body)


@profile_requirement_templates_router.delete(
    "/{profile_id}/requirement-templates/{link_id}",
    summary="품목군 요건 세트에서 템플릿 제거",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_roles(*CAN_EDIT)],
)
def remove_item_profile_requirement_template(
    profile_id: int, link_id: int, current: CurrentUser
) -> None:
    service.remove_profile_requirement_template(
        actor=current, profile_id=profile_id, link_id=link_id
    )
