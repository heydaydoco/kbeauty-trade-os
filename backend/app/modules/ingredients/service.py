"""성분 서비스 (DESIGN.md §4.3 / §17.4 멱등 / §18.4 페이지네이션).

★ 여기 있는 것은 전부 **기록**이다. 판정처럼 보일 수 있는 것(스크리닝)은
  screening.py가 따로 맡고, 그것도 판정이 아니라 대조 리포트다(§1 비범위).
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
from app.core.time import today_kst
from app.modules.catalog.models import Product
from app.modules.idempotency import service as idempotency
from app.modules.identity.service import AuthenticatedUser
from app.modules.ingredients.models import Ingredient, IngredientRule, ProductIngredient
from app.modules.outbox import service as outbox

INGREDIENT_CREATE_ENDPOINT = "POST /api/v1/ingredients"
RULE_CREATE_ENDPOINT = "POST /api/v1/ingredients/{ingredient_id}/rules"
PRODUCT_INGREDIENT_CREATE_ENDPOINT = "POST /api/v1/products/{product_id}/ingredients"

#: CSV 내보내기 상한 — catalog.service와 같은 값·같은 이유(조용히 잘린 파일 금지).
EXPORT_MAX_ROWS = 50_000


# ── 뷰 ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class IngredientView:
    id: int
    inci_name: str
    name_ko: str | None
    note: str | None


@dataclass(frozen=True, slots=True)
class IngredientRuleView:
    id: int
    ingredient_id: int
    country_code: str
    rule_type: str
    max_concentration_pct: Decimal | None
    source_url: str
    last_verified_on: date


@dataclass(frozen=True, slots=True)
class ProductIngredientView:
    id: int
    product_id: int
    ingredient_id: int
    inci_name: str
    ingredient_name_ko: str | None
    concentration_pct: Decimal | None
    display_order: int


def _ingredient_view(row: Ingredient) -> IngredientView:
    return IngredientView(id=row.id, inci_name=row.inci_name, name_ko=row.name_ko, note=row.note)


def _rule_view(row: IngredientRule) -> IngredientRuleView:
    return IngredientRuleView(
        id=row.id,
        ingredient_id=row.ingredient_id,
        country_code=row.country_code,
        rule_type=row.rule_type,
        max_concentration_pct=row.max_concentration_pct,
        source_url=row.source_url,
        last_verified_on=row.last_verified_on,
    )


def _text(value: Decimal | None) -> str | None:
    """Decimal → JSON 문자열. float 변환 금지(§2 ADR-02) — catalog.service._text와 같다."""
    return None if value is None else str(value)


def _serialize_ingredient(view: IngredientView) -> dict[str, Any]:
    return {
        "id": view.id,
        "inci_name": view.inci_name,
        "name_ko": view.name_ko,
        "note": view.note,
    }


def _serialize_rule(view: IngredientRuleView) -> dict[str, Any]:
    return {
        "id": view.id,
        "ingredient_id": view.ingredient_id,
        "country_code": view.country_code,
        "rule_type": view.rule_type,
        "max_concentration_pct": _text(view.max_concentration_pct),
        "source_url": view.source_url,
        "last_verified_on": view.last_verified_on.isoformat(),
    }


def _serialize_product_ingredient(view: ProductIngredientView) -> dict[str, Any]:
    return {
        "id": view.id,
        "product_id": view.product_id,
        "ingredient_id": view.ingredient_id,
        "inci_name": view.inci_name,
        "ingredient_name_ko": view.ingredient_name_ko,
        "concentration_pct": _text(view.concentration_pct),
        "display_order": view.display_order,
    }


# ── 공통 검증 ───────────────────────────────────────────────────────────────


def _normalized_inci(raw: str) -> tuple[str, str]:
    """표기용·유일성 판정용 INCI명 쌍.

    공백만 축약하고 대소문자는 보존한다(라벨 인쇄가 표기를 쓴다). 유일성은
    대문자 사본이 잡는다 — 'Aqua'와 'AQUA'는 같은 성분이다.
    """
    display = " ".join(raw.split())
    if not display:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"inci_name": "INCI명을 입력해 주세요."},
        )
    return display, display.upper()


def _guard_verified_on(value: date) -> None:
    """최종확인일은 미래일 수 없다 (ADR-03) — catalog.service와 같은 규칙.

    비교 기준은 **한국 날짜**다(§22 렌즈 6). UTC 날짜로 보면 한국의 00~09시에
    적은 오늘 날짜가 미래로 걸린다.
    """
    today = today_kst()
    if value > today:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={
                "last_verified_on": f"최종확인일이 미래({value.isoformat()})입니다. "
                f"오늘({today.isoformat()}) 이전 날짜를 입력해 주세요."
            },
            log_context={"last_verified_on": value.isoformat()},
        )


def _decimal(payload: dict[str, Any], field: str, label: str) -> Decimal | None:
    """model_dump(mode="json")이 문자열로 바꾼 Decimal을 되돌린다."""
    value = payload.get(field)
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={field: f"{label}은(는) 숫자여야 합니다."},
            log_context={field: value},
        ) from exc


def require_ingredient(session: Session, ingredient_id: int) -> Ingredient:
    row = session.execute(
        select(Ingredient).where(Ingredient.id == ingredient_id, Ingredient.deleted_at.is_(None))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(log_context={"ingredient_id": ingredient_id})
    return row


def require_product(session: Session, product_id: int) -> Product:
    row = session.execute(
        select(Product).where(Product.id == product_id, Product.deleted_at.is_(None))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(log_context={"product_id": product_id})
    return row


def _guard_export_size(total: int) -> None:
    """CSV 내보내기 상한. 잘라 내지 않고 거절한다 — catalog.service와 같은 규칙."""
    if total > EXPORT_MAX_ROWS:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={
                "size": f"내보낼 자료가 너무 많습니다({total:,}건). "
                f"{EXPORT_MAX_ROWS:,}건 이하가 되도록 조건을 좁혀 주세요."
            },
        )


# ── 성분 마스터 ─────────────────────────────────────────────────────────────


def create_ingredient(
    *, actor: AuthenticatedUser, idempotency_key: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=INGREDIENT_CREATE_ENDPOINT,
            key=idempotency_key,
            request_body=payload,
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        display, normalized = _normalized_inci(str(payload["inci_name"]))
        ingredient = Ingredient(
            inci_name=display,
            inci_name_normalized=normalized,
            name_ko=payload.get("name_ko"),
            note=payload.get("note"),
            created_by_id=actor.id,
        )
        session.add(ingredient)
        try:
            session.flush()
        except IntegrityError as exc:
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={
                    "inci_name": "이미 등록된 성분입니다(대소문자·공백 차이는 같은 "
                    "성분으로 봅니다). 기존 성분을 확인해 주세요."
                },
                log_context={"inci_name": normalized},
            ) from exc

        outbox.publish(
            session,
            event_type="ingredients.ingredient.created",
            aggregate_type="ingredients",
            aggregate_id=ingredient.id,
            payload={"ingredient_id": ingredient.id, "inci_name": ingredient.inci_name},
        )

        body = _serialize_ingredient(_ingredient_view(ingredient))
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def list_ingredients(*, offset: int, limit: int) -> tuple[list[IngredientView], int]:
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(Ingredient).where(Ingredient.deleted_at.is_(None))
        ).scalar_one()
        rows = session.execute(
            select(Ingredient)
            .where(Ingredient.deleted_at.is_(None))
            .order_by(Ingredient.inci_name_normalized)
            .offset(offset)
            .limit(limit)
        ).scalars()
        return [_ingredient_view(row) for row in rows], total


def all_ingredients_for_export() -> list[IngredientView]:
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(Ingredient).where(Ingredient.deleted_at.is_(None))
        ).scalar_one()
        _guard_export_size(total)
        rows = session.execute(
            select(Ingredient)
            .where(Ingredient.deleted_at.is_(None))
            .order_by(Ingredient.inci_name_normalized)
        ).scalars()
        return [_ingredient_view(row) for row in rows]


def get_ingredient(ingredient_id: int) -> IngredientView:
    with unit_of_work() as uow:
        return _ingredient_view(require_ingredient(uow.session, ingredient_id))


# ── 성분×국가 규칙 (§4.3 / ADR-03) ─────────────────────────────────────────


def create_ingredient_rule(
    *, actor: AuthenticatedUser, idempotency_key: str, ingredient_id: int, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """성분 규칙을 등록한다 — 판정이 아니라 사람이 확인한 규칙의 **기록**이다."""
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=RULE_CREATE_ENDPOINT,
            key=idempotency_key,
            request_body={**payload, "ingredient_id": ingredient_id},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        ingredient = require_ingredient(session, ingredient_id)
        rule_type = str(payload["rule_type"])
        limit = _decimal(payload, "max_concentration_pct", "농도 한도")
        # 최종 판정자는 DB의 양방향 CHECK다 — 여기서 보는 것은 사용자에게
        # 어느 칸이 왜 잘못됐는지 알려 주기 위한 것이다.
        if rule_type == "RESTRICTED" and limit is None:
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={
                    "max_concentration_pct": "제한(RESTRICTED) 규칙에는 농도 한도가 "
                    "필요합니다. 한도가 없는 금지라면 PROHIBITED로 등록해 주세요."
                },
            )
        if rule_type == "PROHIBITED" and limit is not None:
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={
                    "max_concentration_pct": "금지(PROHIBITED) 규칙에는 농도 한도를 "
                    "넣지 않습니다. 한도 내 허용이라면 RESTRICTED로 등록해 주세요."
                },
            )
        verified_on = date.fromisoformat(str(payload["last_verified_on"]))
        _guard_verified_on(verified_on)

        row = IngredientRule(
            ingredient_id=ingredient.id,
            country_code=str(payload["country_code"]).strip().upper(),
            rule_type=rule_type,
            max_concentration_pct=limit,
            source_url=str(payload["source_url"]).strip(),
            last_verified_on=verified_on,
            created_by_id=actor.id,
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError as exc:
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={
                    "country_code": "이 성분에는 해당 국가의 규칙이 이미 등록돼 있습니다. "
                    "금지·제한이 공존할 수 없습니다 — 기존 규칙을 수정해 주세요."
                },
                log_context={
                    "ingredient_id": ingredient_id,
                    "country_code": payload.get("country_code"),
                },
            ) from exc

        body = _serialize_rule(_rule_view(row))
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def list_ingredient_rules(
    *, ingredient_id: int, offset: int, limit: int
) -> tuple[list[IngredientRuleView], int]:
    with unit_of_work() as uow:
        session = uow.session
        require_ingredient(session, ingredient_id)
        condition = (
            IngredientRule.ingredient_id == ingredient_id,
            IngredientRule.deleted_at.is_(None),
        )
        total = session.execute(
            select(func.count()).select_from(IngredientRule).where(*condition)
        ).scalar_one()
        rows = session.execute(
            select(IngredientRule)
            .where(*condition)
            .order_by(IngredientRule.country_code)
            .offset(offset)
            .limit(limit)
        ).scalars()
        return [_rule_view(row) for row in rows], total


# ── 전성분 (제품×성분) ──────────────────────────────────────────────────────


def add_product_ingredient(
    *, actor: AuthenticatedUser, idempotency_key: str, product_id: int, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=PRODUCT_INGREDIENT_CREATE_ENDPOINT,
            key=idempotency_key,
            request_body={**payload, "product_id": product_id},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        product = require_product(session, product_id)
        ingredient = require_ingredient(session, int(payload["ingredient_id"]))
        # ★ 실패 경로에서 쓸 값은 flush **전에** 평범한 변수로 빼 둔다(함정 ② —
        #   실패한 세션에서 ORM 속성을 읽으면 새 쿼리가 나가 PendingRollbackError로
        #   원인이 뒤바뀐다. 사용자에겐 "중복입니다" 대신 500이 간다).
        ingredient_pk = ingredient.id
        inci_name = ingredient.inci_name
        ingredient_name_ko = ingredient.name_ko
        row = ProductIngredient(
            product_id=product.id,
            ingredient_id=ingredient_pk,
            concentration_pct=_decimal(payload, "concentration_pct", "함량"),
            display_order=int(payload["display_order"]),
            created_by_id=actor.id,
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError as exc:
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={
                    "ingredient_id": "이 제품의 전성분에 같은 성분이 이미 있습니다. "
                    "함량을 고치려면 기존 항목을 수정해 주세요."
                },
                log_context={"product_id": product_id, "ingredient_id": ingredient_pk},
            ) from exc

        view = ProductIngredientView(
            id=row.id,
            product_id=row.product_id,
            ingredient_id=ingredient_pk,
            inci_name=inci_name,
            ingredient_name_ko=ingredient_name_ko,
            concentration_pct=row.concentration_pct,
            display_order=row.display_order,
        )
        body = _serialize_product_ingredient(view)
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def list_product_ingredients(
    *, product_id: int, offset: int, limit: int
) -> tuple[list[ProductIngredientView], int]:
    """전성분 목록 — 표시순서대로. 성분명은 조인 한 번으로 붙인다(§18.4 N+1 금지)."""
    with unit_of_work() as uow:
        session = uow.session
        require_product(session, product_id)
        condition = (
            ProductIngredient.product_id == product_id,
            ProductIngredient.deleted_at.is_(None),
        )
        total = session.execute(
            select(func.count()).select_from(ProductIngredient).where(*condition)
        ).scalar_one()
        rows = session.execute(
            select(ProductIngredient, Ingredient.inci_name, Ingredient.name_ko)
            .join(Ingredient, ProductIngredient.ingredient_id == Ingredient.id)
            .where(*condition)
            .order_by(ProductIngredient.display_order, ProductIngredient.id)
            .offset(offset)
            .limit(limit)
        ).all()
        return [
            ProductIngredientView(
                id=row.id,
                product_id=row.product_id,
                ingredient_id=row.ingredient_id,
                inci_name=inci_name,
                ingredient_name_ko=name_ko,
                concentration_pct=row.concentration_pct,
                display_order=row.display_order,
            )
            for row, inci_name, name_ko in rows
        ], total


# ── 규칙 목록 CSV (§12.2 — S1-2 관찰 항목 "라벨·BOM·규칙 목록 CSV" 종결분) ────


@dataclass(frozen=True, slots=True)
class RuleExportView:
    """규칙 CSV 전용 뷰 — 성분은 INCI명으로 보인다(id만으로는 사람이 못 읽는다)."""

    id: int
    inci_name: str
    country_code: str
    rule_type: str
    max_concentration_pct: Decimal | None
    source_url: str
    last_verified_on: date


def all_rules_for_export() -> list[RuleExportView]:
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count())
            .select_from(IngredientRule)
            .where(IngredientRule.deleted_at.is_(None))
        ).scalar_one()
        _guard_export_size(total)
        rows = session.execute(
            select(IngredientRule, Ingredient.inci_name)
            .join(Ingredient, IngredientRule.ingredient_id == Ingredient.id)
            .where(IngredientRule.deleted_at.is_(None))
            .order_by(Ingredient.inci_name_normalized, IngredientRule.country_code)
        ).all()
        return [
            RuleExportView(
                id=row.id,
                inci_name=inci_name,
                country_code=row.country_code,
                rule_type=row.rule_type,
                max_concentration_pct=row.max_concentration_pct,
                source_url=row.source_url,
                last_verified_on=row.last_verified_on,
            )
            for row, inci_name in rows
        ]
