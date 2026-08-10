"""자재·BOM·라벨 서비스 (§4.4·§4.5 / §17.4 멱등 / §18.4 페이지네이션).

■ BOM 단가는 원가다 — 조회 역할에게는 **필드가 응답에 없다** (ADR-0024)

  마스킹의 판정은 여기(서비스)서 하고, 응답 모양은 스키마 두 개가 나눠 갖는다
  (router가 역할에 따라 다른 스키마를 고른다). 직렬화한 dict에서 키를 지우는
  방식은 쓰지 않는다 — 그 방식은 새 응답 경로가 생길 때마다 사람이 기억해서
  지워야 하고, 한 번 잊으면 조용히 샌다.
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
from app.core.money import minor_units
from app.modules.catalog.models import Product, Sku
from app.modules.catalog.pricing import may_see_cost
from app.modules.idempotency import service as idempotency
from app.modules.identity.service import AuthenticatedUser
from app.modules.markets import service as markets_service
from app.modules.materials.models import Label, Material, ProductBom
from app.modules.outbox import service as outbox

MATERIAL_CREATE_ENDPOINT = "POST /api/v1/materials"
BOM_CREATE_ENDPOINT = "POST /api/v1/products/{product_id}/boms"
LABEL_CREATE_ENDPOINT = "POST /api/v1/skus/{sku_id}/labels"

#: CSV 내보내기 상한 — catalog·ingredients와 같은 값·같은 이유.
EXPORT_MAX_ROWS = 50_000


# ── 뷰 ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MaterialView:
    id: int
    material_code: str
    name_ko: str
    material_type: str
    hs6: str | None
    #: 기본공급사 = 거래처(유형 SUPPLIER — ADR-0020 부기 승격). 이름은 표시용
    #: 조인 값이고, 코드는 CSV 왕복(§12.2)이 쓴다 — 이름은 유일키가 아니라
    #: 왕복 결정성이 없다.
    default_supplier_partner_id: int | None
    default_supplier_name: str | None
    default_supplier_code: str | None
    inventory_managed: bool
    lot_managed: bool
    note: str | None


@dataclass(frozen=True, slots=True)
class BomLineView:
    """BOM 라인.

    ★ `unit_cost`·`currency`는 **원가**다. 이 뷰는 서버 안에서만 쓰이고,
      응답 경계에서 역할에 따라 원가 없는 스키마로 갈린다(ADR-0024).
    """

    id: int
    product_id: int
    material_id: int
    material_code: str
    material_name_ko: str
    material_type: str
    hs6: str | None
    quantity: Decimal
    quantity_unit: str
    unit_cost: int | None
    currency: str | None
    origin_status: str
    note: str | None


@dataclass(frozen=True, slots=True)
class LabelFileView:
    """라벨에 붙은 문서 요약 (§4.7 승격 — 구 file_url의 후신).

    파일의 실체는 documents가 갖고, 라벨 응답은 표시·다운로드에 필요한
    최소만 싣는다(FILE이면 다운로드 엔드포인트, LINK면 url).
    """

    document_id: int
    storage_kind: str
    url: str | None
    original_filename: str | None


@dataclass(frozen=True, slots=True)
class LabelView:
    id: int
    sku_id: int
    sku_code: str
    country_code: str
    label_version: int
    language: str
    approval_status: str
    cut_in_date: date | None
    #: 라벨 파일(§4.5) — documents 승격분(ADR-0020 부기). 없으면 빈 튜플.
    documents: tuple[LabelFileView, ...]
    inci_local_verified: bool
    origin_mark_verified: bool
    note: str | None


def _material_view(
    row: Material, supplier_name_ko: str | None, supplier_code: str | None
) -> MaterialView:
    return MaterialView(
        id=row.id,
        material_code=row.material_code,
        name_ko=row.name_ko,
        material_type=row.material_type,
        hs6=row.hs6,
        default_supplier_partner_id=row.default_supplier_partner_id,
        default_supplier_name=supplier_name_ko,
        default_supplier_code=supplier_code,
        inventory_managed=row.inventory_managed,
        lot_managed=row.lot_managed,
        note=row.note,
    )


def _bom_view(row: ProductBom, material: Material) -> BomLineView:
    return BomLineView(
        id=row.id,
        product_id=row.product_id,
        material_id=material.id,
        material_code=material.material_code,
        material_name_ko=material.name_ko,
        material_type=material.material_type,
        hs6=material.hs6,
        quantity=row.quantity,
        quantity_unit=row.quantity_unit,
        unit_cost=row.unit_cost,
        currency=row.currency,
        origin_status=row.origin_status,
        note=row.note,
    )


def _label_view(row: Label, sku_code: str, documents: tuple[LabelFileView, ...] = ()) -> LabelView:
    return LabelView(
        id=row.id,
        sku_id=row.sku_id,
        sku_code=sku_code,
        country_code=row.country_code,
        label_version=row.label_version,
        language=row.language,
        approval_status=row.approval_status,
        cut_in_date=row.cut_in_date,
        documents=documents,
        inci_local_verified=row.inci_local_verified,
        origin_mark_verified=row.origin_mark_verified,
        note=row.note,
    )


def _documents_by_label(
    session: Session, label_ids: list[int]
) -> dict[int, tuple[LabelFileView, ...]]:
    """페이지 분량 라벨의 문서를 한 번에 가져온다 — 행마다 조회하면 N+1이다(§18.4)."""
    from app.modules.documents.models import Document

    if not label_ids:
        return {}
    rows = session.execute(
        select(Document)
        .where(
            Document.owner_type == "LABEL",
            Document.owner_id.in_(label_ids),
            Document.deleted_at.is_(None),
        )
        .order_by(Document.owner_id, Document.id)
    ).scalars()
    grouped: dict[int, list[LabelFileView]] = {}
    for doc in rows:
        grouped.setdefault(doc.owner_id, []).append(
            LabelFileView(
                document_id=doc.id,
                storage_kind=doc.storage_kind,
                url=doc.url,
                original_filename=doc.original_filename,
            )
        )
    return {label_id: tuple(views) for label_id, views in grouped.items()}


def _text(value: Decimal | None) -> str | None:
    """Decimal → JSON 문자열. float 변환 금지(§2 ADR-02)."""
    return None if value is None else str(value)


def _serialize_material(view: MaterialView) -> dict[str, Any]:
    return {
        "id": view.id,
        "material_code": view.material_code,
        "name_ko": view.name_ko,
        "material_type": view.material_type,
        "hs6": view.hs6,
        "default_supplier_partner_id": view.default_supplier_partner_id,
        "default_supplier_name": view.default_supplier_name,
        "inventory_managed": view.inventory_managed,
        "lot_managed": view.lot_managed,
        "note": view.note,
    }


def serialize_bom_line(view: BomLineView, *, include_cost: bool) -> dict[str, Any]:
    """BOM 라인 직렬화.

    ★ `include_cost=False`면 원가 키를 **넣지 않는다**(null이 아니라 부재다 —
      null은 "0원"이나 "미입력"으로 읽히고, 그 오독이 곧 원가 추정 단서가 된다).
      호출자는 라우터 하나뿐이고, 그 라우터가 역할로 값을 정한다.
    """
    body: dict[str, Any] = {
        "id": view.id,
        "product_id": view.product_id,
        "material_id": view.material_id,
        "material_code": view.material_code,
        "material_name_ko": view.material_name_ko,
        "material_type": view.material_type,
        "hs6": view.hs6,
        "quantity": _text(view.quantity),
        "quantity_unit": view.quantity_unit,
        "origin_status": view.origin_status,
        "note": view.note,
    }
    if include_cost:
        body["unit_cost"] = view.unit_cost
        body["currency"] = view.currency
    return body


def _serialize_label(view: LabelView) -> dict[str, Any]:
    return {
        "id": view.id,
        "sku_id": view.sku_id,
        "sku_code": view.sku_code,
        "country_code": view.country_code,
        "label_version": view.label_version,
        "language": view.language,
        "approval_status": view.approval_status,
        "cut_in_date": None if view.cut_in_date is None else view.cut_in_date.isoformat(),
        "documents": [
            {
                "document_id": doc.document_id,
                "storage_kind": doc.storage_kind,
                "url": doc.url,
                "original_filename": doc.original_filename,
            }
            for doc in view.documents
        ],
        "inci_local_verified": view.inci_local_verified,
        "origin_mark_verified": view.origin_mark_verified,
        "note": view.note,
    }


# ── 공통 ───────────────────────────────────────────────────────────────────


def _decimal(payload: dict[str, Any], field: str, label: str) -> Decimal | None:
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


def _minor_amount(payload: dict[str, Any]) -> tuple[int | None, str | None]:
    """사람이 쓰는 표기(12.34)를 정수 최소단위로 (§2 ADR-02).

    ★ 실패 메시지에 금액을 넣지 않는다 — 이 값은 원가다(§18.1 로그 마스킹).
    """
    raw = payload.get("unit_cost")
    currency = payload.get("currency")
    if raw is None and currency is None:
        return None, None
    if raw is None or currency is None:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"unit_cost": "단가와 통화는 함께 입력하거나 함께 비워 주세요."},
        )
    code = str(currency).strip().upper()
    try:
        exponent = minor_units(code)
    except Exception as exc:  # UnknownCurrencyError — 통화 목록은 money.py가 유일 출처
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"currency": f"등록되지 않은 통화입니다: {code}"},
            log_context={"currency": code},
        ) from exc
    try:
        amount = Decimal(str(raw))
    except InvalidOperation as exc:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"unit_cost": "단가는 숫자여야 합니다."},
        ) from exc
    scaled = amount.scaleb(exponent)
    if scaled != scaled.to_integral_value():
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={
                "unit_cost": f"{code}는 소수점 {exponent}자리까지 쓸 수 있습니다. "
                "자릿수를 확인해 주세요."
            },
        )
    return int(scaled), code


def require_material(session: Session, material_id: int) -> Material:
    row = session.execute(
        select(Material).where(Material.id == material_id, Material.deleted_at.is_(None))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(log_context={"material_id": material_id})
    return row


def require_product(session: Session, product_id: int) -> Product:
    row = session.execute(
        select(Product).where(Product.id == product_id, Product.deleted_at.is_(None))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(log_context={"product_id": product_id})
    return row


def require_sku(session: Session, sku_id: int) -> Sku:
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


# ── 자재 마스터 (§4.4) ─────────────────────────────────────────────────────


def _live_supplier(session: Session, partner_id: int) -> Any:
    """기본공급사 거래처 검증 — 존재해야 하고 SUPPLIER 유형이어야 한다.

    [M1] 보강(S1-3) ⑤가 기본공급사=SUPPLIER로 못박는다(catalog._live_manufacturer의
    OEM 검증과 같은 꼴).
    """
    from app.modules.partners.models import Partner, PartnerTypeLink

    partner = session.execute(
        select(Partner).where(Partner.id == partner_id, Partner.deleted_at.is_(None))
    ).scalar_one_or_none()
    if partner is None:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={
                "default_supplier_partner_id": "존재하지 않는 거래처입니다. "
                "거래처를 먼저 등록해 주세요."
            },
            log_context={"default_supplier_partner_id": partner_id},
        )
    has_type = session.execute(
        select(PartnerTypeLink.id).where(
            PartnerTypeLink.partner_id == partner_id,
            PartnerTypeLink.type_code == "SUPPLIER",
            PartnerTypeLink.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if has_type is None:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={
                "default_supplier_partner_id": "공급사 유형이 아닌 거래처입니다. "
                "기본공급사는 SUPPLIER 유형을 가진 거래처만 지정할 수 있습니다."
            },
            log_context={"default_supplier_partner_id": partner_id},
        )
    return partner


def create_material(
    *, actor: AuthenticatedUser, idempotency_key: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=MATERIAL_CREATE_ENDPOINT,
            key=idempotency_key,
            request_body=payload,
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        inventory_managed = bool(payload.get("inventory_managed"))
        lot_managed = bool(payload.get("lot_managed"))
        # 최종 판정자는 DB CHECK다 — 여기서 보는 것은 어느 칸이 왜 잘못됐는지
        # 알려 주기 위한 것이다.
        if lot_managed and not inventory_managed:
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={
                    "lot_managed": "로트관리는 재고관리를 전제합니다. 재고관리를 함께 "
                    "켜거나 로트관리를 꺼 주세요."
                },
            )
        supplier_id = payload.get("default_supplier_partner_id")
        supplier = _live_supplier(session, int(supplier_id)) if supplier_id is not None else None

        material = Material(
            material_code=str(payload["material_code"]).strip(),
            name_ko=str(payload["name_ko"]).strip(),
            material_type=str(payload["material_type"]),
            hs6=payload.get("hs6"),
            default_supplier_partner_id=supplier.id if supplier is not None else None,
            inventory_managed=inventory_managed,
            lot_managed=lot_managed,
            note=payload.get("note"),
            created_by_id=actor.id,
        )
        session.add(material)
        try:
            session.flush()
        except IntegrityError as exc:
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={"material_code": "이미 등록된 자재 코드입니다. 다른 코드를 입력해 주세요."},
                log_context={"material_code": payload.get("material_code")},
            ) from exc

        outbox.publish(
            session,
            event_type="materials.material.created",
            aggregate_type="materials",
            aggregate_id=material.id,
            payload={"material_id": material.id, "material_code": material.material_code},
        )

        body = _serialize_material(
            _material_view(
                material,
                supplier.name_ko if supplier is not None else None,
                supplier.partner_code if supplier is not None else None,
            )
        )
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def _material_select() -> Any:
    """자재 + 기본공급사(거래처)를 한 번에. 없을 수 있어 outer join이다."""
    from app.modules.partners.models import Partner

    return (
        select(Material, Partner.name_ko, Partner.partner_code)
        .outerjoin(Partner, Material.default_supplier_partner_id == Partner.id)
        .where(Material.deleted_at.is_(None))
    )


def list_materials(*, offset: int, limit: int) -> tuple[list[MaterialView], int]:
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(Material).where(Material.deleted_at.is_(None))
        ).scalar_one()
        rows = session.execute(
            _material_select().order_by(Material.material_code).offset(offset).limit(limit)
        ).all()
        return [
            _material_view(row, supplier_name, supplier_code)
            for row, supplier_name, supplier_code in rows
        ], total


def all_materials_for_export() -> list[MaterialView]:
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(Material).where(Material.deleted_at.is_(None))
        ).scalar_one()
        _guard_export_size(total)
        rows = session.execute(_material_select().order_by(Material.material_code)).all()
        return [
            _material_view(row, supplier_name, supplier_code)
            for row, supplier_name, supplier_code in rows
        ]


def get_material(material_id: int) -> MaterialView:
    with unit_of_work() as uow:
        session = uow.session
        row = session.execute(_material_select().where(Material.id == material_id)).one_or_none()
        if row is None:
            raise NotFoundError(log_context={"material_id": material_id})
        material, supplier_name, supplier_code = row
        return _material_view(material, supplier_name, supplier_code)


# ── 자재 BOM (§4.4 / GC-B1·B2) ─────────────────────────────────────────────


def add_bom_line(
    *, actor: AuthenticatedUser, idempotency_key: str, product_id: int, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """BOM 라인을 추가한다. 응답의 원가 포함 여부는 라우터가 역할로 정한다."""
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=BOM_CREATE_ENDPOINT,
            key=idempotency_key,
            request_body={**payload, "product_id": product_id},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        product = require_product(session, product_id)
        material = require_material(session, int(payload["material_id"]))
        unit_cost, currency = _minor_amount(payload)
        # 실패 경로에서 쓸 값은 flush 전에 빼 둔다(함정 ②).
        material_pk = material.id
        material_code = material.material_code
        material_name_ko = material.name_ko
        material_type = material.material_type
        material_hs6 = material.hs6

        row = ProductBom(
            product_id=product.id,
            material_id=material_pk,
            quantity=_decimal(payload, "quantity", "소요량"),
            quantity_unit=str(payload["quantity_unit"]).strip(),
            unit_cost=unit_cost,
            currency=currency,
            origin_status=payload.get("origin_status") or "UNKNOWN",
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
                    "material_id": "이 제품의 BOM에 같은 자재가 이미 있습니다. "
                    "소요량을 고치려면 기존 항목을 수정해 주세요."
                },
                log_context={"product_id": product_id, "material_id": material_pk},
            ) from exc

        view = BomLineView(
            id=row.id,
            product_id=row.product_id,
            material_id=material_pk,
            material_code=material_code,
            material_name_ko=material_name_ko,
            material_type=material_type,
            hs6=material_hs6,
            quantity=row.quantity,
            quantity_unit=row.quantity_unit,
            unit_cost=row.unit_cost,
            currency=row.currency,
            origin_status=row.origin_status,
            note=row.note,
        )
        # ★ 멱등 재생 본문도 등록자(원가를 볼 수 있는 역할)의 것이다 — 등록은
        #   TRADE·ADMIN만 할 수 있으므로(라우터 가드) 여기 저장되는 본문은
        #   언제나 원가 포함이고, 조회 역할은 이 엔드포인트에 닿지 못한다.
        body = serialize_bom_line(view, include_cost=True)
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def list_bom_lines(*, product_id: int, offset: int, limit: int) -> tuple[list[BomLineView], int]:
    """BOM 목록. 원가 마스킹은 응답 경계(라우터·스키마)의 일이다.

    ★ 행을 감추지 않는다 — 소요량·원산지상태는 인증·판정 업무에 필요하고,
      행 단위로 지우면 "BOM이 비어 있다"로 읽힌다(ADR-0024 대 ADR-0018).
    """
    with unit_of_work() as uow:
        session = uow.session
        require_product(session, product_id)
        condition = (ProductBom.product_id == product_id, ProductBom.deleted_at.is_(None))
        total = session.execute(
            select(func.count()).select_from(ProductBom).where(*condition)
        ).scalar_one()
        rows = session.execute(
            select(ProductBom, Material)
            .join(Material, ProductBom.material_id == Material.id)
            .where(*condition)
            .order_by(Material.material_code, ProductBom.id)
            .offset(offset)
            .limit(limit)
        ).all()
        return [_bom_view(row, material) for row, material in rows], total


def may_see_bom_cost(actor: AuthenticatedUser) -> bool:
    """BOM 단가를 볼 수 있는가.

    판정은 sku_prices의 원가와 **같은 역할 목록**을 쓴다(pricing.may_see_cost를
    그대로 부른다) — 두 곳이 갈리면 한쪽에서 막은 원가가 다른 쪽으로 샌다.
    """
    return may_see_cost(actor)


# ── 라벨 (§4.5) ────────────────────────────────────────────────────────────


def create_label(
    *, actor: AuthenticatedUser, idempotency_key: str, sku_id: int, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=LABEL_CREATE_ENDPOINT,
            key=idempotency_key,
            request_body={**payload, "sku_id": sku_id},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        sku = require_sku(session, sku_id)
        sku_code = sku.sku_code
        cut_in = payload.get("cut_in_date")
        # 시장 선등록 가드(S2-1 판정 조건 6) — FK는 안전망, 안내는 여기서 나간다.
        country_code = str(payload["country_code"]).strip().upper()
        markets_service.require_active_market_code(session, country_code)
        row = Label(
            sku_id=sku.id,
            country_code=country_code,
            label_version=int(payload["label_version"]),
            language=str(payload["language"]).strip(),
            approval_status=payload.get("approval_status") or "DRAFT",
            cut_in_date=None if cut_in is None else date.fromisoformat(str(cut_in)),
            # 라벨 파일은 documents 승격 완료(ADR-0020 부기) — 라벨 행에는 없다.
            inci_local_verified=bool(payload.get("inci_local_verified")),
            origin_mark_verified=bool(payload.get("origin_mark_verified")),
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
                    "label_version": "이 SKU에는 같은 시장·언어·판번의 라벨이 이미 "
                    "있습니다. 새 판이면 판번을 올려 주세요."
                },
                log_context={
                    "sku_id": sku_id,
                    "country_code": payload.get("country_code"),
                    "label_version": payload.get("label_version"),
                },
            ) from exc

        body = _serialize_label(_label_view(row, sku_code))
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def list_sku_labels(*, sku_id: int, offset: int, limit: int) -> tuple[list[LabelView], int]:
    with unit_of_work() as uow:
        session = uow.session
        sku = require_sku(session, sku_id)
        condition = (Label.sku_id == sku_id, Label.deleted_at.is_(None))
        total = session.execute(
            select(func.count()).select_from(Label).where(*condition)
        ).scalar_one()
        rows = list(
            session.execute(
                select(Label)
                .where(*condition)
                .order_by(Label.country_code, Label.label_version.desc())
                .offset(offset)
                .limit(limit)
            ).scalars()
        )
        docs = _documents_by_label(session, [row.id for row in rows])
        return [_label_view(row, sku.sku_code, docs.get(row.id, ())) for row in rows], total


def list_product_labels(*, product_id: int, offset: int, limit: int) -> tuple[list[LabelView], int]:
    """제품(처방)에 딸린 SKU들의 라벨 롤업 — **읽기 전용**이다 (§14 ③).

    라벨의 실체는 SKU×시장이라(§4.5) 등록·수정은 SKU 상세에서 한다. 여기서는
    "이 처방의 라벨이 어디까지 됐나"를 한눈에 보기 위한 조회만 제공한다.
    세트 SKU는 처방을 갖지 않으므로(ADR-0016) 이 롤업에 나타나지 않는다.
    """
    with unit_of_work() as uow:
        session = uow.session
        require_product(session, product_id)
        condition = (
            Sku.product_id == product_id,
            Sku.deleted_at.is_(None),
            Label.deleted_at.is_(None),
        )
        total = session.execute(
            select(func.count())
            .select_from(Label)
            .join(Sku, Label.sku_id == Sku.id)
            .where(*condition)
        ).scalar_one()
        rows = session.execute(
            select(Label, Sku.sku_code)
            .join(Sku, Label.sku_id == Sku.id)
            .where(*condition)
            .order_by(Sku.sku_code, Label.country_code, Label.label_version.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        docs = _documents_by_label(session, [row.id for row, _ in rows])
        return [_label_view(row, sku_code, docs.get(row.id, ())) for row, sku_code in rows], total


# ── 목록 CSV (§12.2 엑셀 코어 — S1-2 관찰 항목 "라벨·BOM·규칙 목록 CSV" 종결분) ──


@dataclass(frozen=True, slots=True)
class BomLineExportView:
    """BOM CSV 전용 뷰 — **원가 필드 자체가 없다** (웹 세션 승인 문면 "BOM=원가
    열 미포함"). 파일은 역할 분기 없이 한 모양이므로, 원가가 담긴 뷰를 들고
    와서 열만 빼는 방식 대신 애초에 담기지 않는 뷰를 쓴다(ADR-0024의 부재 규율).
    """

    id: int
    product_code: str
    material_code: str
    material_name_ko: str
    quantity: Decimal
    quantity_unit: str
    origin_status: str


def all_labels_for_export() -> list[LabelView]:
    """전 SKU 라벨 — 파일 열은 documents 몫이라 CSV에는 싣지 않는다."""
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(Label).where(Label.deleted_at.is_(None))
        ).scalar_one()
        _guard_export_size(total)
        rows = session.execute(
            select(Label, Sku.sku_code)
            .join(Sku, Label.sku_id == Sku.id)
            .where(Label.deleted_at.is_(None))
            .order_by(Sku.sku_code, Label.country_code, Label.language, Label.label_version)
        ).all()
        return [_label_view(row, sku_code) for row, sku_code in rows]


def all_bom_lines_for_export() -> list[BomLineExportView]:
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(ProductBom).where(ProductBom.deleted_at.is_(None))
        ).scalar_one()
        _guard_export_size(total)
        rows = session.execute(
            select(ProductBom, Material, Product.product_code)
            .join(Material, ProductBom.material_id == Material.id)
            .join(Product, ProductBom.product_id == Product.id)
            .where(ProductBom.deleted_at.is_(None))
            .order_by(Product.product_code, Material.material_code, ProductBom.id)
        ).all()
        return [
            BomLineExportView(
                id=row.id,
                product_code=product_code,
                material_code=material.material_code,
                material_name_ko=material.name_ko,
                quantity=row.quantity,
                quantity_unit=row.quantity_unit,
                origin_status=row.origin_status,
            )
            for row, material, product_code in rows
        ]
