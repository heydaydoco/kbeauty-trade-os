"""SKU 서비스 (DESIGN.md §4.1 / §17.4 멱등 / §18.4 페이지네이션)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, NotFoundError
from app.modules.catalog.models import Sku
from app.modules.idempotency import service as idempotency
from app.modules.identity.service import AuthenticatedUser
from app.modules.outbox import service as outbox

CREATE_ENDPOINT = "POST /api/v1/skus"

#: CSV 내보내기 상한. 무제한으로 두면 한 번의 클릭이 DB와 메모리를 통째로 쓴다.
#: 넘으면 잘라 내지 않고 거절한다 — 조용히 잘린 파일은 "전부 받았다"고 믿게 만든다.
EXPORT_MAX_ROWS = 50_000


@dataclass(frozen=True, slots=True)
class SkuView:
    id: int
    sku_code: str
    name_ko: str
    name_en: str | None
    status: str


def _view(sku: Sku) -> SkuView:
    return SkuView(
        id=sku.id,
        sku_code=sku.sku_code,
        name_ko=sku.name_ko,
        name_en=sku.name_en,
        status=sku.status,
    )


def _serialize(view: SkuView) -> dict[str, Any]:
    return {
        "id": view.id,
        "sku_code": view.sku_code,
        "name_ko": view.name_ko,
        "name_en": view.name_en,
        "status": view.status,
    }


def create_sku(
    *, actor: AuthenticatedUser, idempotency_key: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """SKU를 등록한다. 같은 키의 재요청은 최초 결과를 그대로 돌려준다."""
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=CREATE_ENDPOINT,
            key=idempotency_key,
            request_body=payload,
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        sku = Sku(
            sku_code=str(payload["sku_code"]).strip(),
            name_ko=str(payload["name_ko"]).strip(),
            name_en=payload.get("name_en"),
            status=payload.get("status") or "ACTIVE",
            created_by_id=actor.id,
        )
        session.add(sku)
        try:
            session.flush()
        except IntegrityError as exc:
            # 품번 중복. DB의 부분 유니크가 최종 판정자다 — 코드에서 미리
            # 조회해 확인하는 방식은 동시 등록에 뚫린다(§17.4).
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={"sku_code": "이미 등록된 품번입니다. 다른 품번을 입력해 주세요."},
                log_context={"sku_code": payload.get("sku_code")},
            ) from exc

        outbox.publish(
            session,
            event_type="catalog.sku.created",
            aggregate_type="skus",
            aggregate_id=sku.id,
            payload={"sku_id": sku.id, "sku_code": sku.sku_code},
        )

        body = _serialize(_view(sku))
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def list_skus(*, offset: int, limit: int) -> tuple[list[SkuView], int]:
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(Sku).where(Sku.deleted_at.is_(None))
        ).scalar_one()
        rows = session.execute(
            select(Sku)
            .where(Sku.deleted_at.is_(None))
            .order_by(Sku.sku_code)
            .offset(offset)
            .limit(limit)
        ).scalars()
        return [_view(sku) for sku in rows], total


def get_sku(sku_id: int) -> SkuView:
    with unit_of_work() as uow:
        sku = uow.session.execute(
            select(Sku).where(Sku.id == sku_id, Sku.deleted_at.is_(None))
        ).scalar_one_or_none()
        if sku is None:
            raise NotFoundError(log_context={"sku_id": sku_id})
        return _view(sku)


def all_skus_for_export() -> list[SkuView]:
    """CSV 내보내기용 전건. 상한을 넘으면 거절한다(§18.4 무제한 조회 금지 정신)."""
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(Sku).where(Sku.deleted_at.is_(None))
        ).scalar_one()
        if total > EXPORT_MAX_ROWS:
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={
                    "size": f"내보낼 자료가 너무 많습니다({total:,}건). "
                    f"{EXPORT_MAX_ROWS:,}건 이하가 되도록 조건을 좁혀 주세요."
                },
            )
        rows = session.execute(
            select(Sku).where(Sku.deleted_at.is_(None)).order_by(Sku.sku_code)
        ).scalars()
        return [_view(sku) for sku in rows]
