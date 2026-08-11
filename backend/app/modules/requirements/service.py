"""요건 템플릿 서비스 (§5.1·§5.5 / §17.2·§17.4 / ADR-0033·0034·0035 / S2-1 PR-2).

■ 확정 게이트의 서비스 층 (ADR-0033 — 3층 중 ②)

  확정은 **사람 1클릭 + idempotency key** 하나뿐이다(§15 L2 — 자동 확정 경로
  부재는 아키텍처 테스트가 고정한다). 근거 2필드(근거링크·최종확인일)가 없으면
  422로 거부한다(§5.5 / GC-C8). DB CHECK(confirmed_requires_evidence)는
  이 층이 뚫려도 막는 마지막 층이다.

■ 확정 상태의 편집 차단 (판정 조건 11)

  CONFIRMED 템플릿의 내용(헤더·체크리스트·선행요건) 편집은 409로 거부한다 —
  편집 경로는 초안 전환(명시 액션) → 수정 → **재확정**뿐이다. 내용이 바뀌면
  근거의 지시 대상도 바뀌므로, 재확정이 근거-내용 일치를 지킨다.

■ 순환 참조 거부 (조건 1 — ADR-0034)

  자기참조(깊이 1)는 DB CHECK가 막고, 깊이 2 이상은 선행 추가 시점의 그래프
  탐색(_require_no_cycle)이 막는다 — S2-2의 태스크 순서가 위상 정렬 가능한
  그래프를 전제할 수 있어야 한다.

■ 아웃박스 이벤트를 발행하지 않는다 (판정 ⑦ "이벤트 발행 0" — markets 동형)
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, NotFoundError, VersionConflictError
from app.core.money import UnknownCurrencyError, minor_units
from app.core.time import utcnow
from app.modules.catalog.profiles import require_profile
from app.modules.documents.models import DocumentType
from app.modules.documents.service import require_document_type
from app.modules.idempotency import service as idempotency
from app.modules.identity.service import AuthenticatedUser
from app.modules.markets.models import Market
from app.modules.markets.service import require_market
from app.modules.requirements.models import (
    ItemProfileRequirementTemplate,
    RequirementTemplate,
    TemplateChecklistItem,
    TemplatePrerequisite,
)

TEMPLATE_CREATE_ENDPOINT = "POST /api/v1/requirement-templates"
TEMPLATE_CONFIRM_ENDPOINT = "POST /api/v1/requirement-templates/confirm"
CHECKLIST_ADD_ENDPOINT = "POST /api/v1/requirement-templates/checklist"
PREREQUISITE_ADD_ENDPOINT = "POST /api/v1/requirement-templates/prerequisites"
PROFILE_TEMPLATE_ADD_ENDPOINT = "POST /api/v1/item-profiles/requirement-templates"

_TYPE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,39}$")
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")


# ── 뷰 ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RequirementTemplateView:
    id: int
    market_id: int
    market_code: str
    market_name: str
    name: str
    applies_to: str
    requirement_type: str
    validity_months: int | None
    renewal_cycle_months: int | None
    renewal_lead_days: int | None
    estimated_cost_amount: int | None
    estimated_cost_currency: str | None
    source_url: str | None
    last_verified_on: date | None
    status: str
    note: str | None
    #: 편집·확정·초안 전환의 낙관 잠금 토큰(§17.2) — 화면이 그대로 되돌려 준다.
    version: int


@dataclass(frozen=True, slots=True)
class ChecklistItemView:
    id: int
    template_id: int
    seq: int
    item_name: str
    document_type_id: int | None
    document_type_code: str | None
    document_type_name: str | None
    is_required: bool
    note: str | None
    version: int


@dataclass(frozen=True, slots=True)
class PrerequisiteView:
    id: int
    template_id: int
    prerequisite_template_id: int
    prerequisite_name: str
    prerequisite_status: str
    version: int


@dataclass(frozen=True, slots=True)
class ProfileRequirementTemplateView:
    id: int
    item_profile_id: int
    requirement_template_id: int
    template_name: str
    template_status: str
    market_code: str
    version: int


def _template_view(row: RequirementTemplate, market: Market) -> RequirementTemplateView:
    return RequirementTemplateView(
        id=row.id,
        market_id=row.market_id,
        market_code=market.code,
        market_name=market.name_ko,
        name=row.name,
        applies_to=row.applies_to,
        requirement_type=row.requirement_type,
        validity_months=row.validity_months,
        renewal_cycle_months=row.renewal_cycle_months,
        renewal_lead_days=row.renewal_lead_days,
        estimated_cost_amount=row.estimated_cost_amount,
        estimated_cost_currency=row.estimated_cost_currency,
        source_url=row.source_url,
        last_verified_on=row.last_verified_on,
        status=row.status,
        note=row.note,
        version=row.version,
    )


def _serialize(view: RequirementTemplateView) -> dict[str, Any]:
    """멱등 재생 본문 — JSONB에 담기도록 날짜는 ISO 문자열로 얼린다."""
    body = asdict(view)
    if body["last_verified_on"] is not None:
        body["last_verified_on"] = body["last_verified_on"].isoformat()
    return body


def _serialize_checklist(view: ChecklistItemView) -> dict[str, Any]:
    return asdict(view)


def _serialize_prerequisite(view: PrerequisiteView) -> dict[str, Any]:
    return asdict(view)


def _serialize_profile_link(view: ProfileRequirementTemplateView) -> dict[str, Any]:
    return asdict(view)


# ── 공통 ───────────────────────────────────────────────────────────────────


def require_template(
    session: Session, template_id: int, *, for_update: bool = False
) -> RequirementTemplate:
    stmt = select(RequirementTemplate).where(
        RequirementTemplate.id == template_id, RequirementTemplate.deleted_at.is_(None)
    )
    if for_update:
        stmt = stmt.with_for_update()
    row = session.execute(stmt).scalar_one_or_none()
    if row is None:
        raise NotFoundError(log_context={"template_id": template_id})
    return row


def _require_editable(row: RequirementTemplate) -> None:
    """확정 상태의 내용 편집 차단 (조건 11 — 서비스 층. ADR-0033)."""
    if row.status == "CONFIRMED":
        raise AppError(
            ErrorCode.REQUIREMENTS_TEMPLATE_CONFIRMED_LOCKED,
            log_context={"template_id": row.id},
        )


def _require_version(row: RequirementTemplate, expected: Any) -> None:
    if int(expected) != row.version:
        raise VersionConflictError(
            log_context={"template_id": row.id, "expected": expected, "actual": row.version}
        )


def _normalized_type(raw: Any) -> str:
    """유형 코드 정규화 — 트림·대문자('registration'→'REGISTRATION', 시장 코드 선례)."""
    value = str(raw).strip().upper()
    if not _TYPE_PATTERN.fullmatch(value):
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={
                "requirement_type": "요건 유형은 영문 대문자·숫자·밑줄 코드입니다(예: REGISTRATION)."
            },
            log_context={"requirement_type": value},
        )
    return value


def _normalized_cost(payload: dict[str, Any]) -> tuple[int | None, str | None]:
    """사람 표기(12.34)를 정수 최소단위로 (ADR-0003 ④ — parse_credit_limit 선례 꼴).

    자릿수 출처는 서버 통화 목록 하나다(money.minor_units) — 프런트는 표기
    그대로 보내고 환산은 여기서 한다. 쌍 검증·자릿수 초과 안내 포함, DB의
    쌍 CHECK는 마지막 층이다.
    """
    raw = payload.get("estimated_cost")
    currency = payload.get("estimated_cost_currency")
    if raw is None and currency is None:
        return None, None
    if raw is None or currency is None:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={
                "estimated_cost": "예상 비용은 금액과 통화를 함께 입력하거나 함께 비워 주세요."
            },
        )
    code = str(currency).strip().upper()
    if not _CURRENCY_PATTERN.fullmatch(code):
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"estimated_cost_currency": "통화는 영문 3자 코드입니다(예: USD, KRW)."},
            log_context={"estimated_cost_currency": currency},
        )
    try:
        exponent = minor_units(code)
    except UnknownCurrencyError as exc:  # 통화 목록은 money.py가 유일 출처
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"estimated_cost_currency": f"등록되지 않은 통화입니다: {code}"},
            log_context={"estimated_cost_currency": code},
        ) from exc
    try:
        amount = Decimal(str(raw))
    except InvalidOperation as exc:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"estimated_cost": "예상 비용은 숫자여야 합니다."},
        ) from exc
    if amount < 0:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"estimated_cost": "예상 비용은 0 이상이어야 합니다."},
        )
    scaled = amount.scaleb(exponent)
    if scaled != scaled.to_integral_value():
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={
                "estimated_cost": f"{code}는 소수점 {exponent}자리까지 쓸 수 있습니다. "
                "자릿수를 확인해 주세요."
            },
        )
    return int(scaled), code


def _clean_url(raw: Any) -> str | None:
    """근거링크 트림 — 공백만 있으면 없음으로 취급한다(공백 근거는 근거가 아니다)."""
    if raw is None:
        return None
    value = str(raw).strip()
    return value or None


def _apply_content(row: RequirementTemplate, payload: dict[str, Any]) -> None:
    """생성·편집이 공유하는 내용 반영 — 두 경로의 검증이 갈라질 자리를 없앤다."""
    amount, currency = _normalized_cost(payload)
    row.name = str(payload["name"]).strip()
    row.applies_to = payload["applies_to"]
    row.requirement_type = _normalized_type(payload["requirement_type"])
    row.validity_months = payload.get("validity_months")
    row.renewal_cycle_months = payload.get("renewal_cycle_months")
    row.renewal_lead_days = payload.get("renewal_lead_days")
    row.estimated_cost_amount = amount
    row.estimated_cost_currency = currency
    row.source_url = _clean_url(payload.get("source_url"))
    raw_date = payload.get("last_verified_on")
    row.last_verified_on = date.fromisoformat(raw_date) if isinstance(raw_date, str) else raw_date
    row.note = payload.get("note")


def _duplicate_name(exc: IntegrityError, market_id: int, name: Any) -> AppError:
    return AppError(
        ErrorCode.VALIDATION_INVALID_FIELD,
        detail={"name": "같은 시장에 같은 이름의 템플릿이 이미 있습니다. 목록에서 확인해 주세요."},
        log_context={"market_id": market_id, "name": str(name)},
    )


# ── 템플릿 헤더 ─────────────────────────────────────────────────────────────


def create_template(
    *, actor: AuthenticatedUser, idempotency_key: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """등록 — 항상 DRAFT로 시작한다. 확정은 확정 액션 하나뿐이다(ADR-0033)."""
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=TEMPLATE_CREATE_ENDPOINT,
            key=idempotency_key,
            request_body=payload,
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        market = require_market(session, int(payload["market_id"]))
        row = RequirementTemplate(market_id=market.id, created_by_id=actor.id)
        _apply_content(row, payload)
        session.add(row)
        # 실패 경로에서 쓸 값은 flush 전에 빼 둔다(함정 ②).
        market_pk = market.id
        try:
            session.flush()
        except IntegrityError as exc:
            raise _duplicate_name(exc, market_pk, payload.get("name")) from exc

        body = _serialize(_template_view(row, market))
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def update_template(
    *, actor: AuthenticatedUser, template_id: int, payload: dict[str, Any]
) -> RequirementTemplateView:
    """내용 편집 — CONFIRMED면 409(조건 11). 시장 축은 불변이라 받지 않는다."""
    with unit_of_work() as uow:
        session = uow.session
        row = require_template(session, template_id)
        _require_editable(row)
        _require_version(row, payload["version"])
        status = str(payload["status"])
        if status not in ("DRAFT", "RETIRED"):
            # 스키마 Literal이 1차로 막지만, 서비스 단독 호출 경로도 같은 규칙이다.
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={
                    "status": "편집으로는 초안·은퇴만 오갈 수 있습니다. 확정은 확정 버튼으로 해 주세요."
                },
                log_context={"template_id": template_id, "status": status},
            )
        _apply_content(row, payload)
        row.status = status
        row.updated_by_id = actor.id
        market_pk = row.market_id
        try:
            session.flush()
        except IntegrityError as exc:
            raise _duplicate_name(exc, market_pk, payload.get("name")) from exc
        market = session.get(Market, row.market_id)
        assert market is not None
        return _template_view(row, market)


def confirm_template(
    *, actor: AuthenticatedUser, idempotency_key: str, template_id: int, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """확정 — 사람 1클릭 + idempotency key (§15 L2 / §12.2 보강 ⑤ 선례).

    근거 2필드가 없으면 422(§5.5 / GC-C8 — 차단 사유를 필드별로 안내). 확정은
    DRAFT에서만 — RETIRED는 409(재활성=초안 전환 경유, #16 보완 판정). 행 잠금
    (FOR UPDATE)으로 동시 확정을 직렬화한다 — 뒤늦은 쪽은 409를 받는다.
    """
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=TEMPLATE_CONFIRM_ENDPOINT,
            key=idempotency_key,
            request_body={"template_id": template_id, "version": payload["version"]},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        row = require_template(session, template_id, for_update=True)
        if row.status == "CONFIRMED":
            raise AppError(
                ErrorCode.REQUIREMENTS_TEMPLATE_ALREADY_CONFIRMED,
                log_context={"template_id": template_id},
            )
        if row.status != "DRAFT":
            # #16 보완 판정 — 확정은 DRAFT에서만. RETIRED 재활성은 초안 전환
            # (근거 최신성 재검토) 경유가 유일 경로다. 멱등 재생 판정 뒤에 있어
            # 같은 키의 재수신(과거 확정의 재생)은 이 가드에 닿지 않는다.
            raise AppError(
                ErrorCode.REQUIREMENTS_TEMPLATE_NOT_DRAFT,
                log_context={"template_id": template_id, "status": row.status},
            )
        _require_version(row, payload["version"])

        missing: dict[str, str] = {}
        if _clean_url(row.source_url) is None:
            missing["source_url"] = "근거링크가 없습니다."
        if row.last_verified_on is None:
            missing["last_verified_on"] = "최종확인일이 없습니다."
        if missing:
            # GC-C8 — 거부·초안 유지·차단 사유 표시. 트랜잭션 전체가 롤백되므로
            # 상태는 그대로 남는다(초안 유지).
            raise AppError(
                ErrorCode.REQUIREMENTS_TEMPLATE_EVIDENCE_REQUIRED,
                detail=missing,
                log_context={"template_id": template_id, "status": row.status},
            )

        row.status = "CONFIRMED"
        row.updated_by_id = actor.id
        session.flush()

        market = session.get(Market, row.market_id)
        assert market is not None
        body = _serialize(_template_view(row, market))
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=200, body=body)
        return 200, body


def revert_template_to_draft(
    *, actor: AuthenticatedUser, template_id: int, payload: dict[str, Any]
) -> RequirementTemplateView:
    """초안 전환 — 확정 상태 편집의 유일한 입구(조건 11 재확정 경로의 앞단).

    근거 2필드는 그대로 남는다 — 전환은 상태만 되돌리고, 수정분은 재확정을
    거친다. CONFIRMED가 아니면 422(전환할 확정이 없다).
    """
    with unit_of_work() as uow:
        session = uow.session
        row = require_template(session, template_id, for_update=True)
        if row.status != "CONFIRMED":
            raise AppError(
                ErrorCode.REQUIREMENTS_TEMPLATE_NOT_CONFIRMED,
                log_context={"template_id": template_id, "status": row.status},
            )
        _require_version(row, payload["version"])
        row.status = "DRAFT"
        row.updated_by_id = actor.id
        session.flush()
        market = session.get(Market, row.market_id)
        assert market is not None
        return _template_view(row, market)


def list_templates(
    *,
    market_id: int | None,
    status: str | None,
    offset: int,
    limit: int,
) -> tuple[list[RequirementTemplateView], int]:
    with unit_of_work() as uow:
        session = uow.session
        conditions: list[ColumnElement[bool]] = [RequirementTemplate.deleted_at.is_(None)]
        if market_id is not None:
            conditions.append(RequirementTemplate.market_id == market_id)
        if status is not None:
            conditions.append(RequirementTemplate.status == status)
        total = session.execute(
            select(func.count()).select_from(RequirementTemplate).where(*conditions)
        ).scalar_one()
        rows = session.execute(
            select(RequirementTemplate, Market)
            .join(Market, RequirementTemplate.market_id == Market.id)
            .where(*conditions)
            .order_by(Market.code, RequirementTemplate.name)
            .offset(offset)
            .limit(limit)
        ).all()
        return [_template_view(row, market) for row, market in rows], total


def get_template(template_id: int) -> RequirementTemplateView:
    with unit_of_work() as uow:
        session = uow.session
        row = require_template(session, template_id)
        market = session.get(Market, row.market_id)
        assert market is not None
        return _template_view(row, market)


# ── 체크리스트 (§5.1 — S2-2 참조 복사의 원천) ──────────────────────────────


def _checklist_view(row: TemplateChecklistItem, doc_type: DocumentType | None) -> ChecklistItemView:
    return ChecklistItemView(
        id=row.id,
        template_id=row.template_id,
        seq=row.seq,
        item_name=row.item_name,
        document_type_id=row.document_type_id,
        document_type_code=doc_type.code if doc_type else None,
        document_type_name=doc_type.name_ko if doc_type else None,
        is_required=row.is_required,
        note=row.note,
        version=row.version,
    )


def list_checklist(
    *, template_id: int, offset: int, limit: int
) -> tuple[list[ChecklistItemView], int]:
    with unit_of_work() as uow:
        session = uow.session
        require_template(session, template_id)
        conditions = (
            TemplateChecklistItem.template_id == template_id,
            TemplateChecklistItem.deleted_at.is_(None),
        )
        total = session.execute(
            select(func.count()).select_from(TemplateChecklistItem).where(*conditions)
        ).scalar_one()
        rows = session.execute(
            select(TemplateChecklistItem, DocumentType)
            .outerjoin(DocumentType, TemplateChecklistItem.document_type_id == DocumentType.id)
            .where(*conditions)
            .order_by(TemplateChecklistItem.seq)
            .offset(offset)
            .limit(limit)
        ).all()
        return [_checklist_view(row, doc_type) for row, doc_type in rows], total


def add_checklist_item(
    *, actor: AuthenticatedUser, idempotency_key: str, template_id: int, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=CHECKLIST_ADD_ENDPOINT,
            key=idempotency_key,
            request_body={**payload, "template_id": template_id},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        template = require_template(session, template_id)
        _require_editable(template)  # 체크리스트도 템플릿의 내용이다(조건 11)
        doc_type = (
            require_document_type(session, str(payload["document_type"]))
            if payload.get("document_type")
            else None
        )
        row = TemplateChecklistItem(
            template_id=template_id,
            seq=int(payload["seq"]),
            item_name=str(payload["item_name"]).strip(),
            document_type_id=doc_type.id if doc_type else None,
            is_required=bool(payload["is_required"]),
            note=payload.get("note"),
            created_by_id=actor.id,
        )
        session.add(row)
        seq = row.seq
        try:
            session.flush()
        except IntegrityError as exc:
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={"seq": f"이 템플릿에 이미 있는 순번입니다: {seq}."},
                log_context={"template_id": template_id, "seq": seq},
            ) from exc

        body = _serialize_checklist(_checklist_view(row, doc_type))
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def update_checklist_item(
    *, actor: AuthenticatedUser, template_id: int, item_id: int, payload: dict[str, Any]
) -> ChecklistItemView:
    with unit_of_work() as uow:
        session = uow.session
        template = require_template(session, template_id)
        _require_editable(template)
        row = session.execute(
            select(TemplateChecklistItem).where(
                TemplateChecklistItem.id == item_id,
                TemplateChecklistItem.template_id == template_id,
                TemplateChecklistItem.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError(log_context={"template_id": template_id, "item_id": item_id})
        if int(payload["version"]) != row.version:
            raise VersionConflictError(
                log_context={"item_id": item_id, "expected": payload["version"]}
            )
        doc_type = (
            require_document_type(session, str(payload["document_type"]))
            if payload.get("document_type")
            else None
        )
        row.seq = int(payload["seq"])
        row.item_name = str(payload["item_name"]).strip()
        row.document_type_id = doc_type.id if doc_type else None
        row.is_required = bool(payload["is_required"])
        row.note = payload.get("note")
        row.updated_by_id = actor.id
        seq = row.seq
        try:
            session.flush()
        except IntegrityError as exc:
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={"seq": f"이 템플릿에 이미 있는 순번입니다: {seq}."},
                log_context={"template_id": template_id, "seq": seq},
            ) from exc
        return _checklist_view(row, doc_type)


def remove_checklist_item(*, actor: AuthenticatedUser, template_id: int, item_id: int) -> None:
    """항목 제거 = soft delete. 재추가는 부활이 아니라 신규다(§17.4)."""
    with unit_of_work() as uow:
        session = uow.session
        template = require_template(session, template_id)
        _require_editable(template)
        row = session.execute(
            select(TemplateChecklistItem).where(
                TemplateChecklistItem.id == item_id,
                TemplateChecklistItem.template_id == template_id,
                TemplateChecklistItem.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError(log_context={"template_id": template_id, "item_id": item_id})
        row.deleted_at = utcnow()
        row.updated_by_id = actor.id


# ── 선행요건 (조건 1 — ADR-0034) ────────────────────────────────────────────


def _require_no_cycle(session: Session, *, template_id: int, new_prerequisite_id: int) -> None:
    """새 선행(template→prerequisite)이 순환을 만들면 422 (조건 1 서비스 층).

    새 선행요건에서 출발해 활성 선행 그래프를 따라간다 — template_id에 닿으면
    "선행의 선행이 나"라는 뜻이다. 자기참조(깊이 1)도 첫 판정에서 잡힌다
    (DB CHECK no_self_reference는 그 아래의 층).
    """
    frontier = {new_prerequisite_id}
    seen: set[int] = set()
    while frontier:
        if template_id in frontier:
            raise AppError(
                ErrorCode.REQUIREMENTS_PREREQUISITE_CYCLE,
                log_context={
                    "template_id": template_id,
                    "prerequisite_template_id": new_prerequisite_id,
                },
            )
        seen |= frontier
        rows = (
            session.execute(
                select(TemplatePrerequisite.prerequisite_template_id).where(
                    TemplatePrerequisite.template_id.in_(frontier),
                    TemplatePrerequisite.deleted_at.is_(None),
                )
            )
            .scalars()
            .all()
        )
        frontier = set(rows) - seen


def list_prerequisites(
    *, template_id: int, offset: int, limit: int
) -> tuple[list[PrerequisiteView], int]:
    with unit_of_work() as uow:
        session = uow.session
        require_template(session, template_id)
        conditions = (
            TemplatePrerequisite.template_id == template_id,
            TemplatePrerequisite.deleted_at.is_(None),
        )
        total = session.execute(
            select(func.count()).select_from(TemplatePrerequisite).where(*conditions)
        ).scalar_one()
        rows = session.execute(
            select(TemplatePrerequisite, RequirementTemplate)
            .join(
                RequirementTemplate,
                TemplatePrerequisite.prerequisite_template_id == RequirementTemplate.id,
            )
            .where(*conditions)
            .order_by(RequirementTemplate.name)
            .offset(offset)
            .limit(limit)
        ).all()
        return [
            PrerequisiteView(
                id=row.id,
                template_id=row.template_id,
                prerequisite_template_id=row.prerequisite_template_id,
                prerequisite_name=prereq.name,
                prerequisite_status=prereq.status,
                version=row.version,
            )
            for row, prereq in rows
        ], total


def add_prerequisite(
    *, actor: AuthenticatedUser, idempotency_key: str, template_id: int, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=PREREQUISITE_ADD_ENDPOINT,
            key=idempotency_key,
            request_body={**payload, "template_id": template_id},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        template = require_template(session, template_id)
        _require_editable(template)  # 선행요건도 템플릿의 내용이다(조건 11)
        prerequisite_id = int(payload["prerequisite_template_id"])
        prereq = require_template(session, prerequisite_id)
        _require_no_cycle(session, template_id=template_id, new_prerequisite_id=prerequisite_id)

        row = TemplatePrerequisite(
            template_id=template_id,
            prerequisite_template_id=prerequisite_id,
            created_by_id=actor.id,
        )
        session.add(row)
        prereq_name = prereq.name
        prereq_status = prereq.status
        try:
            session.flush()
        except IntegrityError as exc:
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={"prerequisite_template_id": "이미 등록된 선행요건입니다."},
                log_context={"template_id": template_id, "prerequisite": prerequisite_id},
            ) from exc

        body = _serialize_prerequisite(
            PrerequisiteView(
                id=row.id,
                template_id=template_id,
                prerequisite_template_id=prerequisite_id,
                prerequisite_name=prereq_name,
                prerequisite_status=prereq_status,
                version=row.version,
            )
        )
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def remove_prerequisite(*, actor: AuthenticatedUser, template_id: int, link_id: int) -> None:
    """선행 해제 = soft delete. 재추가는 부활이 아니라 신규다(§17.4)."""
    with unit_of_work() as uow:
        session = uow.session
        template = require_template(session, template_id)
        _require_editable(template)
        row = session.execute(
            select(TemplatePrerequisite).where(
                TemplatePrerequisite.id == link_id,
                TemplatePrerequisite.template_id == template_id,
                TemplatePrerequisite.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError(log_context={"template_id": template_id, "link_id": link_id})
        row.deleted_at = utcnow()
        row.updated_by_id = actor.id


# ── 품목군 요건 세트 (§4.8 — ADR-0035, 부채 #15 요건 몫) ────────────────────


def list_profile_requirement_templates(
    *, profile_id: int, offset: int, limit: int
) -> tuple[list[ProfileRequirementTemplateView], int]:
    with unit_of_work() as uow:
        session = uow.session
        require_profile(session, profile_id)
        conditions = (
            ItemProfileRequirementTemplate.item_profile_id == profile_id,
            ItemProfileRequirementTemplate.deleted_at.is_(None),
        )
        total = session.execute(
            select(func.count()).select_from(ItemProfileRequirementTemplate).where(*conditions)
        ).scalar_one()
        rows = session.execute(
            select(ItemProfileRequirementTemplate, RequirementTemplate, Market)
            .join(
                RequirementTemplate,
                ItemProfileRequirementTemplate.requirement_template_id == RequirementTemplate.id,
            )
            .join(Market, RequirementTemplate.market_id == Market.id)
            .where(*conditions)
            .order_by(Market.code, RequirementTemplate.name)
            .offset(offset)
            .limit(limit)
        ).all()
        return [
            ProfileRequirementTemplateView(
                id=row.id,
                item_profile_id=row.item_profile_id,
                requirement_template_id=row.requirement_template_id,
                template_name=template.name,
                template_status=template.status,
                market_code=market.code,
                version=row.version,
            )
            for row, template, market in rows
        ], total


def add_profile_requirement_template(
    *, actor: AuthenticatedUser, idempotency_key: str, profile_id: int, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """세트에 요건 추가 — 확정 템플릿도 연결 가능하다(세트 구성은 프로파일의
    내용이지 템플릿의 내용이 아니라 조건 11 편집 차단 비대상)."""
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=PROFILE_TEMPLATE_ADD_ENDPOINT,
            key=idempotency_key,
            request_body={**payload, "profile_id": profile_id},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        require_profile(session, profile_id)
        template_id = int(payload["requirement_template_id"])
        template = require_template(session, template_id)
        market = session.get(Market, template.market_id)
        assert market is not None

        row = ItemProfileRequirementTemplate(
            item_profile_id=profile_id,
            requirement_template_id=template_id,
            created_by_id=actor.id,
        )
        session.add(row)
        template_name = template.name
        template_status = template.status
        market_code = market.code
        try:
            session.flush()
        except IntegrityError as exc:
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={
                    "requirement_template_id": "이 품목군의 요건 세트에 이미 있는 템플릿입니다."
                },
                log_context={"profile_id": profile_id, "template_id": template_id},
            ) from exc

        body = _serialize_profile_link(
            ProfileRequirementTemplateView(
                id=row.id,
                item_profile_id=profile_id,
                requirement_template_id=template_id,
                template_name=template_name,
                template_status=template_status,
                market_code=market_code,
                version=row.version,
            )
        )
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def remove_profile_requirement_template(
    *, actor: AuthenticatedUser, profile_id: int, link_id: int
) -> None:
    """세트에서 요건 제거 = soft delete. 재추가는 부활이 아니라 신규다(§17.4)."""
    with unit_of_work() as uow:
        session = uow.session
        require_profile(session, profile_id)
        row = session.execute(
            select(ItemProfileRequirementTemplate).where(
                ItemProfileRequirementTemplate.id == link_id,
                ItemProfileRequirementTemplate.item_profile_id == profile_id,
                ItemProfileRequirementTemplate.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError(log_context={"profile_id": profile_id, "link_id": link_id})
        row.deleted_at = utcnow()
        row.updated_by_id = actor.id
