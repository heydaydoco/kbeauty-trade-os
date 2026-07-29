"""품목군 프로파일 (DESIGN.md §4.8 / ADR-0021).

■ 지금은 헤더뿐이다

  §4.8의 "기본 요건 세트·서류 세트·마일스톤 세트"는 대상 테이블이 전부 후속
  세션이다 — 요건은 S2-1(§5.1), 서류는 S1-3(§4.7), 마일스톤은 S3-2(§7.5).
  연결 테이블은 각 대상이 생기는 세션이 붙이고, "신규 등록 시 자동 적용"도
  적용할 내용이 생긴 뒤다.

  그래서 이 모듈에는 **분류를 만들고 고르는 것**밖에 없다. 그게 전부라는 사실이
  ADR-0021에 적혀 있고, PROGRESS 부채 #15가 연결 시점을 추적한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, NotFoundError
from app.modules.catalog.models import ItemProfile
from app.modules.idempotency import service as idempotency
from app.modules.identity.service import AuthenticatedUser

PROFILE_CREATE_ENDPOINT = "POST /api/v1/item-profiles"


@dataclass(frozen=True, slots=True)
class ItemProfileView:
    id: int
    code: str
    name_ko: str
    description: str | None


def _view(row: ItemProfile) -> ItemProfileView:
    return ItemProfileView(
        id=row.id,
        code=row.code,
        name_ko=row.name_ko,
        description=row.description,
    )


def _serialize(view: ItemProfileView) -> dict[str, Any]:
    return {
        "id": view.id,
        "code": view.code,
        "name_ko": view.name_ko,
        "description": view.description,
    }


def require_profile(session: Session, profile_id: int) -> ItemProfile:
    """존재하는 프로파일인지 확인한다. 제품·SKU 등록이 이 검사를 쓴다."""
    profile = session.execute(
        select(ItemProfile).where(ItemProfile.id == profile_id, ItemProfile.deleted_at.is_(None))
    ).scalar_one_or_none()
    if profile is None:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"item_profile_id": "존재하지 않는 품목군입니다. 품목군을 먼저 등록해 주세요."},
            log_context={"item_profile_id": profile_id},
        )
    return profile


def create_profile(
    *, actor: AuthenticatedUser, idempotency_key: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=PROFILE_CREATE_ENDPOINT,
            key=idempotency_key,
            request_body=payload,
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        row = ItemProfile(
            code=str(payload["code"]).strip(),
            name_ko=str(payload["name_ko"]).strip(),
            description=payload.get("description"),
            created_by_id=actor.id,
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError as exc:
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={"code": "이미 등록된 품목군 코드입니다. 다른 코드를 입력해 주세요."},
                log_context={"code": payload.get("code")},
            ) from exc

        body = _serialize(_view(row))
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def list_profiles(*, offset: int, limit: int) -> tuple[list[ItemProfileView], int]:
    with unit_of_work() as uow:
        session = uow.session
        condition = (ItemProfile.deleted_at.is_(None),)
        total = session.execute(
            select(func.count()).select_from(ItemProfile).where(*condition)
        ).scalar_one()
        rows = session.execute(
            select(ItemProfile)
            .where(*condition)
            .order_by(ItemProfile.code)
            .offset(offset)
            .limit(limit)
        ).scalars()
        return [_view(row) for row in rows], total


def get_profile(profile_id: int) -> ItemProfileView:
    with unit_of_work() as uow:
        row = uow.session.execute(
            select(ItemProfile).where(
                ItemProfile.id == profile_id, ItemProfile.deleted_at.is_(None)
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError(log_context={"item_profile_id": profile_id})
        return _view(row)
