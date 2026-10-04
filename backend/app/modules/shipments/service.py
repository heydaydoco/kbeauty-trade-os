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
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, NotFoundError
from app.core.text import invisible_char_problem, is_invisible_char
from app.core.time import utcnow
from app.modules.shipments.models import (
    CustomsRecord,
    ItemProfileMilestoneType,
    Milestone,
    MilestoneChange,
    MilestoneChangeNotice,
    Shipment,
    ShipmentLine,
    ShipmentParty,
)
from app.modules.trade_docs import editing
from app.modules.trade_docs.constants import RELEASE_BOUND_ACTUALS, DocKind
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
    # 입력(거래처 마스터 텍스트)에서 오는 값의 CHECK — 서비스 검사(`require_english_name`·`clean_address`)가 1차이고, 이 번역은
    # 검사를 빠져나간 값이 500으로 새지 않게 하는 2차 방어선이다(PR-3a 적대 검토: C1 제어문자 500 — 같은 422로 번역).
    "ck_shipment_parties_name_en_clean": ErrorCode.SHIPMENTS_PARTY_ENGLISH_NAME_MISSING,
    "ck_shipment_parties_address_en_clean": ErrorCode.VALIDATION_INVALID_FIELD,
    "ck_shipment_parties_address_en_not_blank": ErrorCode.VALIDATION_INVALID_FIELD,
}

#: S3-2 PR-4a(M15) — 통관 기록·마일스톤 계열 표의 제약 번역(같은 원칙: 유니크 인덱스 + **입력에서 값이 오는 열**을 검사하는 CHECK 전부).
#: 서비스 선검증이 1차이고 이 표는 경합·검사 누락이 500으로 새지 않게 하는 2차 방어선이다(J-09 완결성 시험이 실제 제약과 대사).
#: 입력 유래가 아닌 내부 불변식(소유자 정확히 하나·이력 값 쌍·무변경 이력 금지)은 번역하지 않는다 — 위반은 결함이라 500으로 드러난다.
MILESTONE_CONSTRAINT_ERRORS: dict[str, ErrorCode] = {
    # 유니크 — 동시 최초 입력·같은 신고번호 경합
    "uq_customs_records_declaration_kind_declaration_no_active": (
        ErrorCode.SHIPMENTS_CUSTOMS_DECLARATION_DUPLICATE
    ),
    "uq_milestones_shipment_id_milestone_type_active": ErrorCode.SHIPMENTS_MILESTONE_DUPLICATE_TYPE,
    "uq_milestones_po_id_milestone_type_active": ErrorCode.SHIPMENTS_MILESTONE_DUPLICATE_TYPE,
    # 품목군 세트 중복(쓰기 경로 PR-4c — R-26 DUPLICATE_TYPE 재사용)
    "uq_item_profile_milestone_types_profile_type_active": (
        ErrorCode.SHIPMENTS_MILESTONE_DUPLICATE_TYPE
    ),
    # 통보 연결 — 통보마다 새 통신 기록이라 겹칠 수 없다(경합·결함이면 500이 아니라 재시도 안내 409)
    "uq_milestone_change_notices_change_id_comm_log_id": ErrorCode.CONCURRENCY_VERSION_CONFLICT,
    # 통관 기록 입력 열
    "ck_customs_records_kind_valid": ErrorCode.SHIPMENTS_CUSTOMS_KIND_MISMATCH,
    "ck_customs_records_declaration_no_shape": ErrorCode.VALIDATION_INVALID_FIELD,
    "ck_customs_records_accept_after_declare": ErrorCode.SHIPMENTS_CUSTOMS_ACCEPT_BEFORE_DECLARE,
    "ck_customs_records_note_clean": ErrorCode.VALIDATION_INVALID_FIELD,
    "ck_customs_records_date_range": ErrorCode.VALIDATION_INVALID_FIELD,
    # 마일스톤 입력 열(경로의 종류·계획/실적 값·시간대) — 덮어쓰기 금지 2중의 DB 층(파생 종류 = type_valid 위반)
    "ck_milestones_value_range": ErrorCode.VALIDATION_INVALID_FIELD,
    "ck_milestones_type_valid": ErrorCode.SHIPMENTS_MILESTONE_DERIVED_NOT_EDITABLE,
    "ck_milestones_owner_type_scope": ErrorCode.SHIPMENTS_MILESTONE_TYPE_NOT_APPLICABLE,
    "ck_milestones_date_shape": ErrorCode.SHIPMENTS_MILESTONE_VALUE_SHAPE_MISMATCH,
    "ck_milestones_datetime_shape": ErrorCode.SHIPMENTS_MILESTONE_VALUE_SHAPE_MISMATCH,
    "ck_milestones_tz_iff_instant": ErrorCode.SHIPMENTS_MILESTONE_VALUE_SHAPE_MISMATCH,
    "ck_milestones_tz_format": ErrorCode.SHIPMENTS_MILESTONE_TIMEZONE_INVALID,
    "ck_milestones_customs_actual_from_records": (
        ErrorCode.SHIPMENTS_MILESTONE_ACTUAL_FROM_CUSTOMS_RECORD
    ),
    # 변경 이력 사유(사람 입력)
    "ck_milestone_changes_reason_required": ErrorCode.SHIPMENTS_MILESTONE_REASON_REQUIRED,
    "ck_milestone_changes_reason_clean": ErrorCode.VALIDATION_INVALID_FIELD,
    # 품목군 세트 종류(PR-4c 쓰기 경로 — 파생·OEM 종류 = R-26 TYPE_NOT_APPLICABLE 재사용)
    "ck_item_profile_milestone_types_type_valid": ErrorCode.SHIPMENTS_MILESTONE_TYPE_NOT_APPLICABLE,
    # 선적 통보 기록(comm_logs SHIPMENT 주제)의 요지·오간 날 — 선적 통로가 같은 flush로 넣는다
    "ck_comm_logs_summary_not_blank": ErrorCode.VALIDATION_INVALID_FIELD,
    "ck_comm_logs_shipment_occurred_on_range": ErrorCode.VALIDATION_INVALID_FIELD,
}


def constraint_name(exc: IntegrityError) -> str | None:
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)


def flush_translated(session: Session) -> None:
    """flush하고 번역표의 제약 위반을 업무 오류로 바꾼다(그 밖의 위반은 그대로 전파 — 500). 업무 오류는 TX 전체를 롤백시킨다(멱등 claim 포함)."""
    try:
        session.flush()
    except IntegrityError as exc:
        name = constraint_name(exc) or ""
        code = CONSTRAINT_ERRORS.get(name) or MILESTONE_CONSTRAINT_ERRORS.get(name)
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


#: 주소에서만 허용하는 줄 구분 문자(탭·LF·CR) — 그 밖의 보이지 않는 글자는 거부(DB `ck_shipment_parties_address_en_clean`과 짝).
_ADDRESS_LINE_BREAKS = frozenset("\t\n\r")


def require_english_name(name_en: str | None, *, field: str) -> str:
    """거래처 영문명 — 비었거나 **strip 전 원문**에 보이지 않는 글자(Cc[C0·DEL·C1]·Cf·Zl·Zp·한글 채움 — 하우스 규칙
    `invisible_char_problem`)가 있으면 422 ENGLISH_NAME_MISSING(서류 영문 원천 결측·오염 — fail-visible). 미리보기·생성·당사자 추가가 같은 판정이다
    (DB CHECK `name_en_clean`은 C0·C1만 보는 최후 방어선 — 번역표가 같은 422로 바꾼다)."""
    raw = name_en or ""
    text = raw.strip()
    if not text or invisible_char_problem(raw, label="거래처 영문명") is not None:
        raise AppError(
            ErrorCode.SHIPMENTS_PARTY_ENGLISH_NAME_MISSING,
            detail={field: "거래처 영문명을 확인해 주세요."},
        )
    return text


def clean_address(address_en: str | None, *, field: str) -> str | None:
    """거래처 영문 주소 스냅샷 — 여러 줄(탭·LF·CR)은 허용, 그 밖의 보이지 않는 글자(Cc·Cf·Zl·Zp·한글 채움)는 422 INVALID_FIELD.
    공백뿐이면 NULL로 접는다(주소는 선택 값)."""
    raw = address_en or ""
    if any(is_invisible_char(ch) for ch in raw if ch not in _ADDRESS_LINE_BREAKS):
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={
                field: "거래처 영문 주소에 보이지 않는 글자(제어·서식·채움 문자)가 있습니다 — 거래처 주소를 확인해 주세요."
            },
        )
    text = raw.strip()
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


# ── 통관 기록·마일스톤 착지 (S3-2 PR-4a — 잠금·검증은 오케스트레이터[trade_chain.customs_flow·milestone_flow]가 끝낸 뒤 부른다) ──


def require_customs_record(
    session: Session, shipment_id: int, record_id: int, *, for_update: bool = False
) -> CustomsRecord:
    """경로의 통관 기록이 경로의 선적 소속이 아니면(삭제 포함) 404 — 부작용 0(`D:370` 부모-자식). `for_update` = shipment_children 잠금."""
    stmt = select(CustomsRecord).where(
        CustomsRecord.id == record_id,
        CustomsRecord.shipment_id == shipment_id,
        CustomsRecord.deleted_at.is_(None),
    )
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = session.execute(stmt).scalar_one_or_none()
    if row is None:
        raise NotFoundError(
            log_context={"shipment_id": shipment_id, "customs_record_id": record_id}
        )
    return row


def live_customs_accepted(session: Session, shipment_id: int, kind: str) -> list[date | None]:
    """구분 일치·살아 있는 통관 기록들의 수리일(미수리 = None) — 신고수리 실적 파생(X-02·R-06)의 입력."""
    return list(
        session.execute(
            select(CustomsRecord.accepted_on)
            .where(
                CustomsRecord.shipment_id == shipment_id,
                CustomsRecord.declaration_kind == kind,
                CustomsRecord.deleted_at.is_(None),
            )
            .order_by(CustomsRecord.id)
        ).scalars()
    )


def insert_customs_record(
    session: Session, row: Shipment, values: dict[str, Any], *, actor_id: int
) -> CustomsRecord:
    """통관 기록 1행 INSERT — (구분, 신고번호) 경합은 409 DECLARATION_DUPLICATE로 번역(부분 유니크)."""
    record = CustomsRecord(
        shipment_id=row.id,
        declaration_kind=values["declaration_kind"],
        declaration_no=values["declaration_no"],
        declared_on=values["declared_on"],
        accepted_on=values["accepted_on"],
        customs_broker_partner_id=values["customs_broker_partner_id"],
        note=values["note"],
        created_by_id=actor_id,
        updated_by_id=actor_id,
    )
    session.add(record)
    flush_translated(session)
    return record


def apply_customs_update(customs: CustomsRecord, changes: dict[str, Any], *, actor_id: int) -> None:
    """통관 기록 정정 — 명시 대입만(동적 setattr 0). 보낸 열만 바뀌고 version +1(겹친 정정 409)."""
    if "declaration_no" in changes:
        customs.declaration_no = changes["declaration_no"]
    if "declared_on" in changes:
        customs.declared_on = changes["declared_on"]
    if "accepted_on" in changes:
        customs.accepted_on = changes["accepted_on"]
    if "customs_broker_partner_id" in changes:
        customs.customs_broker_partner_id = changes["customs_broker_partner_id"]
    if "note" in changes:
        customs.note = changes["note"]
    customs.updated_by_id = actor_id
    # 같은 행위자·같은 값이어도 dirty → version_id_col +1(겹친 정정 409)
    customs.updated_at = utcnow()


def remove_customs_record(customs: CustomsRecord, *, actor_id: int) -> None:
    """통관 기록 삭제(soft delete — 사유는 호출자가 audit_log에 남긴다). version +1."""
    customs.deleted_at = utcnow()
    customs.updated_by_id = actor_id
    # 같은 행위자·같은 값이어도 dirty → version_id_col +1(겹친 정정 409)
    customs.updated_at = utcnow()


@dataclass(frozen=True, slots=True)
class CancelBlockers:
    """선적 취소를 막는 살아 있는 사실 기록(R-01·R-16) — 통관 신고번호들·ETD·B/L 발행·ETA 실적 종류들."""

    customs_numbers: list[str]
    actual_types: list[str]


def cancel_blockers(session: Session, shipment_id: int) -> CancelBlockers:
    """살아 있는 통관 기록·출고 결속 실적(ETD·BL_ISSUED·ETA) — 선적 헤더 `FOR UPDATE`를 쥔 호출자가 부른다(하위 쓰기는 전부 같은 헤더를
    `FOR UPDATE`로 잡으므로 판정과 취소 사이에 새 사실이 끼어들 수 없다)."""
    numbers = list(
        session.execute(
            select(CustomsRecord.declaration_no)
            .where(CustomsRecord.shipment_id == shipment_id, CustomsRecord.deleted_at.is_(None))
            .order_by(CustomsRecord.id)
        ).scalars()
    )
    actuals = list(
        session.execute(
            select(Milestone.milestone_type)
            .where(
                Milestone.shipment_id == shipment_id,
                Milestone.deleted_at.is_(None),
                Milestone.milestone_type.in_(sorted(RELEASE_BOUND_ACTUALS)),
                (Milestone.actual_on.is_not(None)) | (Milestone.actual_at.is_not(None)),
            )
            .order_by(Milestone.milestone_type)
        ).scalars()
    )
    return CancelBlockers(numbers, actuals)


def find_milestone(
    session: Session, shipment_id: int, milestone_type: str, *, for_update: bool
) -> Milestone | None:
    """(선적, 종류) 살아 있는 마일스톤 행 — 없으면 None. `for_update` = shipment_children 잠금(헤더 잠금 뒤)."""
    stmt = select(Milestone).where(
        Milestone.shipment_id == shipment_id,
        Milestone.milestone_type == milestone_type,
        Milestone.deleted_at.is_(None),
    )
    if for_update:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return session.execute(stmt).scalar_one_or_none()


def insert_milestone(
    session: Session, *, shipment_id: int, milestone_type: str, actor_id: int
) -> Milestone:
    """빈 마일스톤 행(값 없음) — 값은 호출자가 대입한다. (선적, 종류) 경합은 409 DUPLICATE_TYPE으로 번역."""
    row = Milestone(
        shipment_id=shipment_id,
        milestone_type=milestone_type,
        created_by_id=actor_id,
        updated_by_id=actor_id,
    )
    session.add(row)
    flush_translated(session)
    return row


@dataclass(frozen=True, slots=True)
class MilestoneValue:
    """마일스톤 값 하나 — 날짜형(on) 또는 시각형(at + tz). 셋 다 None이면 '값 없음'."""

    on: date | None = None
    at: datetime | None = None
    tz: str | None = None

    @property
    def empty(self) -> bool:
        return self.on is None and self.at is None


def set_planned(milestone: Milestone, value: MilestoneValue, *, actor_id: int) -> None:
    milestone.planned_on = value.on
    milestone.planned_at = value.at
    milestone.tz = value.tz if value.at is not None else _other_tz(milestone, planned=False)
    milestone.updated_by_id = actor_id
    milestone.updated_at = utcnow()  # dirty 보장 → version_id_col +1


def set_actual(milestone: Milestone, value: MilestoneValue, *, actor_id: int) -> None:
    milestone.actual_on = value.on
    milestone.actual_at = value.at
    milestone.tz = value.tz if value.at is not None else _other_tz(milestone, planned=True)
    milestone.updated_by_id = actor_id
    milestone.updated_at = utcnow()  # dirty 보장 → version_id_col +1


def _other_tz(milestone: Milestone, *, planned: bool) -> str | None:
    """한쪽 시각 값을 지울 때 남은 쪽 시각 값이 있으면 그 tz를 유지한다(`tz_iff_instant`)."""
    other = milestone.planned_at if planned else milestone.actual_at
    return milestone.tz if other is not None else None


def insert_change(
    session: Session,
    *,
    milestone: Milestone,
    change_kind: str,
    old: MilestoneValue,
    new: MilestoneValue,
    reason: str | None,
    actor_id: int,
) -> MilestoneChange:
    """변경 이력 1행(IMMUTABLE) — 사유 CHECK 위반은 422로 번역."""
    change = MilestoneChange(
        milestone_id=milestone.id,
        change_kind=change_kind,
        old_on=old.on,
        old_at=old.at,
        old_tz=old.tz if old.at is not None else None,
        new_on=new.on,
        new_at=new.at,
        new_tz=new.tz if new.at is not None else None,
        reason=reason,
        actor_user_id=actor_id,
    )
    session.add(change)
    flush_translated(session)
    return change


def require_change(session: Session, shipment_id: int, change_id: int) -> MilestoneChange:
    """경로의 변경 이력이 경로의 선적 마일스톤 소속이 아니면 404(부작용 0 — 다른 선적의 변경 id 차단)."""
    change = session.execute(
        select(MilestoneChange)
        .join(Milestone, Milestone.id == MilestoneChange.milestone_id)
        .where(MilestoneChange.id == change_id, Milestone.shipment_id == shipment_id)
    ).scalar_one_or_none()
    if change is None:
        raise NotFoundError(log_context={"shipment_id": shipment_id, "change_id": change_id})
    return change


def lock_notice_slot(session: Session, change: MilestoneChange) -> int:
    """통보 상한 판정용 — 변경의 소유 마일스톤 행을 `FOR UPDATE`(shipment_children — 선적 잠금 뒤)로 잡고 그 변경의 통보 수를 센다.

    이력·통보 표는 IMMUTABLE(UPDATE 권한 회수)이라 행 잠금을 걸 수 없다 — 같은 마일스톤의 통보 동시 기록을 소유 행 잠금으로 직렬화한다
    (적대 검토 반영 ⑧). 통보 연결 행만 세므로 질의 2회.
    """
    session.execute(
        select(Milestone.id)
        .where(Milestone.id == change.milestone_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    return int(
        session.execute(
            select(func.count())
            .select_from(MilestoneChangeNotice)
            .where(MilestoneChangeNotice.change_id == change.id)
        ).scalar_one()
    )


def insert_notice(
    session: Session, *, change: MilestoneChange, comm_log_id: int, actor_id: int
) -> MilestoneChangeNotice:
    notice = MilestoneChangeNotice(
        change_id=change.id, comm_log_id=comm_log_id, actor_user_id=actor_id
    )
    session.add(notice)
    flush_translated(session)
    return notice


def profile_milestone_sets(session: Session, profile_ids: set[int]) -> dict[int, frozenset[str]]:
    """품목군 → 마일스톤 세트(살아 있는 행). 세트가 없는 품목군은 키가 없다(호출자가 '세트 미정의'로 본다)."""
    if not profile_ids:
        return {}
    found: dict[int, set[str]] = {}
    for profile_id, milestone_type in session.execute(
        select(ItemProfileMilestoneType.profile_id, ItemProfileMilestoneType.milestone_type).where(
            ItemProfileMilestoneType.profile_id.in_(sorted(profile_ids)),
            ItemProfileMilestoneType.deleted_at.is_(None),
        )
    ).all():
        found.setdefault(int(profile_id), set()).add(str(milestone_type))
    return {key: frozenset(values) for key, values in found.items()}
