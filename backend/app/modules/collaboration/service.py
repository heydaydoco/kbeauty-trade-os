"""대행 계약·통신 기록 서비스 (§5.4 / §17.1·17.2·17.4 / s2-4-plan.md §2 안건 ① — S2-4 PR-1).

■ 폴리모픽 주제의 실재 검증은 서비스 몫이다

  comm_logs.subject는 FK가 없다(주제가 여러 테이블로 늘어날 자리). 등록 시 주제 실재를
  이 파일이 검증하고, 목록 표시명은 페이지 분량을 일괄 조회한다(N+1 없음 — §18.4).

■ 날짜 규율 (§21 "날짜 3종")

  occurred_on = 실제로 오간 날(업무일, 사람이 적는다 — 소급 입력 수용), 입력일 = created_at
  (시스템), 다음 액션 기한·완료일 = 업무일. "오늘"은 today_kst()다(UTC 날짜 금지).

■ 계약의 유효 여부는 저장하지 않는다 — `is_current`는 조회 시점 계산값이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, NoReturn

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, NotFoundError, VersionConflictError
from app.core.money import minor_units
from app.core.time import today_kst, utcnow
from app.modules.certifications.models import Certification
from app.modules.collaboration.models import (
    GENERIC_COMM_SUBJECT_TYPES,
    AgencyContract,
    CommLog,
)
from app.modules.documents.models import Document
from app.modules.idempotency import service as idempotency
from app.modules.identity.service import AuthenticatedUser
from app.modules.partners.models import Partner
from app.modules.partners.service import require_partner_of_type

CONTRACT_CREATE_ENDPOINT = "POST /api/v1/agency-contracts"
COMM_LOG_CREATE_ENDPOINT = "POST /api/v1/comm-logs"


# ── 뷰 ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ContractView:
    id: int
    partner_id: int
    partner_name: str
    contract_no: str
    scope_note: str | None
    start_on: date
    end_on: date | None
    fee_amount: int | None
    fee_currency: str | None
    note: str | None
    version: int
    #: 계산값(저장 컬럼 아님) — 오늘(KST)이 계약 기간 안인가. 종료일 NULL=기간 미정.
    is_current: bool


@dataclass(frozen=True, slots=True)
class CommLogView:
    id: int
    subject_type: str
    subject_id: int
    #: 주제의 사람용 표기(인증이면 "#id · 요건명") — 페이지 분량 일괄 조회.
    subject_label: str
    partner_id: int | None
    partner_name: str | None
    occurred_on: date
    summary: str
    next_action: str | None
    next_action_due: date | None
    next_action_done_on: date | None
    #: 계산값 — 다음 액션이 있고 아직 끝나지 않았는가 / 기한이 지났는가(오늘 KST 기준).
    follow_up_open: bool
    follow_up_overdue: bool
    #: 첨부(documents 소유 COMM_LOG) 활성 건수.
    attachment_count: int
    #: 입력 시각(UTC ISO) — 실제로 오간 날(occurred_on)과 구분되는 시스템 기록이다.
    created_at: str
    version: int


def _serialize_contract(view: ContractView) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": view.id,
        "partner_id": view.partner_id,
        "partner_name": view.partner_name,
        "contract_no": view.contract_no,
        "scope_note": view.scope_note,
        "start_on": view.start_on.isoformat(),
        "end_on": view.end_on.isoformat() if view.end_on else None,
        "fee_amount": view.fee_amount,
        "fee_currency": view.fee_currency,
        "note": view.note,
        "version": view.version,
        "is_current": view.is_current,
    }
    return body


def _serialize_comm_log(view: CommLogView) -> dict[str, Any]:
    return {
        "id": view.id,
        "subject_type": view.subject_type,
        "subject_id": view.subject_id,
        "subject_label": view.subject_label,
        "partner_id": view.partner_id,
        "partner_name": view.partner_name,
        "occurred_on": view.occurred_on.isoformat(),
        "summary": view.summary,
        "next_action": view.next_action,
        "next_action_due": view.next_action_due.isoformat() if view.next_action_due else None,
        "next_action_done_on": (
            view.next_action_done_on.isoformat() if view.next_action_done_on else None
        ),
        "follow_up_open": view.follow_up_open,
        "follow_up_overdue": view.follow_up_overdue,
        "attachment_count": view.attachment_count,
        "created_at": view.created_at,
        "version": view.version,
    }


# ── 공통 ───────────────────────────────────────────────────────────────────


def _as_date(raw: Any) -> date | None:
    if raw is None or isinstance(raw, date):
        return raw
    return date.fromisoformat(str(raw))


def _require_version(row: Any, expected: Any, *, key: str) -> None:
    if int(expected) != row.version:
        raise VersionConflictError(
            log_context={key: row.id, "expected": expected, "actual": row.version}
        )


def _invalid(field: str, message: str, **context: Any) -> AppError:
    return AppError(
        ErrorCode.VALIDATION_INVALID_FIELD, detail={field: message}, log_context=context or None
    )


def parse_fee(payload: dict[str, Any]) -> tuple[int | None, str | None]:
    """수수료 — 사람이 쓰는 표기(12.34)를 정수 최소단위로(§2 ADR-02). 통화와 한 쌍이다.

    partners.parse_credit_limit과 같은 꼴이다 — 합산·환산에 쓰이지 않는 표시용 값이지만
    금액 규약(정수 최소단위+통화 쌍)은 그대로 지킨다.
    """
    raw = payload.get("fee")
    currency = payload.get("fee_currency")
    if raw is None and currency is None:
        return None, None
    if raw is None or currency is None:
        raise _invalid("fee", "수수료와 통화는 함께 입력하거나 함께 비워 주세요.")
    code = str(currency).strip().upper()
    try:
        exponent = minor_units(code)
    except Exception as exc:  # UnknownCurrencyError — 통화 목록은 money.py가 유일 출처
        raise _invalid(
            "fee_currency", f"등록되지 않은 통화입니다: {code}", fee_currency=code
        ) from exc
    try:
        amount = Decimal(str(raw))
    except InvalidOperation as exc:
        raise _invalid("fee", "수수료는 숫자여야 합니다.") from exc
    scaled = amount.scaleb(exponent)
    if scaled != scaled.to_integral_value():
        raise _invalid(
            "fee", f"{code}는 소수점 {exponent}자리까지 쓸 수 있습니다. 자릿수를 확인해 주세요."
        )
    return int(scaled), code


# ── 대행 계약 ──────────────────────────────────────────────────────────────


def _contract_view(row: AgencyContract, partner_name: str, today: date) -> ContractView:
    return ContractView(
        id=row.id,
        partner_id=row.partner_id,
        partner_name=partner_name,
        contract_no=row.contract_no,
        scope_note=row.scope_note,
        start_on=row.start_on,
        end_on=row.end_on,
        fee_amount=row.fee_amount,
        fee_currency=row.fee_currency,
        note=row.note,
        version=row.version,
        is_current=is_contract_current(row.start_on, row.end_on, today),
    )


def is_contract_current(start_on: date, end_on: date | None, today: date) -> bool:
    """계약 기간 안인가 — 시작·종료일 모두 포함, 종료일 없음=기간 미정. 저장하지 않는 계산값."""
    return start_on <= today and (end_on is None or today <= end_on)


def require_contract(
    session: Session, contract_id: int, *, for_update: bool = False
) -> AgencyContract:
    stmt = select(AgencyContract).where(
        AgencyContract.id == contract_id, AgencyContract.deleted_at.is_(None)
    )
    if for_update:
        stmt = stmt.with_for_update()
    row = session.execute(stmt).scalar_one_or_none()
    if row is None:
        raise NotFoundError(log_context={"contract_id": contract_id})
    return row


def _require_contract_partner(session: Session, partner_id: int) -> Partner:
    return require_partner_of_type(
        session, partner_id, "CERT_AGENCY", field="partner_id", type_label="인증대행"
    )


def _check_contract_period(start_on: date, end_on: date | None) -> None:
    if end_on is not None and end_on < start_on:
        raise _invalid("end_on", "계약 종료일은 시작일보다 빠를 수 없습니다.")


_CONTRACT_UNIQUE = "uq_agency_contracts_partner_id_contract_no_active"


def _translate_contract_integrity(payload: dict[str, Any], exc: IntegrityError) -> NoReturn:
    """유일 위반만 "이미 등록된 번호" 안내로 바꾼다 — 다른 무결성 오류(경쟁 중 FK 등)를 중복 번호로
    오진하면 사용자가 엉뚱한 곳을 고친다. 그 외는 원 예외를 그대로 올려 로그에서 보이게 한다."""
    # 함정 ② — 실패 경로에서는 ORM 속성을 읽지 않고 요청 값만 쓴다.
    if _CONTRACT_UNIQUE not in str(exc.orig):
        raise exc
    raise AppError(
        ErrorCode.VALIDATION_INVALID_FIELD,
        detail={
            "contract_no": "같은 대행사에 이미 등록된 계약 번호입니다. 다른 번호를 입력해 주세요."
        },
        log_context={"contract_no": payload.get("contract_no")},
    ) from exc


def _clean_contract_no(raw: Any) -> str:
    """계약 번호 — 공백뿐이면 422(DB CHECK가 막아도 "중복"으로 오진되지 않게 서비스가 먼저 안내)."""
    value = str(raw).strip()
    if not value:
        raise _invalid("contract_no", "계약 번호를 입력해 주세요.")
    return value


def create_contract(
    *, actor: AuthenticatedUser, idempotency_key: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=CONTRACT_CREATE_ENDPOINT,
            key=idempotency_key,
            request_body=payload,
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        partner = _require_contract_partner(session, int(payload["partner_id"]))
        start_on = _as_date(payload["start_on"])
        end_on = _as_date(payload.get("end_on"))
        assert start_on is not None
        _check_contract_period(start_on, end_on)
        fee_amount, fee_currency = parse_fee(payload)
        partner_name = partner.name_ko

        row = AgencyContract(
            partner_id=partner.id,
            contract_no=_clean_contract_no(payload["contract_no"]),
            scope_note=payload.get("scope_note"),
            start_on=start_on,
            end_on=end_on,
            fee_amount=fee_amount,
            fee_currency=fee_currency,
            note=payload.get("note"),
            created_by_id=actor.id,
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError as exc:
            _translate_contract_integrity(payload, exc)

        body = _serialize_contract(_contract_view(row, partner_name, today_kst()))
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def update_contract(
    *, actor: AuthenticatedUser, contract_id: int, payload: dict[str, Any]
) -> ContractView:
    """계약 편집 — exclude_unset 의미론. 대행사(partner_id)는 바꾸지 않는다(계약의 정체성)."""
    with unit_of_work() as uow:
        session = uow.session
        row = require_contract(session, contract_id, for_update=True)
        _require_version(row, payload["version"], key="contract_id")

        if "contract_no" in payload:
            row.contract_no = _clean_contract_no(payload["contract_no"])
        if "scope_note" in payload:
            row.scope_note = payload["scope_note"]
        if "note" in payload:
            row.note = payload["note"]
        start_on = _as_date(payload["start_on"]) if "start_on" in payload else row.start_on
        end_on = _as_date(payload["end_on"]) if "end_on" in payload else row.end_on
        assert start_on is not None
        _check_contract_period(start_on, end_on)
        row.start_on = start_on
        row.end_on = end_on
        if "fee" in payload or "fee_currency" in payload:
            # 수수료 쌍은 함께 바뀐다 — 한쪽만 보내면 기존 값과 짝이 어긋날 수 있어
            # 둘 다 요청 값으로 판정한다(생략=비움이 아니라 "함께 입력하거나 함께 비움" 오류).
            row.fee_amount, row.fee_currency = parse_fee(payload)
        row.updated_by_id = actor.id
        partner_name = session.execute(
            select(Partner.name_ko).where(Partner.id == row.partner_id)
        ).scalar_one()
        try:
            session.flush()
        except IntegrityError as exc:
            _translate_contract_integrity(payload, exc)
        return _contract_view(row, str(partner_name), today_kst())


def delete_contract(*, actor: AuthenticatedUser, contract_id: int) -> None:
    with unit_of_work() as uow:
        row = require_contract(uow.session, contract_id, for_update=True)
        row.deleted_at = utcnow()
        row.updated_by_id = actor.id
        uow.session.flush()


def get_contract(contract_id: int) -> ContractView:
    with unit_of_work() as uow:
        session = uow.session
        row = require_contract(session, contract_id)
        name = session.execute(
            select(Partner.name_ko).where(Partner.id == row.partner_id)
        ).scalar_one()
        return _contract_view(row, str(name), today_kst())


def list_contracts(
    *,
    partner_id: int | None,
    current_only: bool,
    offset: int,
    limit: int,
) -> tuple[list[ContractView], int]:
    today = today_kst()
    with unit_of_work() as uow:
        session = uow.session
        conditions: list[ColumnElement[bool]] = [AgencyContract.deleted_at.is_(None)]
        if partner_id is not None:
            conditions.append(AgencyContract.partner_id == partner_id)
        if current_only:
            conditions.append(AgencyContract.start_on <= today)
            conditions.append((AgencyContract.end_on.is_(None)) | (AgencyContract.end_on >= today))
        total = session.execute(
            select(func.count()).select_from(AgencyContract).where(*conditions)
        ).scalar_one()
        rows = session.execute(
            select(AgencyContract, Partner.name_ko)
            .join(Partner, AgencyContract.partner_id == Partner.id)
            .where(*conditions)
            .order_by(Partner.name_ko, AgencyContract.start_on.desc(), AgencyContract.id)
            .offset(offset)
            .limit(limit)
        ).all()
        return [_contract_view(row, str(name), today) for row, name in rows], total


# ── 통신 기록 ──────────────────────────────────────────────────────────────


def _subject_labels(session: Session, keys: set[tuple[str, int]]) -> dict[tuple[str, int], str]:
    """페이지 분량 주제 표시 — 소비분(인증)만. 삭제된 주제도 표기는 남긴다."""
    labels: dict[tuple[str, int], str] = {}
    certification_ids = [
        subject_id for subject_type, subject_id in keys if subject_type == "CERTIFICATION"
    ]
    if certification_ids:
        for certification_id, template_name in session.execute(
            select(Certification.id, Certification.template_name).where(
                Certification.id.in_(certification_ids)
            )
        ):
            labels[("CERTIFICATION", certification_id)] = f"#{certification_id} · {template_name}"
    return labels


def _attachment_counts(session: Session, log_ids: list[int]) -> dict[int, int]:
    if not log_ids:
        return {}
    rows = session.execute(
        select(Document.owner_id, func.count())
        .where(
            Document.owner_type == "COMM_LOG",
            Document.owner_id.in_(log_ids),
            Document.deleted_at.is_(None),
        )
        .group_by(Document.owner_id)
    ).all()
    return {owner_id: int(count) for owner_id, count in rows}


def _partner_names(session: Session, partner_ids: set[int | None]) -> dict[int, str]:
    ids = {value for value in partner_ids if value is not None}
    if not ids:
        return {}
    return {
        partner_id: str(name)
        for partner_id, name in session.execute(
            select(Partner.id, Partner.name_ko).where(Partner.id.in_(ids))
        )
    }


def _comm_log_views(session: Session, rows: list[CommLog]) -> list[CommLogView]:
    """뷰 일괄 조립 — 주제 표기·상대 거래처명·첨부 건수를 질의 한 번씩으로(N+1 방지)."""
    today = today_kst()
    labels = _subject_labels(session, {(row.subject_type, row.subject_id) for row in rows})
    names = _partner_names(session, {row.partner_id for row in rows})
    counts = _attachment_counts(session, [row.id for row in rows])
    views: list[CommLogView] = []
    for row in rows:
        follow_up_open = row.next_action is not None and row.next_action_done_on is None
        follow_up_overdue = (
            follow_up_open and row.next_action_due is not None and row.next_action_due < today
        )
        views.append(
            CommLogView(
                id=row.id,
                subject_type=row.subject_type,
                subject_id=row.subject_id,
                subject_label=labels.get(
                    (row.subject_type, row.subject_id), f"{row.subject_type}#{row.subject_id}"
                ),
                partner_id=row.partner_id,
                partner_name=names.get(row.partner_id) if row.partner_id is not None else None,
                occurred_on=row.occurred_on,
                summary=row.summary,
                next_action=row.next_action,
                next_action_due=row.next_action_due,
                next_action_done_on=row.next_action_done_on,
                follow_up_open=follow_up_open,
                follow_up_overdue=follow_up_overdue,
                attachment_count=counts.get(row.id, 0),
                created_at=row.created_at.isoformat()
                if isinstance(row.created_at, datetime)
                else "",
                version=row.version,
            )
        )
    return views


def require_comm_log(session: Session, log_id: int, *, for_update: bool = False) -> CommLog:
    """범용 `/comm-logs` id 접근(상세·PATCH·DELETE) — 범용 주제 밖(SHIPMENT 통보 기록)은 **없는 것과 같다(404, 부작용 0)**(R-05).
    선적 통보는 롤오버 이력과 결속된 사실이라 다른 역할이 범용 경로로 고치거나 지우면 결속이 깨진다."""
    stmt = select(CommLog).where(
        CommLog.id == log_id,
        CommLog.deleted_at.is_(None),
        CommLog.subject_type.in_(GENERIC_COMM_SUBJECT_TYPES),
    )
    if for_update:
        stmt = stmt.with_for_update()
    row = session.execute(stmt).scalar_one_or_none()
    if row is None:
        raise NotFoundError(log_context={"comm_log_id": log_id})
    return row


def _require_subject(session: Session, subject_type: str, subject_id: int) -> None:
    """주제 실재 검증 — 폴리모픽이라 FK가 없다. 소비분(CERTIFICATION)만 있다."""
    if subject_type == "CERTIFICATION":
        found = session.execute(
            select(Certification.id).where(
                Certification.id == subject_id, Certification.deleted_at.is_(None)
            )
        ).scalar_one_or_none()
        if found is None:
            raise _invalid(
                "subject_id",
                "존재하지 않는 인증 인스턴스입니다. 인증 목록에서 다시 선택해 주세요.",
                subject_type=subject_type,
                subject_id=subject_id,
            )
        return
    raise _invalid("subject_type", "알 수 없는 주제 유형입니다.", subject_type=subject_type)


def _require_optional_partner(session: Session, partner_id: int | None) -> None:
    if partner_id is None:
        return
    found = session.execute(
        select(Partner.id).where(Partner.id == partner_id, Partner.deleted_at.is_(None))
    ).scalar_one_or_none()
    if found is None:
        raise _invalid(
            "partner_id",
            "존재하지 않는 거래처입니다. 거래처를 먼저 등록해 주세요.",
            partner_id=partner_id,
        )


def _validate_follow_up(
    *,
    occurred_on: date,
    next_action: str | None,
    due: date | None,
    done_on: date | None,
    today: date,
) -> None:
    """다음 액션·기한·완료일의 일관성 — DB CHECK 3건이 마지막 방어선이고 여기는 422 안내다."""
    if occurred_on > today:
        raise _invalid("occurred_on", "실제로 오간 날은 오늘 이후일 수 없습니다.")
    if next_action is None and (due is not None or done_on is not None):
        raise _invalid(
            "next_action", "다음 액션 기한·완료일을 넣으려면 다음 액션 내용을 함께 적어 주세요."
        )
    if due is not None and due < occurred_on:
        raise _invalid("next_action_due", "다음 액션 기한은 오간 날보다 빠를 수 없습니다.")
    if done_on is not None:
        if done_on < occurred_on:
            raise _invalid("next_action_done_on", "완료일은 오간 날보다 빠를 수 없습니다.")
        if done_on > today:
            raise _invalid("next_action_done_on", "완료일은 오늘 이후일 수 없습니다.")


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def create_comm_log(
    *, actor: AuthenticatedUser, idempotency_key: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=COMM_LOG_CREATE_ENDPOINT,
            key=idempotency_key,
            request_body=payload,
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        subject_type = str(payload["subject_type"])
        subject_id = int(payload["subject_id"])
        _require_subject(session, subject_type, subject_id)
        partner_id = int(payload["partner_id"]) if payload.get("partner_id") is not None else None
        _require_optional_partner(session, partner_id)

        occurred_on = _as_date(payload["occurred_on"])
        assert occurred_on is not None
        summary = _clean_text(payload["summary"])
        if summary is None:
            raise _invalid("summary", "요지를 입력해 주세요.")
        next_action = _clean_text(payload.get("next_action"))
        due = _as_date(payload.get("next_action_due"))
        _validate_follow_up(
            occurred_on=occurred_on,
            next_action=next_action,
            due=due,
            done_on=None,
            today=today_kst(),
        )

        row = CommLog(
            subject_type=subject_type,
            subject_id=subject_id,
            partner_id=partner_id,
            occurred_on=occurred_on,
            summary=summary,
            next_action=next_action,
            next_action_due=due,
            created_by_id=actor.id,
        )
        session.add(row)
        session.flush()
        session.refresh(row)
        body = _serialize_comm_log(_comm_log_views(session, [row])[0])
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def update_comm_log(
    *, actor: AuthenticatedUser, log_id: int, payload: dict[str, Any]
) -> CommLogView:
    """통신 기록 편집 — exclude_unset 의미론(안 보낸 필드는 안 바뀐다).

    다음 액션 내용을 비우면(null) 기한·완료일도 함께 비워진다(주인 없는 날짜 금지).
    완료 처리는 next_action_done_on에 날짜를 넣는 것이고, null이면 완료 취소(재개)다.
    """
    with unit_of_work() as uow:
        session = uow.session
        row = require_comm_log(session, log_id, for_update=True)
        _require_version(row, payload["version"], key="comm_log_id")

        if "partner_id" in payload:
            partner_id = int(payload["partner_id"]) if payload["partner_id"] is not None else None
            if partner_id != row.partner_id:
                _require_optional_partner(session, partner_id)
            row.partner_id = partner_id
        occurred_on = (
            _as_date(payload["occurred_on"]) if "occurred_on" in payload else row.occurred_on
        )
        assert occurred_on is not None
        if "summary" in payload:
            summary = _clean_text(payload["summary"])
            if summary is None:
                raise _invalid("summary", "요지를 입력해 주세요.")
            row.summary = summary
        next_action = row.next_action
        due = row.next_action_due
        done_on = row.next_action_done_on
        if "next_action" in payload:
            new_text = _clean_text(payload["next_action"])
            if new_text is not None and new_text != next_action:
                # 다른 액션으로 바뀌면 이전 액션의 완료 표시를 이어받지 않는다(새 액션이 처음부터 "완료"로 보이는 것 방지).
                done_on = None
            next_action = new_text
            if next_action is None:
                # 다음 액션을 지우면 딸린 날짜도 함께 지운다 — 명시된 날짜가 있으면 아래 검증이 거절.
                due = None
                done_on = None
        if "next_action_due" in payload:
            due = _as_date(payload["next_action_due"])
        if "next_action_done_on" in payload:
            done_on = _as_date(payload["next_action_done_on"])
        _validate_follow_up(
            occurred_on=occurred_on,
            next_action=next_action,
            due=due,
            done_on=done_on,
            today=today_kst(),
        )
        row.occurred_on = occurred_on
        row.next_action = next_action
        row.next_action_due = due
        row.next_action_done_on = done_on
        row.updated_by_id = actor.id
        session.flush()
        return _comm_log_views(session, [row])[0]


def delete_comm_log(*, actor: AuthenticatedUser, log_id: int) -> None:
    with unit_of_work() as uow:
        row = require_comm_log(uow.session, log_id, for_update=True)
        if _attachment_counts(uow.session, [row.id]).get(row.id, 0) > 0:
            raise AppError(
                ErrorCode.COLLABORATION_COMM_LOG_HAS_ATTACHMENTS,
                log_context={"comm_log_id": row.id},
            )
        row.deleted_at = utcnow()
        row.updated_by_id = actor.id
        uow.session.flush()


def get_comm_log(log_id: int) -> CommLogView:
    with unit_of_work() as uow:
        session = uow.session
        return _comm_log_views(session, [require_comm_log(session, log_id)])[0]


def list_comm_logs(
    *,
    subject_type: str | None,
    subject_id: int | None,
    open_only: bool,
    offset: int,
    limit: int,
) -> tuple[list[CommLogView], int]:
    with unit_of_work() as uow:
        session = uow.session
        # 범용 목록의 기본 조건 = 범용 주제만(R-05 — SHIPMENT 통보 기록은 선적 화면의 변경 이력으로만 노출)
        conditions: list[ColumnElement[bool]] = [
            CommLog.deleted_at.is_(None),
            CommLog.subject_type.in_(GENERIC_COMM_SUBJECT_TYPES),
        ]
        if subject_type is not None:
            conditions.append(CommLog.subject_type == subject_type)
        if subject_id is not None:
            conditions.append(CommLog.subject_id == subject_id)
        if open_only:
            conditions.append(CommLog.next_action.is_not(None))
            conditions.append(CommLog.next_action_done_on.is_(None))
        total = session.execute(
            select(func.count()).select_from(CommLog).where(*conditions)
        ).scalar_one()
        rows = list(
            session.execute(
                select(CommLog)
                .where(*conditions)
                .order_by(CommLog.occurred_on.desc(), CommLog.id.desc())
                .offset(offset)
                .limit(limit)
            ).scalars()
        )
        return _comm_log_views(session, rows), total


# ── 선적 통보 기록(SHIPMENT 주제) — 선적 전용 통로 1곳만 부른다(S3-2 PR-4a / ADR-0083 / R-05) ──────────


def record_shipment_comm_log(
    session: Session,
    *,
    shipment_id: int,
    partner_id: int | None,
    occurred_on: date,
    summary: str,
    actor_id: int,
) -> CommLog:
    """선적 마일스톤 롤오버 통보의 통신 기록 1행(SHIPMENT 주제) — **호출처는 `trade_chain/milestone_flow.py` 1곳**(아키텍처 시험).

    주제·상대 거래처 실재와 입력 위생은 호출자(선적 통로)가 잠금 순서 안에서 끝낸 뒤 부른다 — 여기는 행을 만드는 착지뿐이다.
    flush는 호출자가 제약 번역 통로(`shipments.flush_translated`)로 한다(CHECK 위반이 500으로 새지 않게).
    발송 0(일어난 일의 기록 — §5.4 "생성까지 시스템, 발송은 사람"). 다음 액션·기한은 두지 않는다(정체 독촉 대상 아님).
    """
    row = CommLog(
        subject_type="SHIPMENT",
        subject_id=shipment_id,
        partner_id=partner_id,
        occurred_on=occurred_on,
        summary=summary,
        created_by_id=actor_id,
        updated_by_id=actor_id,
    )
    session.add(row)
    return row
