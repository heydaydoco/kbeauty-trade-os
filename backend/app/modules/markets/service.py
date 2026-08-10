"""시장 마스터 서비스 (§5.1 / §17.2 낙관 잠금 / §17.4 멱등 / S2-1 판정 §0-1).

■ 아웃박스 이벤트를 발행하지 않는다 (판정 ⑦ "이벤트 발행 0")

  partners 등 기존 마스터는 생성 시 events에 기록하지만, S2-1은 소비자
  (디스패처 — S2-3)가 없는 상태에서 events 단조 증가(부채 #11)에 가담하지
  않기로 판정됐다. 시장 이벤트가 필요해지는 세션(S2-3 알림)이 발행을 연다.

■ require_active_market_code가 조건 6의 구현이다

  FK(→ markets.code)는 마지막 안전망이고, 사용자에게는 여기서 422와 함께
  "시장을 먼저 등록하라"는 안내가 나간다. 활성 시장만 통과시킨다 — soft
  delete된 시장은 코드는 점유하지만(전역 UNIQUE) 새 등록의 대상은 아니다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, NotFoundError, VersionConflictError
from app.modules.idempotency import service as idempotency
from app.modules.identity.service import AuthenticatedUser
from app.modules.markets.models import Market

MARKET_CREATE_ENDPOINT = "POST /api/v1/markets"

#: CSV 내보내기 상한 — partners·catalog와 같은 값·같은 이유.
EXPORT_MAX_ROWS = 50_000

_CODE_PATTERN = re.compile(r"^[A-Z]{2}$")


# ── 뷰 ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MarketView:
    id: int
    code: str
    name_ko: str
    name_en: str | None
    note: str | None
    #: 정식화 편집(PATCH)의 낙관 잠금 토큰(§17.2) — 화면이 그대로 되돌려 준다.
    version: int


def _view(row: Market) -> MarketView:
    return MarketView(
        id=row.id,
        code=row.code,
        name_ko=row.name_ko,
        name_en=row.name_en,
        note=row.note,
        version=row.version,
    )


def _serialize(view: MarketView) -> dict[str, Any]:
    return {
        "id": view.id,
        "code": view.code,
        "name_ko": view.name_ko,
        "name_en": view.name_en,
        "note": view.note,
        "version": view.version,
    }


# ── 공통 ───────────────────────────────────────────────────────────────────


def normalized_code(raw: Any) -> str:
    """시장 코드 정규화 — 트림·대문자(기존 3테이블의 'us'→'US' 선례와 동일)."""
    code = str(raw).strip().upper()
    if not _CODE_PATTERN.fullmatch(code):
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"code": "시장 코드는 영문 대문자 2자입니다(예: US, EU)."},
            log_context={"code": code},
        )
    return code


def require_market(session: Session, market_id: int) -> Market:
    row = session.execute(
        select(Market).where(Market.id == market_id, Market.deleted_at.is_(None))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(log_context={"market_id": market_id})
    return row


def require_active_market_code(session: Session, code: str, *, field: str = "country_code") -> None:
    """미등록 시장이면 422 + 등록 안내 (S2-1 판정 조건 6).

    소비자: 라벨·성분 규칙·SKU HS 세번 등록 경로. FK는 안전망이고 안내는
    여기서 나간다 — 코드는 호출자가 이미 정규화(대문자)한 값이어야 한다.
    """
    exists = session.execute(
        select(Market.id).where(Market.code == code, Market.deleted_at.is_(None))
    ).scalar_one_or_none()
    if exists is None:
        raise AppError(
            ErrorCode.MARKETS_MARKET_NOT_REGISTERED,
            detail={field: f"등록되지 않은 시장입니다: {code}. 시장 관리에서 먼저 등록해 주세요."},
            log_context={"market_code": code},
        )


def _guard_export_size(total: int) -> None:
    if total > EXPORT_MAX_ROWS:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={
                "size": f"내보낼 자료가 너무 많습니다({total:,}건). "
                f"{EXPORT_MAX_ROWS:,}건 이하가 되도록 조건을 좁혀 주세요."
            },
        )


# ── 시장 마스터 ─────────────────────────────────────────────────────────────


def create_market(
    *, actor: AuthenticatedUser, idempotency_key: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=MARKET_CREATE_ENDPOINT,
            key=idempotency_key,
            request_body=payload,
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        row = Market(
            code=normalized_code(payload["code"]),
            name_ko=str(payload["name_ko"]).strip(),
            name_en=payload.get("name_en"),
            note=payload.get("note"),
            created_by_id=actor.id,
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError as exc:
            # 전역 UNIQUE라 소프트 삭제된 행도 코드를 점유한다(모델 독스트링).
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={"code": "이미 등록된 시장 코드입니다. 목록에서 확인해 주세요."},
                log_context={"code": payload.get("code")},
            ) from exc

        body = _serialize(_view(row))
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def update_market(
    *, actor: AuthenticatedUser, market_id: int, payload: dict[str, Any]
) -> MarketView:
    """정식화 편집 — 이름·메모만. 코드는 불변이다(FK 참조 값 — 계획서 §3-1).

    백필이 만든 MIG 계보 행(name_ko=코드 그대로)의 정식화가 첫 소비자다.
    """
    with unit_of_work() as uow:
        session = uow.session
        row = require_market(session, market_id)
        if int(payload["version"]) != row.version:
            raise VersionConflictError(
                log_context={"market_id": market_id, "expected": payload["version"]}
            )
        row.name_ko = str(payload["name_ko"]).strip()
        row.name_en = payload.get("name_en")
        row.note = payload.get("note")
        row.updated_by_id = actor.id
        session.flush()
        return _view(row)


def list_markets(*, offset: int, limit: int) -> tuple[list[MarketView], int]:
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(Market).where(Market.deleted_at.is_(None))
        ).scalar_one()
        rows = session.execute(
            select(Market)
            .where(Market.deleted_at.is_(None))
            .order_by(Market.code)
            .offset(offset)
            .limit(limit)
        ).scalars()
        return [_view(row) for row in rows], total


def all_markets_for_export() -> list[MarketView]:
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(Market).where(Market.deleted_at.is_(None))
        ).scalar_one()
        _guard_export_size(total)
        rows = session.execute(
            select(Market).where(Market.deleted_at.is_(None)).order_by(Market.code)
        ).scalars()
        return [_view(row) for row in rows]


def get_market(market_id: int) -> MarketView:
    with unit_of_work() as uow:
        return _view(require_market(uow.session, market_id))
