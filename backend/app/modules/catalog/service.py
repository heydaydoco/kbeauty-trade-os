"""제품 계층 서비스 (DESIGN.md §4.1·§4.2 / §17.4 멱등 / §18.4 페이지네이션).

목록 조회는 전부 조인 한 번으로 끝낸다 — 행마다 제품·브랜드를 다시 읽으면
§18.4가 금지하는 N+1이고, 50건짜리 첫 화면에서는 티가 안 나다가 데이터가
쌓인 뒤에 느려진다.
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
from app.modules.catalog.models import Brand, Product, Sku
from app.modules.idempotency import service as idempotency
from app.modules.identity.service import AuthenticatedUser
from app.modules.outbox import service as outbox

BRAND_CREATE_ENDPOINT = "POST /api/v1/brands"
PRODUCT_CREATE_ENDPOINT = "POST /api/v1/products"
SKU_CREATE_ENDPOINT = "POST /api/v1/skus"

#: CSV 내보내기 상한. 무제한으로 두면 한 번의 클릭이 DB와 메모리를 통째로 쓴다.
#: 넘으면 잘라 내지 않고 거절한다 — 조용히 잘린 파일은 "전부 받았다"고 믿게 만든다.
EXPORT_MAX_ROWS = 50_000


# ── 뷰 ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class BrandView:
    id: int
    brand_code: str
    name_ko: str
    name_en: str | None
    description: str | None


@dataclass(frozen=True, slots=True)
class ProductView:
    id: int
    product_code: str
    name_ko: str
    name_en: str | None
    description: str | None
    status: str
    brand_id: int
    brand_code: str
    brand_name_ko: str


@dataclass(frozen=True, slots=True)
class SkuView:
    id: int
    sku_code: str
    name_ko: str
    name_en: str | None
    status: str
    kind: str
    #: SET은 처방을 갖지 않는다(§4.2 / ADR-0016) — 아래 4개가 전부 None이다.
    product_id: int | None
    product_code: str | None
    product_name_ko: str | None
    brand_name_ko: str | None


def _brand_view(brand: Brand) -> BrandView:
    return BrandView(
        id=brand.id,
        brand_code=brand.brand_code,
        name_ko=brand.name_ko,
        name_en=brand.name_en,
        description=brand.description,
    )


def _serialize_brand(view: BrandView) -> dict[str, Any]:
    return {
        "id": view.id,
        "brand_code": view.brand_code,
        "name_ko": view.name_ko,
        "name_en": view.name_en,
        "description": view.description,
    }


def _product_view(product: Product, brand: Brand) -> ProductView:
    return ProductView(
        id=product.id,
        product_code=product.product_code,
        name_ko=product.name_ko,
        name_en=product.name_en,
        description=product.description,
        status=product.status,
        brand_id=brand.id,
        brand_code=brand.brand_code,
        brand_name_ko=brand.name_ko,
    )


def _serialize_product(view: ProductView) -> dict[str, Any]:
    return {
        "id": view.id,
        "product_code": view.product_code,
        "name_ko": view.name_ko,
        "name_en": view.name_en,
        "description": view.description,
        "status": view.status,
        "brand_id": view.brand_id,
        "brand_code": view.brand_code,
        "brand_name_ko": view.brand_name_ko,
    }


def _sku_view(
    sku: Sku,
    product_code: str | None,
    product_name_ko: str | None,
    brand_name_ko: str | None,
) -> SkuView:
    return SkuView(
        id=sku.id,
        sku_code=sku.sku_code,
        name_ko=sku.name_ko,
        name_en=sku.name_en,
        status=sku.status,
        kind=sku.kind,
        product_id=sku.product_id,
        product_code=product_code,
        product_name_ko=product_name_ko,
        brand_name_ko=brand_name_ko,
    )


def _serialize_sku(view: SkuView) -> dict[str, Any]:
    return {
        "id": view.id,
        "sku_code": view.sku_code,
        "name_ko": view.name_ko,
        "name_en": view.name_en,
        "status": view.status,
        "kind": view.kind,
        "product_id": view.product_id,
        "product_code": view.product_code,
        "product_name_ko": view.product_name_ko,
        "brand_name_ko": view.brand_name_ko,
    }


def _duplicate_code(field: str, label: str, value: Any) -> AppError:
    """부분 유니크 위반을 한국어 422로 바꾼다.

    코드에서 미리 조회해 확인하는 방식은 동시 등록에 뚫린다(§17.4) — DB의
    부분 유니크가 최종 판정자다.
    """
    return AppError(
        ErrorCode.VALIDATION_INVALID_FIELD,
        detail={field: f"이미 등록된 {label}입니다. 다른 {label}를 입력해 주세요."},
        log_context={field: value},
    )


def _guard_export_size(total: int) -> None:
    """CSV 내보내기 상한. 잘라 내지 않고 거절한다."""
    if total > EXPORT_MAX_ROWS:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={
                "size": f"내보낼 자료가 너무 많습니다({total:,}건). "
                f"{EXPORT_MAX_ROWS:,}건 이하가 되도록 조건을 좁혀 주세요."
            },
        )


# ── 브랜드 ─────────────────────────────────────────────────────────────────


def create_brand(
    *, actor: AuthenticatedUser, idempotency_key: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=BRAND_CREATE_ENDPOINT,
            key=idempotency_key,
            request_body=payload,
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        brand = Brand(
            brand_code=str(payload["brand_code"]).strip(),
            name_ko=str(payload["name_ko"]).strip(),
            name_en=payload.get("name_en"),
            description=payload.get("description"),
            created_by_id=actor.id,
        )
        session.add(brand)
        try:
            session.flush()
        except IntegrityError as exc:
            raise _duplicate_code("brand_code", "브랜드 코드", payload.get("brand_code")) from exc

        outbox.publish(
            session,
            event_type="catalog.brand.created",
            aggregate_type="brands",
            aggregate_id=brand.id,
            payload={"brand_id": brand.id, "brand_code": brand.brand_code},
        )

        body = _serialize_brand(_brand_view(brand))
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def list_brands(*, offset: int, limit: int) -> tuple[list[BrandView], int]:
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(Brand).where(Brand.deleted_at.is_(None))
        ).scalar_one()
        rows = session.execute(
            select(Brand)
            .where(Brand.deleted_at.is_(None))
            .order_by(Brand.brand_code)
            .offset(offset)
            .limit(limit)
        ).scalars()
        return [_brand_view(brand) for brand in rows], total


def all_brands_for_export() -> list[BrandView]:
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(Brand).where(Brand.deleted_at.is_(None))
        ).scalar_one()
        _guard_export_size(total)
        rows = session.execute(
            select(Brand).where(Brand.deleted_at.is_(None)).order_by(Brand.brand_code)
        ).scalars()
        return [_brand_view(brand) for brand in rows]


def get_brand(brand_id: int) -> BrandView:
    with unit_of_work() as uow:
        brand = uow.session.execute(
            select(Brand).where(Brand.id == brand_id, Brand.deleted_at.is_(None))
        ).scalar_one_or_none()
        if brand is None:
            raise NotFoundError(log_context={"brand_id": brand_id})
        return _brand_view(brand)


# ── 제품(처방) ─────────────────────────────────────────────────────────────


def _live_brand(session: Session, brand_id: int) -> Brand:
    brand = session.execute(
        select(Brand).where(Brand.id == brand_id, Brand.deleted_at.is_(None))
    ).scalar_one_or_none()
    if brand is None:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"brand_id": "존재하지 않는 브랜드입니다. 브랜드를 먼저 등록해 주세요."},
            log_context={"brand_id": brand_id},
        )
    return brand


def create_product(
    *, actor: AuthenticatedUser, idempotency_key: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=PRODUCT_CREATE_ENDPOINT,
            key=idempotency_key,
            request_body=payload,
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        brand = _live_brand(session, int(payload["brand_id"]))
        product = Product(
            brand_id=brand.id,
            product_code=str(payload["product_code"]).strip(),
            name_ko=str(payload["name_ko"]).strip(),
            name_en=payload.get("name_en"),
            description=payload.get("description"),
            status=payload.get("status") or "ACTIVE",
            created_by_id=actor.id,
        )
        session.add(product)
        try:
            session.flush()
        except IntegrityError as exc:
            raise _duplicate_code("product_code", "제품 코드", payload.get("product_code")) from exc

        outbox.publish(
            session,
            event_type="catalog.product.created",
            aggregate_type="products",
            aggregate_id=product.id,
            payload={"product_id": product.id, "product_code": product.product_code},
        )

        body = _serialize_product(_product_view(product, brand))
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def _product_select() -> Any:
    return (
        select(Product, Brand)
        .join(Brand, Product.brand_id == Brand.id)
        .where(Product.deleted_at.is_(None))
    )


def list_products(*, offset: int, limit: int) -> tuple[list[ProductView], int]:
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(Product).where(Product.deleted_at.is_(None))
        ).scalar_one()
        rows = session.execute(
            _product_select().order_by(Product.product_code).offset(offset).limit(limit)
        ).all()
        return [_product_view(product, brand) for product, brand in rows], total


def get_product(product_id: int) -> ProductView:
    with unit_of_work() as uow:
        row = uow.session.execute(_product_select().where(Product.id == product_id)).one_or_none()
        if row is None:
            raise NotFoundError(log_context={"product_id": product_id})
        product, brand = row
        return _product_view(product, brand)


def all_products_for_export() -> list[ProductView]:
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(Product).where(Product.deleted_at.is_(None))
        ).scalar_one()
        _guard_export_size(total)
        rows = session.execute(_product_select().order_by(Product.product_code)).all()
        return [_product_view(product, brand) for product, brand in rows]


# ── SKU ────────────────────────────────────────────────────────────────────


def _guard_kind_product_pairing(kind: str, product_id: int | None) -> None:
    """SKU 종류와 처방 연결의 짝 (§4.2 / ADR-0016).

    DB CHECK가 같은 규칙을 양방향으로 강제한다. 여기서 한 번 더 보는 이유는
    사용자에게 **어느 칸이** 잘못됐는지 알려 주기 위해서다 — CHECK 위반은
    화면에서 "제약 조건 위반"이라는 말밖에 못 준다.
    """
    if kind == "SINGLE" and product_id is None:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={
                "product_id": "단품 SKU에는 제품(처방)이 필요합니다. "
                "제품을 먼저 등록한 뒤 선택해 주세요."
            },
        )
    if kind == "SET" and product_id is not None:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={
                "product_id": "세트 SKU에는 제품(처방)을 지정할 수 없습니다. "
                "세트의 인증·원산지는 구성품 처방별로 관리합니다."
            },
        )


def _live_product(session: Session, product_id: int) -> Product:
    product = session.execute(
        select(Product).where(Product.id == product_id, Product.deleted_at.is_(None))
    ).scalar_one_or_none()
    if product is None:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"product_id": "존재하지 않는 제품입니다. 제품(처방)을 먼저 등록해 주세요."},
            log_context={"product_id": product_id},
        )
    return product


def create_sku(
    *, actor: AuthenticatedUser, idempotency_key: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """SKU를 등록한다. 같은 키의 재요청은 최초 결과를 그대로 돌려준다."""
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=SKU_CREATE_ENDPOINT,
            key=idempotency_key,
            request_body=payload,
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        kind = payload.get("kind") or "SINGLE"
        product_id = payload.get("product_id")
        _guard_kind_product_pairing(kind, product_id)
        # 최종 판정자는 DB의 CHECK와 FK다(ADR-0016). 여기서 보는 것은
        # 사용자에게 어느 칸이 잘못됐는지 알려 주기 위한 것이다.
        product = _live_product(session, int(product_id)) if product_id is not None else None

        sku = Sku(
            sku_code=str(payload["sku_code"]).strip(),
            name_ko=str(payload["name_ko"]).strip(),
            name_en=payload.get("name_en"),
            status=payload.get("status") or "ACTIVE",
            kind=kind,
            product_id=product.id if product is not None else None,
            created_by_id=actor.id,
        )
        session.add(sku)
        try:
            session.flush()
        except IntegrityError as exc:
            raise _duplicate_code("sku_code", "품번", payload.get("sku_code")) from exc

        outbox.publish(
            session,
            event_type="catalog.sku.created",
            aggregate_type="skus",
            aggregate_id=sku.id,
            payload={"sku_id": sku.id, "sku_code": sku.sku_code},
        )

        brand = _live_brand(session, product.brand_id) if product is not None else None
        view = _sku_view(
            sku,
            product.product_code if product is not None else None,
            product.name_ko if product is not None else None,
            brand.name_ko if brand is not None else None,
        )
        body = _serialize_sku(view)
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def _sku_select() -> Any:
    """SKU + 처방 + 브랜드를 한 번에. SET은 처방이 없으므로 outer join이다."""
    return (
        select(Sku, Product.product_code, Product.name_ko, Brand.name_ko)
        .outerjoin(Product, Sku.product_id == Product.id)
        .outerjoin(Brand, Product.brand_id == Brand.id)
        .where(Sku.deleted_at.is_(None))
    )


def list_skus(*, offset: int, limit: int) -> tuple[list[SkuView], int]:
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(Sku).where(Sku.deleted_at.is_(None))
        ).scalar_one()
        rows = session.execute(
            _sku_select().order_by(Sku.sku_code).offset(offset).limit(limit)
        ).all()
        return [_sku_view(*row) for row in rows], total


def get_sku(sku_id: int) -> SkuView:
    with unit_of_work() as uow:
        row = uow.session.execute(_sku_select().where(Sku.id == sku_id)).one_or_none()
        if row is None:
            raise NotFoundError(log_context={"sku_id": sku_id})
        return _sku_view(*row)


def all_skus_for_export() -> list[SkuView]:
    """CSV 내보내기용 전건. 상한을 넘으면 거절한다(§18.4 무제한 조회 금지 정신)."""
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(Sku).where(Sku.deleted_at.is_(None))
        ).scalar_one()
        _guard_export_size(total)
        rows = session.execute(_sku_select().order_by(Sku.sku_code)).all()
        return [_sku_view(*row) for row in rows]
