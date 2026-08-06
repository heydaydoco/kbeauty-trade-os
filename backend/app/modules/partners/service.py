"""거래처·품번 매핑·서명권자 서비스 (§4.6·§4.7 / §17.4 멱등 / §18.4 페이지네이션).

여신한도는 마스킹하지 않는다(마스킹 원장 2번 — 웹 세션 판정 2026-08-05).
그래도 로그에는 값을 싣지 않는다 — 필요한 진단은 코드·ID로 충분하다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, NotFoundError
from app.core.money import minor_units
from app.modules.catalog.models import Sku
from app.modules.idempotency import service as idempotency
from app.modules.identity.service import AuthenticatedUser
from app.modules.outbox import service as outbox
from app.modules.partners.models import (
    PARTNER_TYPES,
    CustomerItemCode,
    Partner,
    PartnerTypeLink,
    Signatory,
)

PARTNER_CREATE_ENDPOINT = "POST /api/v1/partners"
ITEM_CODE_CREATE_ENDPOINT = "POST /api/v1/partners/{partner_id}/item-codes"
SIGNATORY_CREATE_ENDPOINT = "POST /api/v1/signatories"

#: CSV 내보내기 상한 — catalog·ingredients·materials와 같은 값·같은 이유.
EXPORT_MAX_ROWS = 50_000


# ── 뷰 ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PartnerView:
    id: int
    partner_code: str
    name_ko: str
    #: 정렬해 둔다 — 응답·CSV가 요청 순서에 흔들리지 않게.
    type_codes: tuple[str, ...]
    credit_limit_amount: int | None
    credit_limit_currency: str | None
    dg_capable: bool | None
    strengths: str | None
    weaknesses: str | None
    note: str | None


@dataclass(frozen=True, slots=True)
class ItemCodeView:
    id: int
    partner_id: int
    partner_code: str
    sku_id: int
    sku_code: str
    buyer_item_code: str
    note: str | None


@dataclass(frozen=True, slots=True)
class SignatoryView:
    id: int
    name: str
    title: str | None
    note: str | None


def _partner_view(row: Partner, type_codes: list[str]) -> PartnerView:
    return PartnerView(
        id=row.id,
        partner_code=row.partner_code,
        name_ko=row.name_ko,
        type_codes=tuple(sorted(type_codes)),
        credit_limit_amount=row.credit_limit_amount,
        credit_limit_currency=row.credit_limit_currency,
        dg_capable=row.dg_capable,
        strengths=row.strengths,
        weaknesses=row.weaknesses,
        note=row.note,
    )


def _serialize_partner(view: PartnerView) -> dict[str, Any]:
    return {
        "id": view.id,
        "partner_code": view.partner_code,
        "name_ko": view.name_ko,
        "type_codes": list(view.type_codes),
        "credit_limit_amount": view.credit_limit_amount,
        "credit_limit_currency": view.credit_limit_currency,
        "dg_capable": view.dg_capable,
        "strengths": view.strengths,
        "weaknesses": view.weaknesses,
        "note": view.note,
    }


def _serialize_item_code(view: ItemCodeView) -> dict[str, Any]:
    return {
        "id": view.id,
        "partner_id": view.partner_id,
        "partner_code": view.partner_code,
        "sku_id": view.sku_id,
        "sku_code": view.sku_code,
        "buyer_item_code": view.buyer_item_code,
        "note": view.note,
    }


def _serialize_signatory(view: SignatoryView) -> dict[str, Any]:
    return {"id": view.id, "name": view.name, "title": view.title, "note": view.note}


# ── 공통 ───────────────────────────────────────────────────────────────────


def parse_credit_limit(payload: dict[str, Any]) -> tuple[int | None, str | None]:
    """사람이 쓰는 표기(12.34)를 정수 최소단위로 (§2 ADR-02).

    materials._minor_amount와 같은 꼴이다. 여신한도는 원가가 아니지만(마스킹
    원장 2번) 금액 규약은 동일하게 지킨다 — 정수 최소단위+통화 쌍.
    공개 함수다 — 왕복 임포트(§12.2)가 같은 검증·같은 문구를 재사용한다.
    """
    raw = payload.get("credit_limit")
    currency = payload.get("credit_limit_currency")
    if raw is None and currency is None:
        return None, None
    if raw is None or currency is None:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"credit_limit": "여신한도와 통화는 함께 입력하거나 함께 비워 주세요."},
        )
    code = str(currency).strip().upper()
    try:
        exponent = minor_units(code)
    except Exception as exc:  # UnknownCurrencyError — 통화 목록은 money.py가 유일 출처
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"credit_limit_currency": f"등록되지 않은 통화입니다: {code}"},
            log_context={"credit_limit_currency": code},
        ) from exc
    try:
        amount = Decimal(str(raw))
    except InvalidOperation as exc:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"credit_limit": "여신한도는 숫자여야 합니다."},
        ) from exc
    scaled = amount.scaleb(exponent)
    if scaled != scaled.to_integral_value():
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={
                "credit_limit": f"{code}는 소수점 {exponent}자리까지 쓸 수 있습니다. "
                "자릿수를 확인해 주세요."
            },
        )
    return int(scaled), code


def normalized_type_codes(payload: dict[str, Any]) -> list[str]:
    """유형 목록 정규화 — 중복 제거·정렬. 최소 1개는 있어야 한다.

    공개 함수다 — 왕복 임포트(§12.2)가 같은 검증·같은 문구를 재사용한다.

    §4.6이 거래처를 유형으로 정의한다 — 유형 없는 거래처는 어떤 화면(§7.7
    소싱 제외, §7.4 바이어 게이트)에도 걸리지 않는 유령 행이 된다. 자식 행
    개수는 DB CHECK로 강제할 수 없어 여기가 판정 지점이다(테스트가 지킨다).
    """
    raw = payload.get("type_codes") or []
    codes = sorted({str(code).strip().upper() for code in raw if str(code).strip()})
    if not codes:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"type_codes": "거래처 유형을 1개 이상 선택해 주세요."},
        )
    unknown = [code for code in codes if code not in PARTNER_TYPES]
    if unknown:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"type_codes": f"알 수 없는 거래처 유형입니다: {', '.join(unknown)}"},
            log_context={"type_codes": unknown},
        )
    return codes


def require_partner(session: Session, partner_id: int) -> Partner:
    row = session.execute(
        select(Partner).where(Partner.id == partner_id, Partner.deleted_at.is_(None))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(log_context={"partner_id": partner_id})
    return row


def partner_type_codes(session: Session, partner_id: int) -> list[str]:
    rows = session.execute(
        select(PartnerTypeLink.type_code).where(
            PartnerTypeLink.partner_id == partner_id, PartnerTypeLink.deleted_at.is_(None)
        )
    ).scalars()
    return list(rows)


def partner_has_type(session: Session, partner_id: int, type_code: str) -> bool:
    """유형 보유 판정 — 소비처: 품번 매핑(BUYER)·SKU 제조사(OEM)·자재 공급사(SUPPLIER)."""
    return type_code in partner_type_codes(session, partner_id)


def _require_sku(session: Session, sku_id: int) -> Sku:
    row = session.execute(
        select(Sku).where(Sku.id == sku_id, Sku.deleted_at.is_(None))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(log_context={"sku_id": sku_id})
    return row


def _guard_export_size(total: int) -> None:
    if total > EXPORT_MAX_ROWS:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={
                "size": f"내보낼 자료가 너무 많습니다({total:,}건). "
                f"{EXPORT_MAX_ROWS:,}건 이하가 되도록 조건을 좁혀 주세요."
            },
        )


def _type_codes_by_partner(session: Session, partner_ids: list[int]) -> dict[int, list[str]]:
    """페이지 분량의 유형을 한 번에 가져온다 — 행마다 조회하면 N+1이다(§18.4)."""
    if not partner_ids:
        return {}
    rows = session.execute(
        select(PartnerTypeLink.partner_id, PartnerTypeLink.type_code).where(
            PartnerTypeLink.partner_id.in_(partner_ids),
            PartnerTypeLink.deleted_at.is_(None),
        )
    ).all()
    grouped: dict[int, list[str]] = {}
    for partner_id, type_code in rows:
        grouped.setdefault(partner_id, []).append(type_code)
    return grouped


# ── 거래처 마스터 (§4.6) ───────────────────────────────────────────────────


def create_partner(
    *, actor: AuthenticatedUser, idempotency_key: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=PARTNER_CREATE_ENDPOINT,
            key=idempotency_key,
            request_body=payload,
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        type_codes = normalized_type_codes(payload)
        credit_amount, credit_currency = parse_credit_limit(payload)

        partner = Partner(
            partner_code=str(payload["partner_code"]).strip(),
            name_ko=str(payload["name_ko"]).strip(),
            credit_limit_amount=credit_amount,
            credit_limit_currency=credit_currency,
            dg_capable=payload.get("dg_capable"),
            strengths=payload.get("strengths"),
            weaknesses=payload.get("weaknesses"),
            note=payload.get("note"),
            created_by_id=actor.id,
        )
        session.add(partner)
        try:
            session.flush()
        except IntegrityError as exc:
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={
                    "partner_code": "이미 등록된 거래처 코드입니다. 다른 코드를 입력해 주세요."
                },
                log_context={"partner_code": payload.get("partner_code")},
            ) from exc

        for code in type_codes:
            session.add(
                PartnerTypeLink(partner_id=partner.id, type_code=code, created_by_id=actor.id)
            )
        session.flush()

        outbox.publish(
            session,
            event_type="partners.partner.created",
            aggregate_type="partners",
            aggregate_id=partner.id,
            payload={"partner_id": partner.id, "partner_code": partner.partner_code},
        )

        body = _serialize_partner(_partner_view(partner, type_codes))
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def list_partners(*, offset: int, limit: int) -> tuple[list[PartnerView], int]:
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(Partner).where(Partner.deleted_at.is_(None))
        ).scalar_one()
        rows = list(
            session.execute(
                select(Partner)
                .where(Partner.deleted_at.is_(None))
                .order_by(Partner.partner_code)
                .offset(offset)
                .limit(limit)
            ).scalars()
        )
        grouped = _type_codes_by_partner(session, [row.id for row in rows])
        return [_partner_view(row, grouped.get(row.id, [])) for row in rows], total


def all_partners_for_export() -> list[PartnerView]:
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(Partner).where(Partner.deleted_at.is_(None))
        ).scalar_one()
        _guard_export_size(total)
        rows = list(
            session.execute(
                select(Partner).where(Partner.deleted_at.is_(None)).order_by(Partner.partner_code)
            ).scalars()
        )
        grouped = _type_codes_by_partner(session, [row.id for row in rows])
        return [_partner_view(row, grouped.get(row.id, [])) for row in rows]


def get_partner(partner_id: int) -> PartnerView:
    with unit_of_work() as uow:
        session = uow.session
        row = require_partner(session, partner_id)
        return _partner_view(row, partner_type_codes(session, row.id))


# ── 바이어 품번 매핑 (§4.6 customer_item_codes) ────────────────────────────


def add_item_code(
    *, actor: AuthenticatedUser, idempotency_key: str, partner_id: int, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=ITEM_CODE_CREATE_ENDPOINT,
            key=idempotency_key,
            request_body={**payload, "partner_id": partner_id},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        partner = require_partner(session, partner_id)
        # 품번 매핑은 바이어의 것이다(§4.6 "바이어 품번 매핑") — 유형이 없는
        # 거래처에 매핑이 붙으면 §7.4 인테이크 게이트가 바이어가 아닌 상대의
        # 품번을 신뢰하게 된다.
        if not partner_has_type(session, partner.id, "BUYER"):
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={
                    "partner_id": "바이어 유형이 아닌 거래처입니다. 품번 매핑은 "
                    "BUYER 유형을 가진 거래처에만 등록할 수 있습니다."
                },
                log_context={"partner_id": partner_id},
            )
        sku = _require_sku(session, int(payload["sku_id"]))
        # 실패 경로에서 쓸 값은 flush 전에 빼 둔다(함정 ②).
        partner_pk = partner.id
        partner_code = partner.partner_code
        sku_pk = sku.id
        sku_code = sku.sku_code

        row = CustomerItemCode(
            partner_id=partner_pk,
            sku_id=sku_pk,
            buyer_item_code=str(payload["buyer_item_code"]).strip(),
            note=payload.get("note"),
            created_by_id=actor.id,
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError as exc:
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={
                    "buyer_item_code": "이 거래처에 같은 바이어 품번이 이미 있습니다. "
                    "다른 SKU로 바꾸려면 기존 매핑을 정리해 주세요."
                },
                log_context={"partner_id": partner_pk, "sku_id": sku_pk},
            ) from exc

        view = ItemCodeView(
            id=row.id,
            partner_id=partner_pk,
            partner_code=partner_code,
            sku_id=sku_pk,
            sku_code=sku_code,
            buyer_item_code=row.buyer_item_code,
            note=row.note,
        )
        body = _serialize_item_code(view)
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def list_item_codes(*, partner_id: int, offset: int, limit: int) -> tuple[list[ItemCodeView], int]:
    with unit_of_work() as uow:
        session = uow.session
        partner = require_partner(session, partner_id)
        condition = (
            CustomerItemCode.partner_id == partner_id,
            CustomerItemCode.deleted_at.is_(None),
        )
        total = session.execute(
            select(func.count()).select_from(CustomerItemCode).where(*condition)
        ).scalar_one()
        rows = session.execute(
            select(CustomerItemCode, Sku.sku_code)
            .join(Sku, CustomerItemCode.sku_id == Sku.id)
            .where(*condition)
            .order_by(CustomerItemCode.buyer_item_code)
            .offset(offset)
            .limit(limit)
        ).all()
        return [
            ItemCodeView(
                id=row.id,
                partner_id=row.partner_id,
                partner_code=partner.partner_code,
                sku_id=row.sku_id,
                sku_code=sku_code,
                buyer_item_code=row.buyer_item_code,
                note=row.note,
            )
            for row, sku_code in rows
        ], total


# ── 서명권자 대장 (§4.7 — 최소 헤더) ───────────────────────────────────────


def create_signatory(
    *, actor: AuthenticatedUser, idempotency_key: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=SIGNATORY_CREATE_ENDPOINT,
            key=idempotency_key,
            request_body=payload,
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        row = Signatory(
            name=str(payload["name"]).strip(),
            title=payload.get("title"),
            note=payload.get("note"),
            created_by_id=actor.id,
        )
        session.add(row)
        session.flush()

        outbox.publish(
            session,
            event_type="partners.signatory.created",
            aggregate_type="signatories",
            aggregate_id=row.id,
            payload={"signatory_id": row.id},
        )

        body = _serialize_signatory(
            SignatoryView(id=row.id, name=row.name, title=row.title, note=row.note)
        )
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def list_signatories(*, offset: int, limit: int) -> tuple[list[SignatoryView], int]:
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(Signatory).where(Signatory.deleted_at.is_(None))
        ).scalar_one()
        rows = session.execute(
            select(Signatory)
            .where(Signatory.deleted_at.is_(None))
            .order_by(Signatory.name, Signatory.id)
            .offset(offset)
            .limit(limit)
        ).scalars()
        return [
            SignatoryView(id=row.id, name=row.name, title=row.title, note=row.note) for row in rows
        ], total
