"""선적 서비스(L1) — 단건 조회·생성 착지·DB 제약 번역 (S3-2 PR-3a / ADR-0074 / design-integrated §2.10).

L1: 이 모듈은 **전이·참조 생성·SO 수렴을 하지 않는다**(trade_chain L2 `shipment_flow`가 오케스트레이션한다). 여기 있는 것은
(1) 경로 id로 선적·라인·당사자를 찾는 단건 조회(부모-자식 불일치 = 404), (2) 참조 생성 오케스트레이터가 부르는 착지 `insert_planned`
(채번 → INSERT → 탄생 이력·`.created` 이벤트 → 라인·당사자 INSERT, 한 트랜잭션), (3) DB 제약 → 업무 오류 번역표다.

■ 채번은 **생성 트랜잭션의 마지막 잠금**이다(LOCK_ORDER doc_number_seq) — 모든 검증·라인 구성 뒤에 발급하고, 그 뒤에는 새 행 INSERT와
  이미 잠근 SO의 수렴 전이뿐이다(새 잠금 획득 없음).
■ 전표 생성·전이는 audit_log에 이중 기록하지 않는다(상태이력+아웃박스가 정본 — X-24). 당사자 추가·삭제만 audit(§2.7).
■ SO·PO 모델을 임포트하지 않는다(L1→다른 L1 금지) — 원천 값은 오케스트레이터가 계획(dict)으로 넘긴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, NotFoundError
from app.core.time import utcnow
from app.modules.shipments.models import Shipment, ShipmentLine, ShipmentParty
from app.modules.trade_docs import editing
from app.modules.trade_docs.constants import DocKind
from app.modules.trade_docs.doc_number import issue_document_number
from app.modules.trade_docs.snapshot import line_amount
from app.modules.trade_docs.transition import record_birth

KIND = DocKind.SHIPMENT

#: DB 제약 → 업무 오류(경합·결함의 마지막 방어선 — 서비스 선검증을 빠져나간 동시 요청이 500으로 새지 않게, J-09).
#: 아래에 없는 제약 위반은 그대로 전파한다(내부 결함 = 500, 조용히 삼키지 않는다). 시험이 실제 제약·유니크 인덱스와 대사한다.
CONSTRAINT_ERRORS: dict[str, ErrorCode] = {
    "uq_shipment_lines_shipment_id_so_line_id_active": ErrorCode.SHIPMENTS_LINE_DUPLICATE_SOURCE,
    "uq_shipment_lines_shipment_id_po_line_id_active": ErrorCode.SHIPMENTS_LINE_DUPLICATE_SOURCE,
    "uq_shipment_parties_shipment_id_role_active": ErrorCode.SHIPMENTS_PARTY_ROLE_DUPLICATE,
    # 라인 번호는 헤더 FOR UPDATE 하의 카운터라 겹칠 수 없다 — 결함·경합이면 500이 아니라 재시도 안내(409).
    "uq_shipment_lines_shipment_id_line_no_active": ErrorCode.CONCURRENCY_VERSION_CONFLICT,
    # 동시 출고지시·취소 등 헤더 경합은 FOR UPDATE + version이 먼저 막는다. 채번 유니크는 시퀀스 행 잠금이 보증(발생 시 409 — 500 금지).
    "uq_shipments_doc_number": ErrorCode.CONCURRENCY_VERSION_CONFLICT,
}


def constraint_name(exc: IntegrityError) -> str | None:
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)


def flush_translated(session: Session) -> None:
    """flush하고 번역표의 제약 위반을 업무 오류로 바꾼다(그 밖의 위반은 그대로 전파 — 500). 업무 오류는 TX 전체를 롤백시킨다(멱등 claim 포함)."""
    try:
        session.flush()
    except IntegrityError as exc:
        name = constraint_name(exc)
        code = CONSTRAINT_ERRORS.get(name or "")
        if code is None:
            raise
        raise AppError(code, log_context={"constraint": name}) from None


# ── 단건 조회 (부모-자식 = 경로 404) ───────────────────────────────────────────


def require_shipment(session: Session, shipment_id: int) -> Shipment:
    row = session.execute(
        select(Shipment).where(Shipment.id == shipment_id, Shipment.deleted_at.is_(None))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(log_context={"shipment_id": shipment_id})
    return row


def require_line(session: Session, shipment_id: int, line_id: int) -> ShipmentLine:
    """경로의 라인이 경로의 선적 소속이 아니면(삭제 포함) 404 — 부작용 0(`D:370` 부모-자식)."""
    row = session.execute(
        select(ShipmentLine).where(
            ShipmentLine.id == line_id,
            ShipmentLine.shipment_id == shipment_id,
            ShipmentLine.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(log_context={"shipment_id": shipment_id, "line_id": line_id})
    return row


def require_party(session: Session, shipment_id: int, party_id: int) -> ShipmentParty:
    row = session.execute(
        select(ShipmentParty)
        .where(
            ShipmentParty.id == party_id,
            ShipmentParty.shipment_id == shipment_id,
            ShipmentParty.deleted_at.is_(None),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(log_context={"shipment_id": shipment_id, "party_id": party_id})
    return row


def live_lines(session: Session, shipment_id: int) -> list[ShipmentLine]:
    return list(
        session.execute(
            select(ShipmentLine)
            .where(ShipmentLine.shipment_id == shipment_id, ShipmentLine.deleted_at.is_(None))
            .order_by(ShipmentLine.line_no)
        ).scalars()
    )


# ── 생성 착지 ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class NewShipmentLine:
    """생성 TX가 넣을 선적 라인 — 원천 라인 id·SKU 사본·단가 사본(수입은 None)·수량."""

    so_line_id: int | None
    po_line_id: int | None
    sku_id: int
    sku_code: str
    sku_name_ko: str
    sku_name_en: str | None
    sku_kind: str
    unit_price_amount: int | None
    is_free: bool
    quantity: int

    @property
    def amount(self) -> int:
        return (
            0
            if self.unit_price_amount is None
            else line_amount(self.quantity, self.unit_price_amount)
        )


@dataclass(frozen=True, slots=True)
class NewParty:
    role: str
    partner_id: int
    name_en: str
    address_en: str | None
    is_auto: bool


def insert_planned(
    session: Session,
    *,
    actor_id: int,
    header: dict[str, Any],
    lines: list[NewShipmentLine],
    parties: list[NewParty],
) -> Shipment:
    """선적 1건 INSERT(계획 PLANNED로 탄생) — 채번은 마지막 잠금이고, 합계는 라인 금액 합을 미리 구해 넣는다(수입은 0)."""
    editing.require_line_capacity(0, len(lines))
    total = editing.compute_total([item.amount for item in lines])
    number = issue_document_number(session, KIND)  # 잠금 순서 doc_number_seq — 항상 마지막
    row = Shipment(
        doc_number=number,
        total_amount=total,
        last_line_no=len(lines),
        created_by_id=actor_id,
        updated_by_id=actor_id,
        **header,
    )
    record_birth(
        session, row, actor_user_id=actor_id
    )  # status 대입·INSERT·탄생 이력·created 이벤트
    for line_no, item in enumerate(lines, start=1):
        session.add(
            ShipmentLine(
                shipment_id=row.id,
                line_no=line_no,
                so_line_id=item.so_line_id,
                po_line_id=item.po_line_id,
                sku_id=item.sku_id,
                sku_code=item.sku_code,
                sku_name_ko=item.sku_name_ko,
                sku_name_en=item.sku_name_en,
                sku_kind=item.sku_kind,
                currency=row.currency,
                quantity=item.quantity,
                unit_price_amount=item.unit_price_amount,
                is_free=item.is_free,
                line_amount=item.amount,
                created_by_id=actor_id,
                updated_by_id=actor_id,
            )
        )
    for party in parties:
        session.add(
            ShipmentParty(
                shipment_id=row.id,
                role=party.role,
                partner_id=party.partner_id,
                name_en=party.name_en,
                address_en=party.address_en,
                is_auto=party.is_auto,
                created_by_id=actor_id,
                updated_by_id=actor_id,
            )
        )
    flush_translated(session)
    return row


def require_english_name(name_en: str | None, *, field: str) -> str:
    """거래처 영문명 — 비었거나 제어문자(탭·줄바꿈 등)가 있으면 422 ENGLISH_NAME_MISSING(서류 영문 원천 결측 — fail-visible)."""
    text = (name_en or "").strip()
    if not text or any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise AppError(
            ErrorCode.SHIPMENTS_PARTY_ENGLISH_NAME_MISSING,
            detail={field: "거래처 영문명을 확인해 주세요."},
        )
    return text


def clean_address(address_en: str | None) -> str | None:
    text = (address_en or "").strip()
    return text or None


# ── 편집 착지(계획 중 라인·헤더 국가·FREE·당사자) — 잠금·검증은 오케스트레이터(trade_chain.shipment_flow)가 끝낸 뒤 부른다 ─────


def insert_line(session: Session, row: Shipment, item: NewShipmentLine, *, actor_id: int) -> int:
    """라인 1줄 추가 — 돌려주는 값은 새 라인 번호(헤더 `last_line_no` 카운터 대입은 `finish_line_change`가 한다 — 결번 허용·재사용 금지).

    ★ 헤더를 dirty로 만드는 대입은 라인 flush 뒤에 몰아서 한다(합계 SUM의 autoflush가 헤더 UPDATE를 먼저 내보내면 version이 두 번 오른다 —
      라인 변경 1건 = 헤더 version +1이 응답 계약이다, SO 라인 편집 선례).
    """
    line_no = row.last_line_no + 1
    line = ShipmentLine(
        shipment_id=row.id,
        line_no=line_no,
        so_line_id=item.so_line_id,
        po_line_id=item.po_line_id,
        sku_id=item.sku_id,
        sku_code=item.sku_code,
        sku_name_ko=item.sku_name_ko,
        sku_name_en=item.sku_name_en,
        sku_kind=item.sku_kind,
        currency=row.currency,
        quantity=item.quantity,
        unit_price_amount=item.unit_price_amount,
        is_free=item.is_free,
        line_amount=item.amount,
        created_by_id=actor_id,
        updated_by_id=actor_id,
    )
    session.add(line)
    flush_translated(session)  # (선적, 원천 라인) 부분 유니크 경합 → 409 DUPLICATE_SOURCE
    return line_no


def finish_line_change(
    session: Session, row: Shipment, *, actor_id: int, last_line_no: int | None = None
) -> None:
    """라인 변경 마무리 — 라인 flush → 합계 재계산 → (추가면) 라인 번호 카운터 → 헤더 version +1(단일 UPDATE)."""
    flush_translated(session)
    editing.recompute_total(session, KIND, row, ShipmentLine)
    if last_line_no is not None:
        row.last_line_no = last_line_no
    editing.bump_header_version(row)
    row.updated_by_id = actor_id
    flush_translated(session)


def set_line_quantity(line: ShipmentLine, quantity: int, *, actor_id: int) -> None:
    """라인 수량(CONTENT, 계획 중) — 금액 = 수량 × 단가 사본(수입은 0, numeric 곱 CHECK가 DB에서 재확인)."""
    line.quantity = quantity
    line.line_amount = (
        0 if line.unit_price_amount is None else line_amount(quantity, line.unit_price_amount)
    )
    line.updated_by_id = actor_id


def remove_line(line: ShipmentLine, *, actor_id: int) -> None:
    line.deleted_at = utcnow()
    line.updated_by_id = actor_id


def remove_party(party: ShipmentParty, *, actor_id: int) -> None:
    party.deleted_at = utcnow()
    party.updated_by_id = actor_id


def apply_header_edit(
    row: Shipment,
    *,
    actor_id: int,
    countries: dict[str, str],
    note: tuple[bool, str | None],
    assignee_id: int | None,
) -> None:
    """헤더 편집 — 국가 2열(CONTENT, 호출자가 계획 중임을 확인)·FREE 2열(메모·담당자). 명시 대입만(동적 setattr 0)."""
    if "origin_country_code" in countries:
        row.origin_country_code = countries["origin_country_code"]
    if "dest_country_code" in countries:
        row.dest_country_code = countries["dest_country_code"]
    if note[0]:
        row.internal_note = note[1]
    if assignee_id is not None:
        row.assignee_id = assignee_id
    editing.bump_header_version(row)  # 편집 요청은 항상 새 version(겹친 편집 409)
    row.updated_by_id = actor_id
