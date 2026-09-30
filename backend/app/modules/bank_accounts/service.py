"""은행 계좌 서비스 (S3-1 design-A A8) — 쓰기 ADMIN 전용, 조회 ADMIN·TRADE(PI 선택용).

■ 발행된 PI는 계좌 값을 **복사**해 갖는다 — 여기서 수정·비활성해도 과거 PI는 불변(스냅샷).
■ 비활성 = soft delete. 활성 계좌만 PI 생성에서 선택된다.
■ 중복 방지: 유니크 키에 계좌번호를 넣지 않고(`test_secret_boundaries`) **서비스가 (정규화 계좌번호, SWIFT)로 검사**한다
  — 경합 잔여 위험(동시 등록)은 ADMIN 전용 저빈도라 관찰로 등재했다.
■ 계좌번호는 로그·audit·이벤트에 싣지 않는다(redaction `_account_no` 접미). 등록·수정·비활성은 audit_log에 남기되(같은 TX)
  detail은 **변경된 필드 이름만** 싣는다(계좌번호·SWIFT 값·전후값 금지). 이벤트(outbox)는 소비자가 없어 발행하지 않는다.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, NotFoundError, VersionConflictError
from app.core.time import to_kst, utcnow
from app.modules.audit import service as audit
from app.modules.audit.models import AuditAction
from app.modules.bank_accounts.models import BankAccount
from app.modules.idempotency import service as idempotency
from app.modules.identity.service import AuthenticatedUser
from app.modules.trade_docs.constants import SWIFT_PATTERN
from app.modules.trade_docs.fx import require_known_currency

BANK_ACCOUNT_CREATE_ENDPOINT = "POST /api/v1/bank-accounts"
EXPORT_MAX_ROWS = 50_000
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SWIFT = re.compile(SWIFT_PATTERN)

_TEXT_FIELDS = (
    "label",
    "beneficiary_name",
    "beneficiary_address",
    "bank_name",
    "bank_address",
    "account_no",
)


def _invalid(field: str, message: str) -> AppError:
    return AppError(ErrorCode.VALIDATION_INVALID_FIELD, detail={field: message})


def _clean_text(field: str, raw: object) -> str:
    text = str(raw).strip()
    if not text:
        raise _invalid(field, "이 항목은 비울 수 없습니다.")
    if _CONTROL.search(text):
        raise _invalid(field, "개행·제어문자는 입력할 수 없습니다.")
    return text


def _clean_swift(raw: object) -> str:
    code = str(raw).strip().upper()
    if not _SWIFT.match(code):
        raise _invalid("swift_code", "SWIFT 코드는 영문 대문자·숫자 8자 또는 11자입니다.")
    return code


def normalize_account_no(value: str) -> str:
    """중복 검사용 정규화 — 공백·하이픈 제거·대문자. 저장값은 사용자가 입력한 표기 그대로다."""
    return re.sub(r"[\s\-]", "", value).upper()


def _duplicate_exists(
    session: Session, account_no: str, swift_code: str, *, exclude_id: int | None = None
) -> bool:
    wanted = normalize_account_no(account_no)
    query = select(BankAccount.id, BankAccount.account_no).where(
        BankAccount.deleted_at.is_(None), BankAccount.swift_code == swift_code
    )
    if exclude_id is not None:
        query = query.where(BankAccount.id != exclude_id)
    return any(normalize_account_no(no) == wanted for _id, no in session.execute(query).all())


def _body(row: BankAccount) -> dict[str, Any]:
    return {
        "id": row.id,
        "label": row.label,
        "currency": row.currency,
        "beneficiary_name": row.beneficiary_name,
        "beneficiary_address": row.beneficiary_address,
        "bank_name": row.bank_name,
        "bank_address": row.bank_address,
        "account_no": row.account_no,
        "swift_code": row.swift_code,
        "version": row.version,
        "created_at": row.created_at.isoformat(),
    }


def require_bank_account(session: Session, account_id: int) -> BankAccount:
    row = session.execute(
        select(BankAccount).where(BankAccount.id == account_id, BankAccount.deleted_at.is_(None))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(log_context={"bank_account_id": account_id})
    return row


def _flush_or_duplicate(session: Session) -> None:
    """flush에서 이름 유니크(부분 인덱스) 경합 위반이 나면 서비스 검사와 같은 422로 번역한다(동시 등록의 500 방지)."""
    try:
        session.flush()
    except IntegrityError as exc:
        if "uq_bank_accounts_label_active" in str(exc.orig):
            raise _invalid(
                "label", "같은 이름의 계좌가 이미 있습니다. 다른 이름을 입력해 주세요."
            ) from None
        raise


def _label_taken(session: Session, label: str, *, exclude_id: int | None = None) -> bool:
    query = (
        select(func.count())
        .select_from(BankAccount)
        .where(BankAccount.deleted_at.is_(None), BankAccount.label == label)
    )
    if exclude_id is not None:
        query = query.where(BankAccount.id != exclude_id)
    return bool(session.execute(query).scalar_one())


def create_bank_account(
    *, actor: AuthenticatedUser, idempotency_key: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=BANK_ACCOUNT_CREATE_ENDPOINT,
            key=idempotency_key,
            request_body=payload,
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body
        values = {field: _clean_text(field, payload[field]) for field in _TEXT_FIELDS}
        currency = require_known_currency(payload["currency"])
        swift = _clean_swift(payload["swift_code"])
        if _label_taken(session, values["label"]):
            raise _invalid("label", "같은 이름의 계좌가 이미 있습니다. 다른 이름을 입력해 주세요.")
        if _duplicate_exists(session, values["account_no"], swift):
            raise _invalid("account_no", "이미 등록된 계좌입니다(같은 계좌번호·SWIFT).")
        row = BankAccount(
            **values,
            currency=currency,
            swift_code=swift,
            created_by_id=actor.id,
            updated_by_id=actor.id,
        )
        session.add(row)
        _flush_or_duplicate(session)
        audit.record(
            session,
            action=AuditAction.BANK_ACCOUNT_CREATED,
            actor_user_id=actor.id,
            entity_type="bank_accounts",
            entity_id=row.id,
            detail={"fields": sorted([*values, "currency", "swift_code"])},
        )
        body = _body(row)
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def update_bank_account(
    *, actor: AuthenticatedUser, account_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    with unit_of_work() as uow:
        session = uow.session
        row = session.execute(
            select(BankAccount)
            .where(BankAccount.id == account_id, BankAccount.deleted_at.is_(None))
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError(log_context={"bank_account_id": account_id})
        if int(payload["version"]) != row.version:
            raise VersionConflictError(log_context={"bank_account_id": account_id})
        changes: dict[str, str] = {}
        for field in _TEXT_FIELDS:
            if payload.get(field) is not None:
                changes[field] = _clean_text(field, payload[field])
        if payload.get("swift_code") is not None:
            changes["swift_code"] = _clean_swift(payload["swift_code"])
        if "label" in changes and _label_taken(session, changes["label"], exclude_id=row.id):
            raise _invalid("label", "같은 이름의 계좌가 이미 있습니다. 다른 이름을 입력해 주세요.")
        if ({"account_no", "swift_code"} & changes.keys()) and _duplicate_exists(
            session,
            changes.get("account_no", row.account_no),
            changes.get("swift_code", row.swift_code),
            exclude_id=row.id,
        ):
            raise _invalid("account_no", "이미 등록된 계좌입니다(같은 계좌번호·SWIFT).")
        for field, value in changes.items():
            setattr(row, field, value)
        row.updated_by_id = actor.id
        _flush_or_duplicate(session)
        audit.record(
            session,
            action=AuditAction.BANK_ACCOUNT_UPDATED,
            actor_user_id=actor.id,
            entity_type="bank_accounts",
            entity_id=row.id,
            detail={"fields": sorted(changes)},  # 값·전후값은 싣지 않는다
        )
        return _body(row)


def deactivate_bank_account(
    *, actor: AuthenticatedUser, account_id: int, version: int
) -> dict[str, Any]:
    """비활성(soft delete) — 이미 발행된 PI는 은행 스냅샷을 갖고 있어 영향이 없다."""
    with unit_of_work() as uow:
        session = uow.session
        row = session.execute(
            select(BankAccount)
            .where(BankAccount.id == account_id, BankAccount.deleted_at.is_(None))
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError(log_context={"bank_account_id": account_id})
        if version != row.version:
            raise VersionConflictError(log_context={"bank_account_id": account_id})
        row.deleted_at = utcnow()
        row.updated_by_id = actor.id
        session.flush()
        audit.record(
            session,
            action=AuditAction.BANK_ACCOUNT_DEACTIVATED,
            actor_user_id=actor.id,
            entity_type="bank_accounts",
            entity_id=row.id,
            detail={},
        )
        return _body(row)


def get_bank_account(account_id: int) -> dict[str, Any]:
    with unit_of_work() as uow:
        return _body(require_bank_account(uow.session, account_id))


def _conditions(currency: str | None) -> list[Any]:
    conditions: list[Any] = [BankAccount.deleted_at.is_(None)]
    if currency:
        conditions.append(BankAccount.currency == currency.upper())
    return conditions


def list_bank_accounts(
    *, offset: int, limit: int, currency: str | None = None
) -> tuple[list[dict[str, Any]], int]:
    conditions = _conditions(currency)
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(BankAccount).where(*conditions)
        ).scalar_one()
        rows = session.execute(
            select(BankAccount)
            .where(*conditions)
            .order_by(BankAccount.currency, BankAccount.label)
            .offset(offset)
            .limit(limit)
        ).scalars()
        return [_body(row) for row in rows], total


EXPORT_HEADER: tuple[str, ...] = (
    "이름",
    "통화",
    "수취인",
    "수취인 주소",
    "은행명",
    "은행 주소",
    "계좌번호",
    "SWIFT",
    "등록일",
)


def export_rows(*, currency: str | None = None) -> list[tuple[Any, ...]]:
    conditions = _conditions(currency)
    with unit_of_work() as uow:
        session = uow.session
        total = session.execute(
            select(func.count()).select_from(BankAccount).where(*conditions)
        ).scalar_one()
        if total > EXPORT_MAX_ROWS:
            raise _invalid(
                "size",
                f"내보낼 자료가 너무 많습니다({total:,}건). {EXPORT_MAX_ROWS:,}건 이하가 되도록 조건을 좁혀 주세요.",
            )
        rows = session.execute(
            select(BankAccount).where(*conditions).order_by(BankAccount.currency, BankAccount.label)
        ).scalars()
        return [
            (
                r.label,
                r.currency,
                r.beneficiary_name,
                r.beneficiary_address,
                r.bank_name,
                r.bank_address,
                r.account_no,
                r.swift_code,
                to_kst(r.created_at).date().isoformat(),
            )
            for r in rows
        ]
