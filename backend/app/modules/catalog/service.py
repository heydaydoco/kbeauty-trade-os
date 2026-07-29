"""제품 계층 서비스 (DESIGN.md §4.1·§4.2 / §17.4 멱등 / §18.4 페이지네이션).

목록 조회는 전부 조인 한 번으로 끝낸다 — 행마다 제품·브랜드를 다시 읽으면
§18.4가 금지하는 N+1이고, 50건짜리 첫 화면에서는 티가 안 나다가 데이터가
쌓인 뒤에 느려진다.
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
from app.modules.catalog.models import Brand, Product, Sku, SkuHsCode
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
    # 물류 (§4.1)
    barcode: str | None
    unit_weight_g: Decimal | None
    box_qty: int | None
    shelf_life_months: int | None
    manufacturer_name: str | None
    # DG (§4.1 / §7.7)
    dg_flag: bool
    un_number: str | None
    dg_class: str | None
    packing_group: str | None
    flash_point_c: Decimal | None
    alcohol_content_pct: Decimal | None
    is_aerosol: bool
    is_limited_quantity: bool
    msds_url: str | None


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
        barcode=sku.barcode,
        unit_weight_g=sku.unit_weight_g,
        box_qty=sku.box_qty,
        shelf_life_months=sku.shelf_life_months,
        manufacturer_name=sku.manufacturer_name,
        dg_flag=sku.dg_flag,
        un_number=sku.un_number,
        dg_class=sku.dg_class,
        packing_group=sku.packing_group,
        flash_point_c=sku.flash_point_c,
        alcohol_content_pct=sku.alcohol_content_pct,
        is_aerosol=sku.is_aerosol,
        is_limited_quantity=sku.is_limited_quantity,
        msds_url=sku.msds_url,
    )


def _text(value: Decimal | None) -> str | None:
    """Decimal을 JSON에 담을 수 있는 문자열로.

    ★ float으로 바꾸지 않는다. §2 ADR-02가 Float을 금지하는 이유가 여기서도
      그대로 적용된다 — 멱등 재요청이 돌려주는 "최초 결과"가 원본과 미세하게
      다른 값이 되면 그 차이는 아무도 못 찾는다.
    """
    return None if value is None else str(value)


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
        "barcode": view.barcode,
        "unit_weight_g": _text(view.unit_weight_g),
        "box_qty": view.box_qty,
        "shelf_life_months": view.shelf_life_months,
        "manufacturer_name": view.manufacturer_name,
        "dg_flag": view.dg_flag,
        "un_number": view.un_number,
        "dg_class": view.dg_class,
        "packing_group": view.packing_group,
        "flash_point_c": _text(view.flash_point_c),
        "alcohol_content_pct": _text(view.alcohol_content_pct),
        "is_aerosol": view.is_aerosol,
        "is_limited_quantity": view.is_limited_quantity,
        "msds_url": view.msds_url,
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


#: 세트가 가질 수 없는 DG 속성 (ADR-0016 부기 — P4까지 미결이라 입력을 막는다).
_DG_FIELDS = (
    ("un_number", "UN번호"),
    ("dg_class", "Class"),
    ("packing_group", "포장등급"),
    ("flash_point_c", "인화점"),
    ("alcohol_content_pct", "알코올 함량"),
    ("is_aerosol", "에어로졸"),
    ("is_limited_quantity", "LQ"),
    ("msds_url", "MSDS 링크"),
)

#: DG 분류가 있어야만 의미가 생기는 값들. 인화점·알코올함량·에어로졸·MSDS는
#: 여기 없다 — 그건 "왜 DG가 아닌지"의 근거값이라 비DG 행에도 있어야 한다.
_DG_CLASSIFICATION_FIELDS = (
    ("un_number", "UN번호"),
    ("dg_class", "Class"),
    ("packing_group", "포장등급"),
    ("is_limited_quantity", "LQ"),
)


def _present(payload: dict[str, Any], field: str) -> bool:
    value = payload.get(field)
    return value is not None and value is not False


def _guard_dangerous_goods(payload: dict[str, Any], kind: str) -> None:
    """DG 속성의 두 방향 잠금 (§4.1·§7.7 / ADR-0016 정정).

    DB CHECK가 최종 판정자다. 여기서 한 번 더 보는 것은 어느 칸이 문제인지
    한국어로 알려 주기 위해서다.

    ★ dg_flag=true 방향(UN번호 필수 등)과 교차 정합(에어로졸↔DG)은 **여기서
      막지 않는다**. 등록 시점에 DG 정보가 다 모여 있지 않은 것이 실무의 정상
      상태이고, 차단해야 할 지점은 등록이 아니라 선적이다(§7.7 DG 게이트, P4).
    """
    if kind == "SET":
        offenders = [label for field, label in _DG_FIELDS if _present(payload, field)]
        if payload.get("dg_flag"):
            offenders.insert(0, "위험물 여부")
        if offenders:
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={
                    "dg_flag": f"세트 SKU에는 위험물 정보를 입력할 수 없습니다"
                    f"({', '.join(offenders)}). 위험물 판정은 구성품 단위로 합니다."
                },
            )
        return

    if not payload.get("dg_flag"):
        offenders = [
            label for field, label in _DG_CLASSIFICATION_FIELDS if _present(payload, field)
        ]
        if offenders:
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={
                    "dg_flag": f"위험물이 아닌 SKU에는 {', '.join(offenders)}을(를) "
                    "입력할 수 없습니다. 위험물이라면 '위험물 여부'를 먼저 켜 주세요."
                },
            )


def _decimal(payload: dict[str, Any], field: str, label: str) -> Decimal | None:
    """JSON 문자열로 온 소수를 Decimal로. float은 거치지 않는다(§2 ADR-02)."""
    value = payload.get(field)
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={field: f"{label}은(는) 숫자여야 합니다."},
            log_context={field: value},
        ) from exc


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
        _guard_dangerous_goods(payload, kind)
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
            barcode=payload.get("barcode"),
            unit_weight_g=_decimal(payload, "unit_weight_g", "중량"),
            box_qty=payload.get("box_qty"),
            shelf_life_months=payload.get("shelf_life_months"),
            manufacturer_name=payload.get("manufacturer_name"),
            dg_flag=bool(payload.get("dg_flag")),
            un_number=payload.get("un_number"),
            dg_class=payload.get("dg_class"),
            packing_group=payload.get("packing_group"),
            flash_point_c=_decimal(payload, "flash_point_c", "인화점"),
            alcohol_content_pct=_decimal(payload, "alcohol_content_pct", "알코올 함량"),
            is_aerosol=bool(payload.get("is_aerosol")),
            is_limited_quantity=bool(payload.get("is_limited_quantity")),
            msds_url=payload.get("msds_url"),
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


# ── 국가별 HS 세번 (§4.1 / ADR-03 / ADR-0019) ──────────────────────────────


HS_CODE_CREATE_ENDPOINT = "POST /api/v1/skus/{sku_id}/hs-codes"


@dataclass(frozen=True, slots=True)
class SkuHsCodeView:
    id: int
    sku_id: int
    country_code: str
    hs_version: str
    hs_code: str
    tariff_note: str | None
    source_url: str
    last_verified_on: date


def _hs_view(row: SkuHsCode) -> SkuHsCodeView:
    return SkuHsCodeView(
        id=row.id,
        sku_id=row.sku_id,
        country_code=row.country_code,
        hs_version=row.hs_version,
        hs_code=row.hs_code,
        tariff_note=row.tariff_note,
        source_url=row.source_url,
        last_verified_on=row.last_verified_on,
    )


def _serialize_hs(view: SkuHsCodeView) -> dict[str, Any]:
    return {
        "id": view.id,
        "sku_id": view.sku_id,
        "country_code": view.country_code,
        "hs_version": view.hs_version,
        "hs_code": view.hs_code,
        "tariff_note": view.tariff_note,
        "source_url": view.source_url,
        "last_verified_on": view.last_verified_on.isoformat(),
    }


def _normalized_hs_code(raw: str) -> str:
    """점·공백을 지우고 숫자만 남긴다.

    ★ 국가마다 "3304.99.00"·"3304 99 0000"·"3304990000"으로 표기가 갈린다.
      표기 그대로 저장하면 같은 세번이 다른 행으로 들어와 유일키가 중복을
      못 잡고, 나중에 비교하는 쪽이 매번 정규화를 다시 짜게 된다.
    """
    digits = "".join(char for char in raw if char.isdigit())
    if not 6 <= len(digits) <= 12:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={
                "hs_code": "HS 세번은 숫자 6~12자리입니다(점·공백은 지워도 됩니다). "
                f"입력하신 값에서 숫자 {len(digits)}자리를 찾았습니다."
            },
            log_context={"hs_code": raw},
        )
    return digits


def _guard_verified_on(value: date) -> None:
    """최종확인일은 미래일 수 없다 (ADR-03 — 확인은 이미 한 일이다).

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


def _live_sku(session: Session, sku_id: int) -> Sku:
    sku = session.execute(
        select(Sku).where(Sku.id == sku_id, Sku.deleted_at.is_(None))
    ).scalar_one_or_none()
    if sku is None:
        raise NotFoundError(log_context={"sku_id": sku_id})
    return sku


def create_sku_hs_code(
    *, actor: AuthenticatedUser, idempotency_key: str, sku_id: int, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """SKU에 국가별 HS 세번을 등록한다.

    ★ 판정이 아니라 **기록**이다. 시스템은 HS를 추천하지도 계산하지도 않는다
      (§1 비범위) — 사람이 확인한 값과 그 근거를 받아 적을 뿐이다.
    """
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=HS_CODE_CREATE_ENDPOINT,
            key=idempotency_key,
            request_body={**payload, "sku_id": sku_id},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        sku = _live_sku(session, sku_id)
        # 스키마가 date로 검증했지만 model_dump(mode="json")을 거치며 ISO 문자열이
        # 됐다. 다른 엔드포인트와 같은 직렬화 경로를 쓰기 위한 값이라 여기서 되돌린다.
        verified_on = date.fromisoformat(str(payload["last_verified_on"]))
        _guard_verified_on(verified_on)
        row = SkuHsCode(
            sku_id=sku.id,
            country_code=str(payload["country_code"]).strip().upper(),
            hs_version=str(payload["hs_version"]).strip().upper(),
            hs_code=_normalized_hs_code(str(payload["hs_code"])),
            tariff_note=payload.get("tariff_note"),
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
                    "country_code": "이 SKU에는 같은 국가·HS 버전의 세번이 이미 등록돼 "
                    "있습니다. 기존 항목을 확인해 주세요."
                },
                log_context={
                    "sku_id": sku_id,
                    "country_code": payload.get("country_code"),
                    "hs_version": payload.get("hs_version"),
                },
            ) from exc

        body = _serialize_hs(_hs_view(row))
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def list_sku_hs_codes(*, sku_id: int, offset: int, limit: int) -> tuple[list[SkuHsCodeView], int]:
    with unit_of_work() as uow:
        session = uow.session
        _live_sku(session, sku_id)
        condition = (SkuHsCode.sku_id == sku_id, SkuHsCode.deleted_at.is_(None))
        total = session.execute(
            select(func.count()).select_from(SkuHsCode).where(*condition)
        ).scalar_one()
        rows = session.execute(
            select(SkuHsCode)
            .where(*condition)
            .order_by(SkuHsCode.country_code, SkuHsCode.hs_version)
            .offset(offset)
            .limit(limit)
        ).scalars()
        return [_hs_view(row) for row in rows], total
