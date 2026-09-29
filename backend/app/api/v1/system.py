"""시스템 엔드포인트 — 헬스체크·통화 자릿수.

인증이 붙는 S0-2에서 헬스 두 경로는 인증 예외 목록(allowlist)에 넣어야 한다.
그러지 않으면 컨테이너 헬스체크가 401을 받고 계속 재시작한다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel

from app.api.deps import AdminUser, CurrentUser
from app.core import health
from app.core.db.uow import unit_of_work
from app.core.money import CURRENCY_MINOR_UNITS
from app.core.pagination import Page, PageParams
from app.modules.audit import service as audit
from app.modules.audit.models import AuditAction
from app.modules.platform import backups

router = APIRouter(prefix="/system", tags=["system"])


class Currency(BaseModel):
    code: str
    #: 소수 자릿수. KRW=0, USD=2. 화면은 이 값으로만 최소단위↔표시값을 바꾼다.
    minor_units: int


@router.get("/healthz", summary="살아 있는지 (DB 비의존)")
def healthz() -> dict[str, Any]:
    return health.liveness()


@router.get("/readyz", summary="요청을 받아도 되는지 (DB·마이그레이션 확인)")
def readyz(response: Response) -> dict[str, Any]:
    ready, payload = health.readiness()
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return payload


@router.get("/currencies", summary="통화별 소수 자릿수")
def list_currencies(
    current: CurrentUser, params: Annotated[PageParams, Depends()]
) -> Page[Currency]:
    """금액 표시의 유일한 출처 (§2 ADR-02 / 부채 #9).

    ★ 화면이 자릿수 표를 따로 갖지 않게 하려고 만든 경로다. 프런트에 같은 표를
      복사해 두면 통화가 추가될 때 한쪽만 갱신되고, 그러면 같은 금액이 서버와
      화면에서 다르게 보인다 — 그 차이는 정산 대사에서야 드러난다.
    """
    items = [
        Currency(code=code, minor_units=units)
        for code, units in sorted(CURRENCY_MINOR_UNITS.items())
    ]
    window = items[params.offset : params.offset + params.limit]
    return Page.of(window, len(items), params)


class BackupSummary(BaseModel):
    name: str
    created_at: datetime
    database: str | None
    migration_head: str | None
    file_documents: int | None
    table_count: int | None
    total_rows: int | None
    total_bytes: int | None
    #: 매니페스트를 읽지 못했거나 필수 값이 없으면 False — 깨진 세트도 목록에 남겨 보인다.
    manifest_ok: bool


class RehearsalSummary(BaseModel):
    finished_at: datetime
    ok: bool
    set_name: str | None
    failures: list[str]


class FreshnessProblemSummary(BaseModel):
    code: str
    message: str


class BackupsPage(Page[BackupSummary]):
    """백업 세트 목록(최신순) + 최근 복원 리허설 + 신선도 문제 — 관리자 전용·매 호출 audit 기록."""

    #: KBOS_BACKUP_DIR가 없으면 False(백업 볼륨 미구성 — dev 기본). 이때 목록은 비어 있다.
    configured: bool
    latest_rehearsal: RehearsalSummary | None
    problems: list[FreshnessProblemSummary]


@router.get(
    "/backups",
    summary="백업 세트 목록·최근 복원 리허설·신선도 (관리자 전용 — 매니페스트만, 산출물 본체 다운로드 없음)",
)
def list_backups(current: AdminUser, params: Annotated[PageParams, Depends()]) -> BackupsPage:
    """★ 산출물(암호화 덤프·파일 묶음)을 내려주는 API는 없다 — 전 데이터 유출면이다(ADR-0050).
    ★ 조회 자체도 audit에 남는다(§2 접근 통제) — 누가 언제 백업 현황을 봤는가."""
    directory = backups.backup_dir()
    with unit_of_work() as uow:
        audit.record(
            uow.session,
            action=AuditAction.BACKUPS_VIEWED,
            actor_user_id=current.id,
            entity_type="backups",
            detail={"configured": directory is not None, "page": params.page, "size": params.size},
        )
    if directory is None:
        return BackupsPage(
            items=[],
            total=0,
            page=params.page,
            size=params.size,
            configured=False,
            latest_rehearsal=None,
            problems=[],
        )
    views = backups.list_backups(directory)
    window = views[params.offset : params.offset + params.limit]
    rehearsals = backups.list_rehearsals(directory)
    latest = rehearsals[0] if rehearsals else None
    return BackupsPage(
        items=[BackupSummary.model_validate(view, from_attributes=True) for view in window],
        total=len(views),
        page=params.page,
        size=params.size,
        configured=True,
        latest_rehearsal=(
            RehearsalSummary(
                finished_at=latest.finished_at,
                ok=latest.ok,
                set_name=latest.set_name,
                failures=latest.failures,
            )
            if latest is not None
            else None
        ),
        problems=[
            FreshnessProblemSummary(code=item.code, message=item.message)
            for item in backups.freshness_problems(directory)
        ],
    )
