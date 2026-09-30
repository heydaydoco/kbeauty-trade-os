"""전표 공통 입력 검증 — 증빙일·유효기간·담당자·공백 정리 (S3-1 design-A A10).

QT(L1)와 PI 참조 생성(L2)이 **같은 규칙**을 쓰도록 한 곳에 둔다(조용한 분기 방지).
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError
from app.core.time import today_kst
from app.modules.identity.models import User
from app.modules.trade_docs.constants import MAX_VALIDITY_DAYS


def invalid(field: str, message: str) -> AppError:
    return AppError(ErrorCode.VALIDATION_INVALID_FIELD, detail={field: message})


def check_doc_date(doc_date: date) -> None:
    """증빙일은 미래일 수 없다(소급은 허용 — §21). 기준은 KST 업무일이다."""
    if doc_date > today_kst():
        raise invalid("doc_date", "증빙일은 오늘(KST)보다 미래일 수 없습니다.")


def check_valid_until(valid_until: date | None, doc_date: date) -> None:
    """유효기간은 증빙일 이후·증빙일로부터 365일 이내(오타 방어)."""
    if valid_until is None:
        return
    if valid_until < doc_date:
        raise invalid("valid_until", "유효기간은 증빙일 이후여야 합니다.")
    if valid_until > doc_date + timedelta(days=MAX_VALIDITY_DAYS):
        raise invalid(
            "valid_until", f"유효기간은 증빙일로부터 {MAX_VALIDITY_DAYS}일 이내여야 합니다."
        )


def require_active_user(session: Session, user_id: int, *, field: str = "assignee_id") -> None:
    found = session.execute(
        select(User.id).where(
            User.id == user_id, User.deleted_at.is_(None), User.is_active.is_(True)
        )
    ).scalar_one_or_none()
    if found is None:
        raise invalid(field, "활성 사용자가 아닙니다. 담당자를 다시 선택해 주세요.")


def blank_to_none(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None
