"""라인 스냅샷 — 마스터 값을 전표로 **복사**하는 두 함수 (S3-1 ADR-0056 / design-A A8·A9).

■ 두 함수만이 라인 스냅샷을 만든다: `snapshot_line_from_master`(수기 추가 — 마스터를 1회 읽는다)와
  `snapshot_line_from_source`(참조 생성·개정 — 선행 전표 값을 그대로 복사하고 **마스터를 다시 읽지 않는다**).
  마스터(SKU명·판가·품번)를 이후 바꿔도 발행된 전표 값은 불변이다(ADR-0017 "이력과 스냅샷은 값 복사").
■ 단가: 기본=`price_at`(SALES) 규칙(부재는 `CATALOG.PRICE.NOT_EFFECTIVE` 422를 그대로 — 0·NULL 대체 금지),
  마스터 판가 0은 자동 수용하지 않는다(무상은 명시). 수동 입력은 자릿수 초과를 **거부**한다(반올림 금지 —
  `parse_minor_amount`). 수동 라인도 그 시점 마스터 판가를 `list_price_amount`로 남겨 편차가 값으로 보인다.
■ `buyer_item_code`: (바이어, SKU)의 활성 매핑이 정확히 1건이면 그 값, 0건이면 NULL, 2건 이상이면 사용자가 그
  집합에서 직접 지정(미지정 NULL — 추측 선택 금지).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError
from app.core.money import parse_minor_amount
from app.modules.catalog.models import Sku
from app.modules.catalog.pricing import prices_at
from app.modules.partners.models import CustomerItemCode
from app.modules.trade_docs.constants import (
    MAX_QUANTITY,
    MAX_SAFE_INTEGER,
    PriceBasis,
)

PRICE_REASON_MAX = 200


@dataclass(frozen=True, slots=True)
class LineSnapshot:
    sku_id: int
    sku_code: str
    sku_name_ko: str
    sku_name_en: str | None
    sku_kind: str
    buyer_item_code: str | None
    unit_price_amount: int
    list_price_amount: int | None
    price_basis: str
    is_free: bool
    price_reason: str | None


def _invalid(field: str, message: str) -> AppError:
    return AppError(ErrorCode.VALIDATION_INVALID_FIELD, detail={field: message})


def validate_quantity(quantity: object, *, field: str = "quantity") -> int:
    """EA 정수 1..99,999,999 — BOX 입력·환산은 화면 몫(서버는 EA만)."""
    if (
        isinstance(quantity, bool)
        or not isinstance(quantity, int)
        or not 1 <= quantity <= MAX_QUANTITY
    ):
        raise _invalid(field, f"수량은 1 ~ {MAX_QUANTITY:,} 사이의 정수(EA)로 입력해 주세요.")
    return quantity


def line_amount(quantity: int, unit_price_amount: int) -> int:
    """라인금액 = 수량 × 단가(정수 곱, 반올림 없음). 상한(2^53−1) 초과는 422."""
    amount = quantity * unit_price_amount
    if amount > MAX_SAFE_INTEGER:
        raise AppError(
            ErrorCode.TRADE_DOCS_LINE_AMOUNT_OUT_OF_RANGE,
            detail={"unit_price": "라인 금액이 허용 범위를 넘었습니다."},
        )
    return amount


def _clean_reason(raw: object, *, field: str) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if len(text) > PRICE_REASON_MAX:
        raise _invalid(field, f"사유는 {PRICE_REASON_MAX}자 이내로 입력해 주세요.")
    return text


def resolve_buyer_item_code(
    session: Session, buyer_partner_id: int, sku_id: int, requested: str | None, *, field: str
) -> str | None:
    """바이어 품번 스냅샷 결정 규칙(위 모듈 독스트링). 지정값이 활성 매핑 집합 밖이면 422."""
    codes = list(
        session.execute(
            select(CustomerItemCode.buyer_item_code)
            .where(
                CustomerItemCode.partner_id == buyer_partner_id,
                CustomerItemCode.sku_id == sku_id,
                CustomerItemCode.deleted_at.is_(None),
            )
            .order_by(CustomerItemCode.id)
        ).scalars()
    )
    wanted = requested.strip() if requested else None
    if wanted:
        if wanted not in codes:
            raise _invalid(
                field, "이 바이어에게 등록된 품번이 아닙니다. 바이어 품번 매핑을 확인해 주세요."
            )
        return wanted
    return codes[0] if len(codes) == 1 else None


def master_list_price(session: Session, sku_id: int, currency: str, doc_date: date) -> int | None:
    """그 시점 마스터 표준 판가(없으면 None) — list_price_amount 스냅샷의 원천."""
    found = prices_at(session, sku_ids=[sku_id], price_type="SALES", currency=currency, on=doc_date)
    return found[sku_id].amount if sku_id in found else None


def snapshot_line_from_master(
    session: Session,
    *,
    sku: Sku,
    buyer_partner_id: int,
    currency: str,
    doc_date: date,
    unit_price: object | None,
    is_free: bool,
    price_reason: object | None,
    buyer_item_code: str | None,
    field_prefix: str = "",
) -> LineSnapshot:
    """수기 라인 추가 — 마스터를 1회 읽어 값 복사한다(부재·0원 판가는 거부, 자릿수 초과는 거부)."""
    prefix = field_prefix
    master = master_list_price(session, sku.id, currency, doc_date)
    reason = _clean_reason(price_reason, field=f"{prefix}price_reason")
    code = resolve_buyer_item_code(
        session, buyer_partner_id, sku.id, buyer_item_code, field=f"{prefix}buyer_item_code"
    )
    if is_free:
        if (
            unit_price is not None
            and _parse_price(unit_price, currency, f"{prefix}unit_price") != 0
        ):
            raise _invalid(f"{prefix}unit_price", "무상 라인의 단가는 0이어야 합니다.")
        if reason is None:
            raise _invalid(f"{prefix}price_reason", "무상 라인은 사유를 입력해 주세요.")
        unit, basis = 0, PriceBasis.MANUAL.value
    elif unit_price is not None:
        unit = _parse_price(unit_price, currency, f"{prefix}unit_price")
        if unit == 0:
            raise _invalid(
                f"{prefix}unit_price", "0원 단가는 무상(is_free)으로 명시하고 사유를 입력해 주세요."
            )
        basis = PriceBasis.MANUAL.value
    else:
        if master is None:
            raise AppError(
                ErrorCode.CATALOG_PRICE_NOT_EFFECTIVE,
                detail={
                    f"{prefix}unit_price": f"{doc_date.isoformat()} 기준으로 적용되는 {currency} 판가가 "
                    "없습니다. 마스터에 판가를 등록하거나 단가를 직접 입력해 주세요."
                },
                log_context={"sku_id": sku.id, "currency": currency},
            )
        if master == 0:
            raise _invalid(
                f"{prefix}unit_price",
                "마스터 판가가 0원입니다. 무상은 is_free로 명시하고 사유를 입력해 주세요.",
            )
        unit, basis = master, PriceBasis.MASTER.value
    return LineSnapshot(
        sku_id=sku.id,
        sku_code=sku.sku_code,
        sku_name_ko=sku.name_ko,
        sku_name_en=sku.name_en,
        sku_kind=sku.kind,
        buyer_item_code=code,
        unit_price_amount=unit,
        list_price_amount=master,
        price_basis=basis,
        is_free=is_free,
        price_reason=reason,
    )


def snapshot_line_from_source(source: Any) -> LineSnapshot:
    """참조 생성·개정 — 선행 전표 라인 값을 그대로 복사한다(마스터 재조회 금지). `line_amount`는 새 수량 × 복사 단가."""
    return LineSnapshot(
        sku_id=source.sku_id,
        sku_code=source.sku_code,
        sku_name_ko=source.sku_name_ko,
        sku_name_en=source.sku_name_en,
        sku_kind=source.sku_kind,
        buyer_item_code=source.buyer_item_code,
        unit_price_amount=source.unit_price_amount,
        list_price_amount=source.list_price_amount,
        price_basis=source.price_basis,
        is_free=source.is_free,
        price_reason=source.price_reason,
    )


def _parse_price(raw: object, currency: str, field: str) -> int:
    try:
        return parse_minor_amount(raw, currency, field=field, max_digits=16)
    except ValueError as exc:
        raise _invalid(field, str(exc).split(": ", 1)[-1]) from None


def reprice(
    current: Any,
    currency: str,
    *,
    unit_price: object | None,
    is_free: bool | None,
    price_reason: object | None,
    prefix: str = "",
) -> tuple[int, str, bool, str | None]:
    """기존 라인의 가격 필드 부분 수정 → (단가, 기준, 무상, 사유).

    ★ 단가를 바꾸면 기준은 MANUAL이다(원출처가 BUYER_PO이고 값이 그대로면 유지). 마스터를 다시 읽지 않는다 —
      list_price_amount는 라인 생성 시점 스냅샷 그대로다.
    """
    free = current.is_free if is_free is None else is_free
    reason = (
        current.price_reason
        if price_reason is None
        else _clean_reason(price_reason, field=f"{prefix}price_reason")
    )
    if free:
        if (
            unit_price is not None
            and _parse_price(unit_price, currency, f"{prefix}unit_price") != 0
        ):
            raise _invalid(f"{prefix}unit_price", "무상 라인의 단가는 0이어야 합니다.")
        if reason is None:
            raise _invalid(f"{prefix}price_reason", "무상 라인은 사유를 입력해 주세요.")
        basis = (
            current.price_basis
            if current.price_basis == PriceBasis.BUYER_PO.value
            else PriceBasis.MANUAL.value
        )
        return 0, basis, True, reason
    if unit_price is not None:
        unit = _parse_price(unit_price, currency, f"{prefix}unit_price")
        if unit == 0:
            raise _invalid(
                f"{prefix}unit_price", "0원 단가는 무상(is_free)으로 명시하고 사유를 입력해 주세요."
            )
        basis = (
            current.price_basis if unit == current.unit_price_amount else PriceBasis.MANUAL.value
        )
        return unit, basis, False, reason
    if current.is_free:  # 무상 → 유상 전환에는 단가가 필요하다
        raise _invalid(f"{prefix}unit_price", "무상을 해제하려면 단가를 입력해 주세요.")
    return current.unit_price_amount, current.price_basis, False, reason
