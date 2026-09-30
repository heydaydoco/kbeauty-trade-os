"""은행 계좌 엔드포인트 (S3-1 design-integrated §2.7 / design-A A8).

권한: **쓰기(등록·수정·비활성)는 ADMIN 전용**, 조회는 ADMIN·TRADE(PI 발행 선택용). 물류·인증·조회 역할은 403 —
계좌번호는 거래 서류에만 필요한 값이라 열람 범위를 좁힌다. 목록은 페이지네이션(기본 50)이다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, IdempotencyKey, require_roles
from app.core.csv_export import csv_response
from app.core.pagination import Page, PageParams
from app.modules.bank_accounts import service
from app.modules.bank_accounts.schemas import (
    BankAccountCreateRequest,
    BankAccountOut,
    BankAccountUpdateRequest,
)
from app.modules.identity.models import RoleCode

#: 조회 — 관리자는 상시 통과(require_roles 규약), 무역은 PI 선택용.
CAN_READ = (RoleCode.TRADE,)

router = APIRouter(prefix="/bank-accounts", tags=["bank-accounts"])

#: 쓰기 게이트 — 허용 역할 목록이 비어 있어 **관리자만** 통과한다(_role_guard의 ADMIN 상시 통과).
ADMIN_ONLY = require_roles()


@router.post(
    "",
    summary="은행 계좌 등록 (관리자 전용)",
    status_code=status.HTTP_201_CREATED,
    dependencies=[ADMIN_ONLY],
)
def create_bank_account(
    payload: BankAccountCreateRequest,
    current: CurrentUser,
    key: IdempotencyKey,
    response: Response,
) -> BankAccountOut:
    status_code, body = service.create_bank_account(
        actor=current, idempotency_key=key, payload=payload.model_dump()
    )
    response.status_code = status_code
    return BankAccountOut.model_validate(body)


@router.get(
    "",
    summary="은행 계좌 목록 (활성만 — 페이지·통화 필터)",
    dependencies=[require_roles(*CAN_READ)],
)
def list_bank_accounts(
    current: CurrentUser,
    params: Annotated[PageParams, Depends()],
    currency: Annotated[str | None, Query(pattern=r"^[A-Za-z]{3}$")] = None,
) -> Page[BankAccountOut]:
    items, total = service.list_bank_accounts(
        offset=params.offset, limit=params.limit, currency=currency
    )
    return Page.of([BankAccountOut.model_validate(item) for item in items], total, params)


@router.get(
    "/export.csv",
    summary="은행 계좌 CSV 내보내기 (UTF-8 BOM)",
    dependencies=[require_roles(*CAN_READ)],
)
def export_bank_accounts_csv(
    current: CurrentUser, currency: Annotated[str | None, Query(pattern=r"^[A-Za-z]{3}$")] = None
) -> StreamingResponse:
    return csv_response(
        "은행계좌목록.csv", service.EXPORT_HEADER, service.export_rows(currency=currency)
    )


# ★ `/{account_id}`는 `/export.csv`보다 **뒤에** 선언해야 한다(앞에 두면 422).
@router.get("/{account_id}", summary="은행 계좌 상세", dependencies=[require_roles(*CAN_READ)])
def get_bank_account(account_id: int, current: CurrentUser) -> BankAccountOut:
    return BankAccountOut.model_validate(service.get_bank_account(account_id))


@router.patch(
    "/{account_id}",
    summary="은행 계좌 수정 (관리자 전용 — 통화 불변, 발행된 PI는 스냅샷이라 영향 없음)",
    dependencies=[ADMIN_ONLY],
)
def update_bank_account(
    account_id: int, payload: BankAccountUpdateRequest, current: CurrentUser
) -> BankAccountOut:
    body = service.update_bank_account(
        actor=current, account_id=account_id, payload=payload.model_dump(exclude_unset=True)
    )
    return BankAccountOut.model_validate(body)


@router.delete(
    "/{account_id}",
    summary="은행 계좌 비활성 (관리자 전용 — soft delete, 발행된 PI 불변)",
    dependencies=[ADMIN_ONLY],
)
def deactivate_bank_account(
    account_id: int,
    current: CurrentUser,
    version: Annotated[int, Query(ge=1, description="화면이 본 version(낙관 잠금)")],
) -> BankAccountOut:
    body = service.deactivate_bank_account(actor=current, account_id=account_id, version=version)
    return BankAccountOut.model_validate(body)
