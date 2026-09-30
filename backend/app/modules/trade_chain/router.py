"""견적 전이 엔드포인트 — 발행·취소·개정 (S3-1 design-B B8).

전부 **사람 1클릭 + `Idempotency-Key` 필수**이고 무역(관리자 상시 통과)이 한다. 동결 액션(`issue`)은 범용
`/transitions`로 못 넘는다(스키마 `to` Literal에서 동결 엣지·자동 엣지·RESERVED를 구조적으로 제외 — 우회 표면 제거).
이 라우터가 `issue_quotation`의 **유일한 호출처**다(자동 확정 경로 부재 — test_no_auto_confirm_code_path_exists).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from app.api.deps import CurrentUser, IdempotencyKey, require_roles
from app.modules.identity.models import RoleCode
from app.modules.quotations.schemas import QuotationDetail
from app.modules.trade_chain import lifecycle
from app.modules.trade_docs.constants import DocKind
from app.modules.trade_docs.machine import public_transition_targets

CAN_WRITE = (RoleCode.TRADE,)

#: 범용 전이의 `to` — machine에서 파생한 값이 Literal과 같은지 아키텍처 테스트가 대사한다.
QuotationTarget = Literal["CANCELLED"]
assert set(QuotationTarget.__args__) == public_transition_targets(DocKind.QUOTATION)  # type: ignore[attr-defined]


class IssueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1)


class QuotationTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: QuotationTarget
    version: StrictInt = Field(ge=1)
    #: 취소는 사유 필수(1~500자, 공백 불가).
    reason: StrictStr | None = Field(default=None, max_length=500)


class RevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: StrictInt = Field(ge=1)


router = APIRouter(prefix="/quotations", tags=["trade-chain"])


@router.post(
    "/{qt_id}/issue",
    summary="견적 발행 (동결 액션 — 개정본이면 같은 트랜잭션에서 원본 취소)",
    dependencies=[require_roles(*CAN_WRITE)],
)
def issue_quotation(
    qt_id: int, payload: IssueRequest, current: CurrentUser, key: IdempotencyKey, response: Response
) -> QuotationDetail:
    status_code, body = lifecycle.issue_quotation(
        actor=current, idempotency_key=key, qt_id=qt_id, version=payload.version
    )
    response.status_code = status_code
    return QuotationDetail.model_validate(body)


@router.post(
    "/{qt_id}/transitions",
    summary="견적 취소 (사유 필수 — 후속 생존 시 409, 발행은 /issue 전용)",
    dependencies=[require_roles(*CAN_WRITE)],
)
def transition_quotation(
    qt_id: int,
    payload: QuotationTransitionRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> QuotationDetail:
    status_code, body = lifecycle.transition_quotation(
        actor=current,
        idempotency_key=key,
        qt_id=qt_id,
        to=payload.to,
        version=payload.version,
        reason=payload.reason,
    )
    response.status_code = status_code
    return QuotationDetail.model_validate(body)


@router.post(
    "/{qt_id}/revisions",
    summary="견적 개정 초안 작성 (발행 상태 원본 한정 — 초안 발행 시 원본이 취소된다)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_WRITE)],
)
def create_revision(
    qt_id: int,
    payload: RevisionRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> QuotationDetail:
    status_code, body = lifecycle.create_revision(
        actor=current, idempotency_key=key, qt_id=qt_id, version=payload.version
    )
    response.status_code = status_code
    return QuotationDetail.model_validate(body)
