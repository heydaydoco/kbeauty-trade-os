"""오더 인테이크 엔드포인트 — 등록(수동)·목록·상세·편집·품번 재해석·거부 (S3-1 PR-13a / design-D D2·D7).

권한: 조회는 **전 역할**(원가·마진 필드가 없어 마스킹 비대상), 쓰기는 무역(관리자 상시 통과) — 서비스가 역할을 한 번 더 확인한다. 전 쓰기 POST는 `Idempotency-Key` 필수, PATCH는 `version`만(B8 선례).
**`DELETE /order-intakes/{id}`는 없다** — 폐기의 유일한 방법은 거부(사유 필수)이고 행·스냅샷·사유는 영구 보존된다.
**확정(`POST /order-intakes/{id}/confirm`)과 게이트 조회(`GET …/gates`)는 이 라우터에 없다** — SO 생성 오케스트레이션이라 trade_chain(`intake_flow`·`router.intake_router`)에 있다
(order_intake는 trade_chain을 임포트하지 못한다). `POST /order-intakes/import-csv`·`GET /order-intakes/template.csv`(CSV 입구)는 `csv_import`가 처리한다(PR-14a — **`/{intake_id}`보다 앞에 선언**해야 `template.csv`가 id로 읽히지 않는다).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Path, Query, Response, UploadFile, status

from app.api.deps import CurrentUser, IdempotencyKey, require_roles
from app.core.csv_export import csv_response
from app.core.pagination import Page, PageParams
from app.modules.identity.models import RoleCode
from app.modules.order_intake import csv_import, csv_template, service
from app.modules.order_intake.schemas import (
    CsvImportResult,
    IntakeCreateRequest,
    IntakeDetail,
    IntakeRejectRequest,
    IntakeSummary,
    IntakeUpdateRequest,
    IntakeVersionRequest,
)

CAN_WRITE = (RoleCode.TRADE,)

router = APIRouter(prefix="/order-intakes", tags=["order-intake"])


@router.post(
    "",
    summary="오더 인테이크 수동 등록 (헤더+라인 일괄 — 항상 PENDING으로만 착지, 품번→SKU는 서버가 해석, 같은 바이어 PO가 이미 점유 중이면 409)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_WRITE)],
)
def create_order_intake(
    payload: IntakeCreateRequest, current: CurrentUser, key: IdempotencyKey, response: Response
) -> IntakeDetail:
    status_code, body = service.create_manual_intake(
        actor=current, idempotency_key=key, payload=payload.model_dump()
    )
    response.status_code = status_code
    return IntakeDetail.model_validate(body)


@router.post(
    "/import-csv",
    summary="오더 인테이크 CSV 업로드 (표준 9열 양식 — 같은 바이어 PO번호의 줄은 인테이크 1건으로 묶인다. 파일 전체가 원자: 한 행이라도 오류면 아무것도 등록하지 않고 행별 오류를 모두 돌려준다. 같은 파일 재업로드 409)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_roles(*CAN_WRITE)],
)
def import_order_intakes_csv(
    file: Annotated[
        UploadFile, File(description="표준 양식 CSV(UTF-8 또는 CP949, 엑셀 파일 불가)")
    ],
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> CsvImportResult:
    status_code, body = csv_import.import_csv(
        actor=current, idempotency_key=key, stream=file.file, filename=file.filename or ""
    )
    response.status_code = status_code
    return CsvImportResult.model_validate(body)


@router.get(
    "/template.csv",
    summary="오더 인테이크 CSV 표준 양식 (헤더 9열만 — UTF-8 BOM, 전 역할)",
)
def download_intake_template(current: CurrentUser) -> Any:
    return csv_response(csv_template.DOWNLOAD_FILENAME, csv_import.template_header(), [])


@router.get("", summary="오더 인테이크 목록 (페이지·필터 — 전 역할)")
def list_order_intakes(
    current: CurrentUser,
    params: Annotated[PageParams, Depends()],
    status: Annotated[str | None, Query(pattern=r"^(PENDING|CONFIRMED|REJECTED)$")] = None,
    buyer_partner_id: Annotated[int | None, Query(ge=1)] = None,
    assignee_id: Annotated[int | None, Query(ge=1)] = None,
    q: Annotated[
        str | None, Query(max_length=100, description="바이어 PO번호·바이어명 부분 일치")
    ] = None,
) -> Page[IntakeSummary]:
    items, total = service.list_intakes(
        offset=params.offset,
        limit=params.limit,
        status=status,
        buyer_partner_id=buyer_partner_id,
        assignee_id=assignee_id,
        q=q,
        roles=current.roles,
    )
    return Page.of([IntakeSummary.model_validate(item) for item in items], total, params)


@router.get(
    "/{intake_id}", summary="오더 인테이크 상세 (라인·품번 해석 상태·불변 원본 포함 — 전 역할)"
)
def get_order_intake(intake_id: Annotated[int, Path(ge=1)], current: CurrentUser) -> IntakeDetail:
    return IntakeDetail.model_validate(service.get_intake(intake_id, roles=current.roles))


@router.patch(
    "/{intake_id}",
    summary="오더 인테이크 편집 (대기 상태만 — PO번호·PO일자·시장·담당자·라인 전체. 거래처·통화·상태·원본은 못 바꾼다)",
    dependencies=[require_roles(*CAN_WRITE)],
)
def update_order_intake(
    intake_id: Annotated[int, Path(ge=1)], payload: IntakeUpdateRequest, current: CurrentUser
) -> IntakeDetail:
    body = service.update_intake(
        actor=current,
        intake_id=intake_id,
        payload=payload.model_dump(exclude_unset=True),
    )
    return IntakeDetail.model_validate(body)


@router.post(
    "/{intake_id}/resolve",
    summary="오더 인테이크 품번 재해석 (품번 매핑 등록·수정 뒤 전 라인을 지금 매핑으로 다시 해석해 저장본을 갱신)",
    dependencies=[require_roles(*CAN_WRITE)],
)
def resolve_order_intake(
    intake_id: Annotated[int, Path(ge=1)],
    payload: IntakeVersionRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> IntakeDetail:
    status_code, body = service.resolve_intake(
        actor=current, idempotency_key=key, intake_id=intake_id, version=payload.version
    )
    response.status_code = status_code
    return IntakeDetail.model_validate(body)


@router.post(
    "/{intake_id}/reject",
    summary="오더 인테이크 거부 (사유 필수 5~500자 — 종결, 행·원본·사유는 영구 보존)",
    dependencies=[require_roles(*CAN_WRITE)],
)
def reject_order_intake(
    intake_id: Annotated[int, Path(ge=1)],
    payload: IntakeRejectRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> IntakeDetail:
    status_code, body = service.reject_intake(
        actor=current,
        idempotency_key=key,
        intake_id=intake_id,
        version=payload.version,
        reason=payload.reason,
    )
    response.status_code = status_code
    return IntakeDetail.model_validate(body)
