"""단가 이력 (DESIGN.md §4.1 "그때 단가" / §2 권한·통제 / ADR-0017·0018).

■ 이 모듈이 지키는 두 규칙

  ① **기준일에 단가가 없으면 오류다.** 0이나 null을 돌려주지 않는다 — S3-1의
     전표 단가 스냅샷이 이 조회를 소비하므로, 0을 돌려주면 금액 0원 전표가
     조용히 확정되고 그건 §20 A의 "확정 건 단가 불변"으로도 못 잡는다
     (애초에 틀린 값이 확정되기 때문이다).

  ② **매입가는 원가다.** 조회 역할에게는 필드가 아니라 **행 단위로** 보이지
     않는다(ADR-0018) — 목록에서 빠지고 건수에도 안 잡히며, 단건은 404다.
     403이 아니다: 403은 "네가 볼 수 없는 원가가 여기 존재한다"를 알려 주고,
     존재 사실과 건수만으로도 마진 추정의 단서가 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, NotFoundError
from app.core.money import Money, UnknownCurrencyError, minor_units
from app.core.time import today_kst
from app.modules.catalog.models import SkuPrice
from app.modules.catalog.service import require_sku
from app.modules.idempotency import service as idempotency
from app.modules.identity.models import RoleCode
from app.modules.identity.service import AuthenticatedUser

PRICE_CREATE_ENDPOINT = "POST /api/v1/skus/{sku_id}/prices"

#: 원가(매입가)를 볼 수 있는 역할.
#:
#: ★ §2는 "**조회**는 원가·마진을 API 응답 레벨에서 마스킹"이라고만 규정한다.
#:   문언 그대로 읽으면 나머지 네 역할은 볼 수 있다. 역할이 하나도 없는 계정은
#:   못 보는 쪽으로 둔다 — 기본값은 안전한 쪽이어야 한다.
COST_VISIBLE_ROLES = (RoleCode.ADMIN, RoleCode.TRADE, RoleCode.LOGISTICS, RoleCode.CERT)

COST_PRICE_TYPE = "PURCHASE"


def may_see_cost(actor: AuthenticatedUser) -> bool:
    return actor.has_any_role(*COST_VISIBLE_ROLES)


@dataclass(frozen=True, slots=True)
class SkuPriceView:
    id: int
    sku_id: int
    price_type: str
    currency: str
    #: 정수 최소단위. 표시 변환은 화면이 한다(통화별 자릿수는 /system/currencies).
    amount: int
    effective_from: date
    note: str | None
    #: 이 (종류, 통화)에서 **오늘 적용되는** 행인가. 미래 발효 행이 있어도
    #: "지금 얼마인가"를 화면이 바로 알 수 있게 서버가 계산해 준다.
    is_current: bool


def _view(row: SkuPrice, *, is_current: bool) -> SkuPriceView:
    return SkuPriceView(
        id=row.id,
        sku_id=row.sku_id,
        price_type=row.price_type,
        currency=row.currency,
        amount=row.amount,
        effective_from=row.effective_from,
        note=row.note,
        is_current=is_current,
    )


def _serialize(view: SkuPriceView) -> dict[str, Any]:
    return {
        "id": view.id,
        "sku_id": view.sku_id,
        "price_type": view.price_type,
        "currency": view.currency,
        "amount": view.amount,
        "effective_from": view.effective_from.isoformat(),
        "note": view.note,
        "is_current": view.is_current,
    }


def _to_money(raw: Any, currency: str) -> Money:
    """사람이 쓴 표기(12.34)를 정수 최소단위로. float은 거치지 않는다(§2 ADR-02)."""
    try:
        return Money.from_decimal(Decimal(str(raw)), currency)
    except UnknownCurrencyError as exc:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={
                "currency": f"등록되지 않은 통화({currency})입니다. "
                "쓰려면 통화별 소수 자릿수를 먼저 등록해야 합니다."
            },
            log_context={"currency": currency},
        ) from exc
    except (InvalidOperation, ValueError) as exc:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"amount": "금액은 숫자여야 합니다."},
        ) from exc


def record_price(
    *, actor: AuthenticatedUser, idempotency_key: str, sku_id: int, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """단가를 이력에 추가한다. 같은 (SKU·종류·통화·발효일)은 한 번만."""
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=PRICE_CREATE_ENDPOINT,
            # ★ 금액은 멱등 키 판정에 필요하지만 로그로 새면 안 된다. 여기 담기는
            #   값은 DB(idempotency_keys)에만 남고 로그로는 나가지 않는다.
            key=idempotency_key,
            request_body={**payload, "sku_id": sku_id},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        sku = require_sku(session, sku_id)
        currency = str(payload["currency"]).strip().upper()
        money = _to_money(payload["amount"], currency)
        effective_from = date.fromisoformat(str(payload["effective_from"]))

        row = SkuPrice(
            sku_id=sku.id,
            price_type=str(payload["price_type"]),
            currency=money.currency,
            amount=money.amount,
            effective_from=effective_from,
            note=payload.get("note"),
            created_by_id=actor.id,
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError as exc:
            # ★ 실패 경로에서 ORM 속성을 읽지 않는다 — flush 실패 뒤 세션은 롤백
            #   대기 상태라 만료 속성 접근이 PendingRollbackError로 원인을 뒤바꾼다.
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={
                    "effective_from": "같은 발효일의 단가가 이미 있습니다. "
                    "기존 항목을 고치거나 다른 발효일로 등록해 주세요."
                },
                log_context={"sku_id": sku_id, "price_type": payload.get("price_type")},
            ) from exc

        body = _serialize(_view(row, is_current=_is_current(session, row)))
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def _is_current(session: Session, row: SkuPrice) -> bool:
    """이 행이 (종류, 통화)에서 오늘 적용되는 단가인가."""
    today = today_kst()
    if row.effective_from > today:
        return False
    latest = session.execute(
        select(func.max(SkuPrice.effective_from)).where(
            SkuPrice.sku_id == row.sku_id,
            SkuPrice.price_type == row.price_type,
            SkuPrice.currency == row.currency,
            SkuPrice.effective_from <= today,
            SkuPrice.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    return bool(latest == row.effective_from)


def _visible_types(actor: AuthenticatedUser) -> list[str] | None:
    """조회 역할에게 감출 종류. None이면 제한 없음."""
    return None if may_see_cost(actor) else [COST_PRICE_TYPE]


def list_prices(
    *, actor: AuthenticatedUser, sku_id: int, offset: int, limit: int
) -> tuple[list[SkuPriceView], int]:
    """단가 이력. **조회 역할에게는 매입가 행이 아예 없다**(ADR-0018)."""
    with unit_of_work() as uow:
        session = uow.session
        require_sku(session, sku_id)

        conditions = [SkuPrice.sku_id == sku_id, SkuPrice.deleted_at.is_(None)]
        hidden = _visible_types(actor)
        if hidden is not None:
            conditions.append(SkuPrice.price_type.not_in(hidden))

        # 건수도 같은 조건으로 센다 — total만 전체로 두면 "안 보이는 행이 몇 건
        # 있는지"가 새어 나가고, 그 건수 자체가 원가 변경 횟수라는 정보다.
        total = session.execute(
            select(func.count()).select_from(SkuPrice).where(*conditions)
        ).scalar_one()
        rows = session.execute(
            select(SkuPrice)
            .where(*conditions)
            .order_by(
                SkuPrice.price_type,
                SkuPrice.currency,
                SkuPrice.effective_from.desc(),
            )
            .offset(offset)
            .limit(limit)
        ).scalars()
        return [_view(row, is_current=_is_current(session, row)) for row in rows], total


def get_price(*, actor: AuthenticatedUser, sku_id: int, price_id: int) -> SkuPriceView:
    """단건 조회. 감춰야 하는 행은 403이 아니라 **404**다(ADR-0018)."""
    with unit_of_work() as uow:
        session = uow.session
        row = session.execute(
            select(SkuPrice).where(
                SkuPrice.id == price_id,
                SkuPrice.sku_id == sku_id,
                SkuPrice.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        hidden = _visible_types(actor)
        if row is None or (hidden is not None and row.price_type in hidden):
            raise NotFoundError(log_context={"sku_id": sku_id, "price_id": price_id})
        return _view(row, is_current=_is_current(session, row))


def price_at(*, sku_id: int, price_type: str, currency: str, on: date | None = None) -> Money:
    """기준일에 적용되는 단가 (§4.1 "그때 단가").

    `effective_from <= 기준일` 중 **최댓값 1행**. 없으면 오류다 — 0이나 null을
    돌려주면 호출자마다 다른 기본값이 붙고, 금액 0원 전표가 조용히 확정된다.

    ★ 권한 검사를 여기서 하지 않는다. 이 함수는 서버 내부 계산(S3-1 전표 스냅샷)
      용이고, 사람에게 보여 줄지는 부르는 쪽이 판단한다 — 마스킹은 응답을 만드는
      경계(list_prices·get_price)의 일이다.
    """
    reference = on or today_kst()
    code = currency.upper()
    minor_units(code)  # 미등록 통화면 여기서 실패한다(money.py가 유일한 출처).

    with unit_of_work() as uow:
        row = uow.session.execute(
            select(SkuPrice)
            .where(
                SkuPrice.sku_id == sku_id,
                SkuPrice.price_type == price_type,
                SkuPrice.currency == code,
                SkuPrice.effective_from <= reference,
                SkuPrice.deleted_at.is_(None),
            )
            .order_by(SkuPrice.effective_from.desc())
            .limit(1)
        ).scalar_one_or_none()

    if row is None:
        raise AppError(
            ErrorCode.CATALOG_PRICE_NOT_EFFECTIVE,
            detail={
                "effective_from": f"{reference.isoformat()} 기준으로 적용되는 "
                f"{code} 단가가 없습니다."
            },
            log_context={
                "sku_id": sku_id,
                "price_type": price_type,
                "currency": code,
                "on": reference.isoformat(),
            },
        )
    return Money(row.amount, row.currency)
