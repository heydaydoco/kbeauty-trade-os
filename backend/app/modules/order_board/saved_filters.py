"""오더 보드 저장 필터 — 본인 것만·사용자당 20개·이름 유일·스키마 재검증 (S3-1 PR-15a / design-D D6 / ADR-0066·0067).

■ **소유권 = 당사자성**(§18.1 부기 ③): 모든 조회·수정·삭제는 `user_id = 행위자`로 거른다 — 타인 id는 **404**(존재 오라클 방지, 403 아님). 전 역할이 쓸 수 있다
  (원가가 없는 개인 설정). 다른 사람의 필터를 보거나 공유하는 경로는 없다(재판정 트리거: 공유 요구 — D-D10).
■ **활성 20개 상한**: 등록은 **사용자별 트랜잭션 advisory lock**(`pg_advisory_xact_lock(SAVED_FILTER_LOCK_NS, user_id)` — 2인자 키 공간, 네임스페이스는
  스케줄러의 잡 잠금 키와 다르다)을 잡은 **뒤 새 문장으로** 센다. 행 잠금(`FOR UPDATE`)은 행이 0개일 때 잠글 것이 없어 직렬화가 안 되고, 잠금 문의 결과 행 수로
  세면 READ COMMITTED에서 대기 중 커밋된 행이 빠져 뚫린다(실측) — 그래서 행과 무관한 사용자 단위 잠금 + 잠근 뒤 재계수다. 이름 유일은 DB 부분 유니크가 최종
  보증이고 위반은 제약명으로 409로 번역한다(500 금지).
■ **이름 위생**: strip 전에 원문 전체를 `app.core.text.invisible_char_problem`(Cc·Cf·Zl·Zp·한글 채움)으로 검사해 422다. DB CHECK(`name_clean` — PG16 `[[:cntrl:]]`는
  C0·C1 제어 0x00-0x1F·0x7F-0x9F만 잡는다, Zl·Zp·Cf는 못 잡는다)·`name_nonblank` 위반도 두 번째 방어선으로 422 `INVALID_FIELD`로 번역한다(500 금지).
■ `filter_config`는 `BoardFilter`(extra=forbid)로 저장 시 검증하고 **읽을 때 다시 검증**한다 — 스키마가 바뀌어 맞지 않는 옛 값은 조용히 무시·변환하지 않고
  `filter_config=null`·`needs_resave=true`로 드러낸다.
■ 등록 POST는 `Idempotency-Key`(같은 트랜잭션의 claim/complete), 수정·삭제는 `version` 낙관 잠금.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, NotFoundError, VersionConflictError
from app.core.logging import get_logger
from app.core.text import invisible_char_problem
from app.core.time import utcnow
from app.modules.idempotency import service as idempotency
from app.modules.identity.service import AuthenticatedUser
from app.modules.order_board.constants import SAVED_FILTER_LIMIT
from app.modules.order_board.models import SAVED_FILTER_NAME_UNIQUE, BoardSavedFilter
from app.modules.order_board.schemas import BoardFilter
from app.modules.trade_docs.validation import invalid

logger = get_logger(__name__)

CREATE_ENDPOINT = "POST /api/v1/order-board/saved-filters"


#: 저장 필터 등록 직렬화용 advisory lock 네임스페이스(2인자 키의 첫 인자) — 스케줄러 잡 잠금(`SCHEDULER_LOCK_KEY`, 2인자)·시드 투입(1인자 bigint 키 공간)과
#: 겹치지 않는 임의 상수다(아키텍처 시험이 다름을 고정한다).
SAVED_FILTER_LOCK_NS = 7_340_215

#: DB CHECK 위반 → 422 번역(두 번째 방어선 — 1차는 `_clean_name`).
_NAME_CHECKS = frozenset(
    {"ck_board_saved_filters_name_clean", "ck_board_saved_filters_name_nonblank"}
)


def _clean_name(raw: str) -> str:
    """이름 — **strip 전에** 원문 전체에 보이지 않는 글자가 있으면 422, 앞뒤 공백 제거 뒤 비면 422."""
    problem = invisible_char_problem(raw, label="필터 이름")
    if problem is not None:
        raise invalid("name", problem)
    name = raw.strip()
    if not name:
        raise invalid("name", "필터 이름을 입력해 주세요.")
    return name


def _config_of(f: BoardFilter) -> dict[str, Any]:
    """저장 형태 — 설정된 키만(JSON 직렬화: 날짜는 ISO 문자열)."""
    return f.model_dump(mode="json", exclude_none=True)


def body_of(row: BoardSavedFilter) -> dict[str, Any]:
    """응답 본문 — 읽을 때 지금의 `BoardFilter`로 다시 검증한다(낡은 값은 null+`needs_resave`)."""
    try:
        config: dict[str, Any] | None = _config_of(BoardFilter.model_validate(row.filter_config))
        needs_resave = False
    except ValidationError:
        config = None
        needs_resave = True
    return {
        "id": row.id,
        "name": row.name,
        "filter_config": config,
        "needs_resave": needs_resave,
        "version": row.version,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _duplicate_name() -> AppError:
    return AppError(ErrorCode.ORDER_BOARD_FILTER_DUPLICATE_NAME)


def _name_taken(session: Session, user_id: int, name: str, *, exclude_id: int | None) -> bool:
    query = select(BoardSavedFilter.id).where(
        BoardSavedFilter.user_id == user_id,
        BoardSavedFilter.name == name,
        BoardSavedFilter.deleted_at.is_(None),
    )
    if exclude_id is not None:
        query = query.where(BoardSavedFilter.id != exclude_id)
    return session.execute(query.limit(1)).first() is not None


@contextmanager
def _guarded(session: Session) -> Iterator[None]:
    """SAVEPOINT 안에서 flush — 이름 유니크 경합(선조회를 빠져나간 동시 요청)을 같은 409로 번역한다. 모르는 제약은 500 유지(제약명만 로그)."""
    try:
        with session.begin_nested():
            yield
    except IntegrityError as exc:
        name = str(getattr(getattr(exc.orig, "diag", None), "constraint_name", "") or "")
        if name == SAVED_FILTER_NAME_UNIQUE:
            raise _duplicate_name() from None
        if name in _NAME_CHECKS:
            raise invalid(
                "name", "필터 이름에 쓸 수 없는 글자가 있습니다. 이름을 다시 입력해 주세요."
            ) from None
        logger.error("order_board_unmapped_constraint", constraint=name)
        raise


def _require_own(
    session: Session, actor_id: int, filter_id: int, *, version: int
) -> BoardSavedFilter:
    """본인의 활성 필터를 잠가 돌려준다 — 없거나 타인 것이면 404(존재 여부를 알리지 않는다), version이 다르면 409."""
    row = session.execute(
        select(BoardSavedFilter)
        .where(
            BoardSavedFilter.id == filter_id,
            BoardSavedFilter.user_id == actor_id,
            BoardSavedFilter.deleted_at.is_(None),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(log_context={"saved_filter_id": filter_id})
    if row.version != version:
        raise VersionConflictError(log_context={"saved_filter_id": filter_id})
    return row


def list_saved_filters(
    *, actor: AuthenticatedUser, offset: int, limit: int
) -> tuple[list[dict[str, Any]], int]:
    """본인 필터 목록(페이지) — 이름순."""
    with unit_of_work() as uow:
        session = uow.session
        base = (BoardSavedFilter.user_id == actor.id, BoardSavedFilter.deleted_at.is_(None))
        total = int(
            session.execute(
                select(func.count()).select_from(BoardSavedFilter).where(*base)
            ).scalar_one()
        )
        rows = (
            session.execute(
                select(BoardSavedFilter)
                .where(*base)
                .order_by(BoardSavedFilter.name, BoardSavedFilter.id)
                .offset(offset)
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return [body_of(row) for row in rows], total


def create_saved_filter(
    *, actor: AuthenticatedUser, idempotency_key: str, name: str, filter_config: BoardFilter
) -> tuple[int, dict[str, Any]]:
    """등록 — 한 트랜잭션: 멱등 claim → 사용자 advisory lock → 재계수(20 상한) → 이름 중복 → INSERT(SAVEPOINT) → `complete(201)`."""
    clean = _clean_name(name)
    config = _config_of(filter_config)
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=CREATE_ENDPOINT,
            key=idempotency_key,
            request_body={"name": name, "filter_config": config},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body
        mine = (BoardSavedFilter.user_id == actor.id, BoardSavedFilter.deleted_at.is_(None))
        # ① 사용자 단위 advisory lock으로 등록을 직렬화한다(행이 0개여도 잡힌다). ② 잠근 **뒤** 새 문장으로 센다 — READ COMMITTED에서 잠금을 기다린
        #   문장의 결과는 그 문장 시작 시점 기준이라, 잠금 문의 결과로 세면 대기 중 커밋된 행이 빠진다(실측).
        session.execute(
            text("SELECT pg_advisory_xact_lock(:ns, CAST(:uid AS integer))"),
            {"ns": SAVED_FILTER_LOCK_NS, "uid": actor.id},
        )
        active = int(
            session.execute(
                select(func.count()).select_from(BoardSavedFilter).where(*mine)
            ).scalar_one()
        )
        if active >= SAVED_FILTER_LIMIT:
            raise AppError(
                ErrorCode.ORDER_BOARD_FILTER_LIMIT_REACHED,
                detail={"limit": SAVED_FILTER_LIMIT},
            )
        if _name_taken(session, actor.id, clean, exclude_id=None):
            raise _duplicate_name()
        row = BoardSavedFilter(
            user_id=actor.id,
            name=clean,
            filter_config=config,
            created_by_id=actor.id,
            updated_by_id=actor.id,
        )
        with _guarded(session):
            session.add(row)
            session.flush()
        session.refresh(row)
        body = idempotency_body(body_of(row))
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def update_saved_filter(
    *, actor: AuthenticatedUser, filter_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    """수정 — 보낸 필드만. 실제 변경이 없으면 version을 올리지 않는다."""
    with unit_of_work() as uow:
        session = uow.session
        row = _require_own(session, actor.id, filter_id, version=payload["version"])
        changed = False
        with _guarded(session):
            if payload.get("name") is not None:
                clean = _clean_name(payload["name"])
                if clean != row.name:
                    if _name_taken(session, actor.id, clean, exclude_id=row.id):
                        raise _duplicate_name()
                    row.name = clean
                    changed = True
            if payload.get("filter_config") is not None:
                config = _config_of(BoardFilter.model_validate(payload["filter_config"]))
                if config != row.filter_config:
                    row.filter_config = config
                    changed = True
            if changed:
                row.updated_by_id = actor.id
                session.flush()
        session.refresh(row)
        return body_of(row)


def delete_saved_filter(*, actor: AuthenticatedUser, filter_id: int, version: int) -> None:
    """삭제(soft delete) — 이름은 해방된다(같은 이름으로 다시 저장 가능)."""
    with unit_of_work() as uow:
        session = uow.session
        row = _require_own(session, actor.id, filter_id, version=version)
        row.deleted_at = utcnow()
        row.updated_by_id = actor.id
        session.flush()


def idempotency_body(body: dict[str, Any]) -> dict[str, Any]:
    """멱등 저장(JSONB)용 — 시각을 ISO 문자열로(응답 모델이 같은 값을 다시 파싱한다)."""
    import json

    return dict(json.loads(json.dumps(body, default=str)))
