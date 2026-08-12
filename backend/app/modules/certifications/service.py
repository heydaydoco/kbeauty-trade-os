"""인증 인스턴스 서비스 (§5.1·§5.2 / §17.1·17.2·17.4 / s2-2-plan.md §0-1 — S2-2 PR-1).

■ 상태 대입의 단일 통로 (층2 — 아키텍처 테스트가 고정)

  row.status를 바꾸는 코드는 이 파일의 _apply_transition() 하나뿐이다.
  사람 전이(transition_certification)는 HUMAN_TRANSITIONS만, 자동 수렴
  (_converge — 날짜 스윕·조건 A 즉시 수렴)은 AUTO_TRANSITIONS만 통과시킨다.
  생성은 NOT_STARTED 고정, PATCH 스키마에는 status 필드가 구조적으로 없다.

■ 달력 즉시 수렴 (판정 조건 A)

  달력 파생 3태(APPROVED·EXPIRING·EXPIRED)로 들어가는 전이(6·10)와 만료일
  정정 PATCH 직후, 같은 트랜잭션 말미에 해당 행 한정 수렴을 호출한다 —
  커밋 시점에 저장 상태와 달력이 항상 정합하고, "정정 후 다음 스윕까지
  거짓 상태" 창이 없다. 수렴 전이도 이력·이벤트를 남긴다(actor NULL).

■ 아웃박스 발행 (판정 안건 ⑩ — 발행 채택)

  생성+전이 전건(사람·자동 공히)을 outbox.publish로 기록한다 — S2-3 기일·
  알림 엔진의 원료다. 발송은 없다(디스패처=S2-3, 부채 #11 의도 상태).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, NotFoundError, VersionConflictError
from app.core.time import today_kst, utcnow
from app.modules.catalog.models import Product, Sku
from app.modules.certifications.machine import (
    AUTO_TRANSITIONS,
    DATE_DERIVED_STATUSES,
    HUMAN_TRANSITIONS,
    REASON_REQUIRED_TO,
    TERMINAL_STATUSES,
    TRANSITION_OPTIONAL_FIELDS,
    TRANSITION_REQUIRED_FIELDS,
)
from app.modules.certifications.models import (
    Certification,
    CertificationStatusLog,
    CertificationTask,
)
from app.modules.documents.models import Document
from app.modules.idempotency import service as idempotency
from app.modules.identity.models import User
from app.modules.identity.service import AuthenticatedUser
from app.modules.ingredients.models import Ingredient
from app.modules.markets.models import Market
from app.modules.outbox import service as outbox
from app.modules.partners.models import Partner
from app.modules.requirements.models import (
    ItemProfileRequirementTemplate,
    RequirementTemplate,
    TemplateChecklistItem,
)

CERTIFICATION_CREATE_ENDPOINT = "POST /api/v1/certifications"
CERTIFICATION_TRANSITION_ENDPOINT = "POST /api/v1/certifications/transitions"
TASK_ADD_ENDPOINT = "POST /api/v1/certifications/tasks"

#: 폴리모픽 대상의 실체 매핑 (판정 안건 ② — FACILITY→partners는 잠정,
#: COMPANY→자사 단일이라 실체 조회가 없다). 실 FK가 없어 이 검증이 그 자리다.
_TARGET_MODELS: dict[str, type[Any]] = {
    "PRODUCT": Product,
    "SKU": Sku,
    "INGREDIENT": Ingredient,
    "FACILITY": Partner,
}

#: 자동 전이의 사유 자동 기록 (조건 B — reason CHECK 3종을 이 문구가 채운다).
_AUTO_REASONS: dict[str, str] = {
    "EXPIRING": "만료일 임박(자동 — 리드타임 도달)",
    "EXPIRED": "만료일 도과(자동)",
    "APPROVED": "만료일 정정으로 조건 해소(자동 복귀)",
}


# ── 뷰 ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CertificationView:
    id: int
    template_id: int
    market_code: str
    template_name: str
    requirement_type: str
    target_type: str
    target_id: int | None
    target_label: str
    status: str
    validity_months: int | None
    renewal_cycle_months: int | None
    renewal_lead_days: int | None
    source_url: str | None
    last_verified_on: date | None
    cert_number: str | None
    applied_on: date | None
    approved_on: date | None
    valid_from: date | None
    expires_on: date | None
    assignee_id: int | None
    note: str | None
    version: int


@dataclass(frozen=True, slots=True)
class TaskView:
    id: int
    certification_id: int
    seq: int
    item_name: str
    document_type_id: int | None
    is_required: bool
    done: bool
    done_at: str | None
    document_id: int | None
    note: str | None
    version: int


@dataclass(frozen=True, slots=True)
class StatusLogView:
    id: int
    certification_id: int
    occurred_at: str
    from_status: str
    to_status: str
    reason: str | None
    actor_user_id: int | None
    expires_on_snapshot: date | None
    cert_number_snapshot: str | None


def _target_label(session: Session, target_type: str, target_id: int | None) -> str:
    """대상의 사람용 표기 — COMPANY는 자사 단일이라 고정 문구다."""
    if target_type == "COMPANY":
        return "자사(기업 단위)"
    model = _TARGET_MODELS[target_type]
    row = session.get(model, target_id)
    if row is None:  # soft delete 이후에도 라벨은 남긴다 — 표기 실패는 아니다
        return f"{target_type}#{target_id}"
    return str(getattr(row, "name_ko", None) or f"{target_type}#{target_id}")


def _certification_view(
    session: Session, row: Certification, market_code: str
) -> CertificationView:
    return CertificationView(
        id=row.id,
        template_id=row.template_id,
        market_code=market_code,
        template_name=row.template_name,
        requirement_type=row.requirement_type,
        target_type=row.target_type,
        target_id=row.target_id,
        target_label=_target_label(session, row.target_type, row.target_id),
        status=row.status,
        validity_months=row.validity_months,
        renewal_cycle_months=row.renewal_cycle_months,
        renewal_lead_days=row.renewal_lead_days,
        source_url=row.source_url,
        last_verified_on=row.last_verified_on,
        cert_number=row.cert_number,
        applied_on=row.applied_on,
        approved_on=row.approved_on,
        valid_from=row.valid_from,
        expires_on=row.expires_on,
        assignee_id=row.assignee_id,
        note=row.note,
        version=row.version,
    )


def _market_code(session: Session, template_id: int) -> str:
    code = session.execute(
        select(Market.code)
        .join(RequirementTemplate, RequirementTemplate.market_id == Market.id)
        .where(RequirementTemplate.id == template_id)
    ).scalar_one()
    return str(code)


def _serialize(view: CertificationView) -> dict[str, Any]:
    """멱등 재생 본문 — JSONB에 담기도록 날짜는 ISO 문자열로 얼린다."""
    body = asdict(view)
    for field in ("last_verified_on", "applied_on", "approved_on", "valid_from", "expires_on"):
        if body[field] is not None:
            body[field] = body[field].isoformat()
    return body


def _task_view(row: CertificationTask) -> TaskView:
    return TaskView(
        id=row.id,
        certification_id=row.certification_id,
        seq=row.seq,
        item_name=row.item_name,
        document_type_id=row.document_type_id,
        is_required=row.is_required,
        done=row.done,
        done_at=row.done_at.isoformat() if row.done_at else None,
        document_id=row.document_id,
        note=row.note,
        version=row.version,
    )


# ── 공통 ───────────────────────────────────────────────────────────────────


def require_certification(
    session: Session, certification_id: int, *, for_update: bool = False
) -> Certification:
    stmt = select(Certification).where(
        Certification.id == certification_id, Certification.deleted_at.is_(None)
    )
    if for_update:
        stmt = stmt.with_for_update()
    row = session.execute(stmt).scalar_one_or_none()
    if row is None:
        raise NotFoundError(log_context={"certification_id": certification_id})
    return row


def _require_version(row: Certification, expected: Any) -> None:
    if int(expected) != row.version:
        raise VersionConflictError(
            log_context={"certification_id": row.id, "expected": expected, "actual": row.version}
        )


def _as_date(raw: Any) -> date | None:
    if raw is None or isinstance(raw, date):
        return raw
    return date.fromisoformat(str(raw))


def _require_target(session: Session, target_type: str, target_id: int | None) -> None:
    """폴리모픽 대상의 존재 검증 — 실 FK의 대역 (판정 안건 ②)."""
    if target_type == "COMPANY":
        if target_id is not None:
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={"target_id": "기업 단위 인증은 대상을 지정하지 않습니다(자사 단일)."},
            )
        return
    if target_id is None:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"target_id": "인증 대상을 선택해 주세요."},
        )
    model = _TARGET_MODELS[target_type]
    row = session.execute(
        select(model.id).where(model.id == target_id, model.deleted_at.is_(None))
    ).scalar_one_or_none()
    if row is None:
        raise AppError(
            ErrorCode.CERTIFICATIONS_TARGET_NOT_FOUND,
            log_context={"target_type": target_type, "target_id": target_id},
        )


# ── 전이 — 상태 대입의 단일 통로 (층2) ──────────────────────────────────────


def _record_transition(
    session: Session,
    row: Certification,
    *,
    to_status: str,
    actor_user_id: int | None,
    reason: str | None,
) -> None:
    """상태 대입 + 이력 자동 + 아웃박스 발행 — 셋은 한 트랜잭션의 한 동작이다."""
    from_status = row.status
    row.status = to_status
    session.add(
        CertificationStatusLog(
            certification_id=row.id,
            from_status=from_status,
            to_status=to_status,
            reason=reason,
            actor_user_id=actor_user_id,
            # 전이 시점의 본체 값 동결 — 재승인 덮어쓰기의 "그때 값" 재구성(§3).
            expires_on_snapshot=row.expires_on,
            cert_number_snapshot=row.cert_number,
        )
    )
    outbox.publish(
        session,
        event_type="certifications.certification.status_changed",
        aggregate_type="certifications",
        aggregate_id=row.id,
        payload={
            "certification_id": row.id,
            "template_id": row.template_id,
            "target_type": row.target_type,
            "target_id": row.target_id,
            "from_status": from_status,
            "to_status": to_status,
            "automatic": actor_user_id is None,
        },
    )


def _derived_date_status(row: Certification, base_date: date) -> str:
    """달력 파생 3태의 정답 — 만료일·리드타임의 순수 함수 (§5.2 자동 부여).

    만료일이 없으면 무기한이라 APPROVED가 정답이고, 리드가 없으면 임박 단계가
    없다(임의 기본값 발명 금지 — 필요하면 템플릿에 리드타임을 입력하는 것이
    정공법).
    """
    if row.expires_on is None:
        return "APPROVED"
    if base_date > row.expires_on:
        return "EXPIRED"
    if row.renewal_lead_days is not None and base_date >= row.expires_on - timedelta(
        days=row.renewal_lead_days
    ):
        return "EXPIRING"
    return "APPROVED"


def _converge(session: Session, row: Certification, *, base_date: date | None = None) -> str | None:
    """달력 파생 3태 안의 행을 정답 상태로 수렴시킨다 (조건 A / 날짜 스윕 코어).

    출발·도달 모두 3태 한정이다 — RENEWING·진행 상태·종결 2태는 건드리지
    않는다(층3이 고정). 수렴 전이는 AUTO_TRANSITIONS 검증을 거친다.
    돌아오는 값은 수렴이 일어났을 때의 도달 상태, 아니면 None.
    """
    if row.status not in DATE_DERIVED_STATUSES:
        return None
    target = _derived_date_status(row, base_date or today_kst())
    if target == row.status:
        return None
    pair = (row.status, target)
    # 3태 상호 6방향은 전부 허용이라 이 단언은 항상 참이어야 한다 — 깨지면
    # machine.py와 이 함수의 정의가 어긋난 것이다(조용히 넘기지 않는다).
    if pair not in AUTO_TRANSITIONS:  # pragma: no cover — 정의 어긋남 방어
        raise AssertionError(f"수렴 전이가 자동 전이 표 밖입니다: {pair}")
    _record_transition(
        session, row, to_status=target, actor_user_id=None, reason=_AUTO_REASONS[target]
    )
    return target


def sweep_date_transitions(*, base_date: date | None = None) -> dict[str, int]:
    """날짜 스윕 — 달력 파생 3태 전 행을 정답 상태로 수렴시키는 일괄 처리 함수.

    WBS S2-2 DoD "만료임박 날짜 조건 자동 부여 배치"의 충족 형태다(판정 ⑦ —
    배치=일괄 처리 함수의 실재+C 그룹 직접 호출 검증). S2-3 실행기가 이 함수를
    `scheduled_jobs`의 `certification-sweep`(daily@06:00)으로 **등록만** 했다 —
    함수는 그대로다(ADR-0039 문면). 실행 수단은 스케줄 + CLI 수동 호출 둘이다.

    건별 독립 트랜잭션(§17.6 — 한 건의 실패가 스윕 전체를 막지 않는다)·
    행 잠금 후 상태 재확인(§17.2 확인→기록 — 사람 전이와의 경합 직렬화:
    잠그는 사이 갱신중·종결로 옮겨 간 행은 _converge가 건너뛴다)·
    멱등(재실행 변화 0 — §20 J). 무기한(expires_on NULL)은 후보 밖이다.
    """
    effective = base_date or today_kst()
    with unit_of_work() as uow:
        candidate_ids = list(
            uow.session.execute(
                select(Certification.id)
                .where(
                    Certification.status.in_(DATE_DERIVED_STATUSES),
                    Certification.deleted_at.is_(None),
                    Certification.expires_on.is_not(None),
                )
                .order_by(Certification.id)
            ).scalars()
        )
    counts: dict[str, int] = {"scanned": len(candidate_ids), "converged": 0}
    for certification_id in candidate_ids:
        with unit_of_work() as uow:
            session = uow.session
            row = session.execute(
                select(Certification)
                .where(Certification.id == certification_id, Certification.deleted_at.is_(None))
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:  # 후보 수집 후 삭제된 행 — 건너뛴다
                continue
            before = row.status
            target = _converge(session, row, base_date=effective)
            if target is not None:
                counts["converged"] += 1
                key = f"{before}->{target}"
                counts[key] = counts.get(key, 0) + 1
    return counts


def transition_certification(
    *,
    actor: AuthenticatedUser,
    idempotency_key: str,
    certification_id: int,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    """사람 전이 — 허용 21방향 밖은 409, 사유 필수 전이의 사유 결여는 422.

    부속 데이터(신청일·승인일·유효시작·만료일·인증번호)는 같은 요청·같은
    트랜잭션에 싣는다(§17.1). 행 잠금(FOR UPDATE)으로 동시 전이를 직렬화하고
    (§17.2 확인→기록), 도달이 달력 파생 상태면 즉시 수렴한다(조건 A).
    """
    to_status = str(payload["to"])
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=CERTIFICATION_TRANSITION_ENDPOINT,
            key=idempotency_key,
            request_body={
                "certification_id": certification_id,
                "to": to_status,
                "version": payload["version"],
            },
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        row = require_certification(session, certification_id, for_update=True)
        _require_version(row, payload["version"])

        pair = (row.status, to_status)
        if pair not in HUMAN_TRANSITIONS:
            raise AppError(
                ErrorCode.CERTIFICATIONS_TRANSITION_NOT_ALLOWED,
                detail={"from": row.status, "to": to_status},
                log_context={"certification_id": certification_id, "pair": list(pair)},
            )

        reason_raw = payload.get("reason")
        reason = str(reason_raw).strip() if reason_raw is not None else None
        if to_status in REASON_REQUIRED_TO and not reason:
            raise AppError(
                ErrorCode.CERTIFICATIONS_TRANSITION_REASON_REQUIRED,
                detail={"to": to_status},
                log_context={"certification_id": certification_id, "to": to_status},
            )

        # 부속 데이터 — 필수 결여는 422, 허용 밖 필드는 무시가 아니라 거부한다
        # (조용한 무시는 "넣었는데 안 들어간" 사고를 만든다).
        required = TRANSITION_REQUIRED_FIELDS.get(pair, frozenset())
        optional = TRANSITION_OPTIONAL_FIELDS.get(pair, frozenset())
        supplied = {
            k: payload[k]
            for k in ("applied_on", "approved_on", "valid_from", "expires_on", "cert_number")
            if payload.get(k) is not None
        }
        missing = sorted(required - supplied.keys())
        if missing:
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail=dict.fromkeys(missing, "이 전이에 필요한 값입니다."),
                log_context={"certification_id": certification_id, "pair": list(pair)},
            )
        stray = sorted(supplied.keys() - (required | optional))
        if stray:
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail=dict.fromkeys(stray, "이 전이에서 받지 않는 값입니다."),
                log_context={"certification_id": certification_id, "pair": list(pair)},
            )
        for field in ("applied_on", "approved_on", "valid_from", "expires_on"):
            if field in supplied:
                setattr(row, field, _as_date(supplied[field]))
        if "cert_number" in supplied:
            cert_number = str(supplied["cert_number"]).strip()
            if not cert_number:
                raise AppError(
                    ErrorCode.VALIDATION_INVALID_FIELD,
                    detail={"cert_number": "인증번호가 비어 있습니다."},
                )
            row.cert_number = cert_number

        _record_transition(session, row, to_status=to_status, actor_user_id=actor.id, reason=reason)
        row.updated_by_id = actor.id
        # 조건 A — 달력 파생 상태로 들어왔다면 같은 트랜잭션 말미에 즉시 수렴.
        _converge(session, row)
        session.flush()

        body = _serialize(_certification_view(session, row, _market_code(session, row.template_id)))
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=200, body=body)
        return 200, body


# ── 생성 (CONFIRMED 게이트 + 스냅샷 + 태스크 참조 복사) ─────────────────────


def _instantiate_template(
    session: Session,
    *,
    template: RequirementTemplate,
    target_type: str,
    target_id: int | None,
    actor_user_id: int,
    assignee_id: int | None = None,
    note: str | None = None,
    automatic: bool = False,
) -> Certification:
    """스냅샷 행 + 체크리스트→태스크 참조 복사 + 아웃박스 발행 — 생성의 공유 코어.

    API 생성과 §4.8 자동 적용(S2-2 PR-2)이 공유한다. 게이트(CONFIRMED·
    applies_to 일치·대상 실재)와 활성 유니크 충돌의 처리(API=422 변환 /
    자동 적용=사전 존재 확인으로 생략)는 호출자 몫 — flush의 IntegrityError를
    그대로 올린다. 상태는 server_default NOT_STARTED뿐(상태 대입 없음 — 층2).
    """
    row = Certification(
        template_id=template.id,
        target_type=target_type,
        target_id=target_id,
        # 스냅샷 7필드 — "그때 요건"의 동결 (estimated_cost는 명시 예외).
        template_name=template.name,
        requirement_type=template.requirement_type,
        validity_months=template.validity_months,
        renewal_cycle_months=template.renewal_cycle_months,
        renewal_lead_days=template.renewal_lead_days,
        source_url=template.source_url,
        last_verified_on=template.last_verified_on,
        assignee_id=assignee_id,
        note=note,
        created_by_id=actor_user_id,
    )
    session.add(row)
    session.flush()

    # 체크리스트 → 태스크 참조 복사 (ADR-05 — [M2] 보강 S2-1 PR-2 ④ 지시분).
    items = (
        session.execute(
            select(TemplateChecklistItem)
            .where(
                TemplateChecklistItem.template_id == template.id,
                TemplateChecklistItem.deleted_at.is_(None),
            )
            .order_by(TemplateChecklistItem.seq)
        )
        .scalars()
        .all()
    )
    for item in items:
        session.add(
            CertificationTask(
                certification_id=row.id,
                seq=item.seq,
                item_name=item.item_name,
                document_type_id=item.document_type_id,
                is_required=item.is_required,
                created_by_id=actor_user_id,
            )
        )

    outbox.publish(
        session,
        event_type="certifications.certification.created",
        aggregate_type="certifications",
        aggregate_id=row.id,
        payload={
            "certification_id": row.id,
            "template_id": template.id,
            "target_type": target_type,
            "target_id": target_id,
            "task_count": len(items),
            # 전이 이벤트의 automatic 표식(판정 ⑩)과 대칭 — §4.8 자동 생성분 구분.
            "automatic": automatic,
        },
    )
    return row


def apply_item_profile_requirements(
    session: Session,
    *,
    item_profile_id: int,
    target_type: str,
    target_id: int,
    actor: AuthenticatedUser,
) -> list[int]:
    """§4.8 "신규 등록 시 자동 적용" — 등록 트랜잭션에 합류해 호출된다 (안건 ⑥).

    품목군 요건 세트 중 **CONFIRMED이면서 적용단위가 등록 대상과 일치**하는
    템플릿 전건에 미착수 인스턴스+태스크를 만든다(제품→PRODUCT, SKU→SKU).
    멱등 = 활성 한정 유니크 술어의 **사전 존재 확인**(기존재 시 생성 생략) —
    예외 경로(IntegrityError→422)로 잡으면 합류한 등록 트랜잭션이 오염된다.
    소급 3계열(품목군 사후 지정·세트 추가 소급·FACILITY/COMPANY/INGREDIENT)은
    비포함 — 수동 등록 경로가 흡수(관찰 원장 등재분). 생성 인스턴스의
    행위자는 등록 조작자다(자동 표식은 아웃박스 payload의 automatic).
    """
    templates = (
        session.execute(
            select(RequirementTemplate)
            .join(
                ItemProfileRequirementTemplate,
                ItemProfileRequirementTemplate.requirement_template_id == RequirementTemplate.id,
            )
            .where(
                ItemProfileRequirementTemplate.item_profile_id == item_profile_id,
                ItemProfileRequirementTemplate.deleted_at.is_(None),
                RequirementTemplate.deleted_at.is_(None),
                RequirementTemplate.status == "CONFIRMED",
                RequirementTemplate.applies_to == target_type,
            )
            .order_by(RequirementTemplate.id)
        )
        .scalars()
        .all()
    )
    created: list[int] = []
    for template in templates:
        # 활성 한정 유니크 술어와 같은 조건(deleted_at IS NULL + 종결 2태 제외).
        existing = session.execute(
            select(Certification.id).where(
                Certification.template_id == template.id,
                Certification.target_type == target_type,
                Certification.target_id == target_id,
                Certification.deleted_at.is_(None),
                Certification.status.notin_(sorted(TERMINAL_STATUSES)),
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue
        row = _instantiate_template(
            session,
            template=template,
            target_type=target_type,
            target_id=target_id,
            actor_user_id=actor.id,
            automatic=True,
        )
        created.append(row.id)
    return created


def create_certification(
    *, actor: AuthenticatedUser, idempotency_key: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """등록 — 항상 NOT_STARTED로 시작한다 (상태 지정 경로 없음 — 층2).

    CONFIRMED 템플릿에서만 생성하고(3층 확정 게이트의 소비자 — 안건 ①),
    요건 정의 7필드를 스냅샷으로 동결하며(이후 템플릿 개정 비소급), 체크리스트를
    태스크로 참조 복사한다(ADR-05). 전부 한 트랜잭션이다(§17.1).
    """
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=CERTIFICATION_CREATE_ENDPOINT,
            key=idempotency_key,
            request_body=payload,
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        template_id = int(payload["template_id"])
        template = session.execute(
            select(RequirementTemplate).where(
                RequirementTemplate.id == template_id,
                RequirementTemplate.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if template is None:
            raise NotFoundError(log_context={"template_id": template_id})
        if template.status != "CONFIRMED":
            raise AppError(
                ErrorCode.CERTIFICATIONS_TEMPLATE_NOT_CONFIRMED,
                log_context={"template_id": template_id, "status": template.status},
            )

        target_type = str(payload["target_type"])
        if target_type != template.applies_to:
            raise AppError(
                ErrorCode.CERTIFICATIONS_TARGET_APPLIES_TO_MISMATCH,
                detail={"target_type": target_type, "applies_to": template.applies_to},
                log_context={"template_id": template_id},
            )
        target_id = payload.get("target_id")
        target_id = int(target_id) if target_id is not None else None
        _require_target(session, target_type, target_id)

        assignee_id = payload.get("assignee_id")
        if assignee_id is not None:
            assignee = session.execute(
                select(User.id).where(User.id == int(assignee_id), User.deleted_at.is_(None))
            ).scalar_one_or_none()
            if assignee is None:
                raise AppError(
                    ErrorCode.VALIDATION_INVALID_FIELD,
                    detail={"assignee_id": "담당자 계정을 찾을 수 없습니다."},
                )

        try:
            row = _instantiate_template(
                session,
                template=template,
                target_type=target_type,
                target_id=target_id,
                actor_user_id=actor.id,
                assignee_id=int(assignee_id) if assignee_id is not None else None,
                note=payload.get("note"),
            )
        except IntegrityError as exc:
            # 함정 ② — 실패 경로의 값은 flush 전에 빼 둔 평범한 변수만 읽는다.
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={
                    "target_id": "같은 대상·템플릿의 인증이 이미 진행 중입니다. 목록에서 확인해 주세요."
                },
                log_context={"template_id": template_id, "target_id": target_id},
            ) from exc
        session.flush()

        body = _serialize(_certification_view(session, row, _market_code(session, row.template_id)))
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


# ── 편집 (비상태 필드 — status는 스키마에 없다) ──────────────────────────────


def update_certification(
    *, actor: AuthenticatedUser, certification_id: int, payload: dict[str, Any]
) -> CertificationView:
    """비상태 필드 편집 — 인증번호·담당자·메모는 상시, 만료일 정정은 달력 파생
    3태 한정(그 직후 즉시 수렴 — 조건 A)."""
    with unit_of_work() as uow:
        session = uow.session
        row = require_certification(session, certification_id, for_update=True)
        _require_version(row, payload["version"])

        if "expires_on" in payload:
            if row.status not in DATE_DERIVED_STATUSES:
                raise AppError(
                    ErrorCode.CERTIFICATIONS_EXPIRES_ON_STATE_LOCKED,
                    log_context={"certification_id": certification_id, "status": row.status},
                )
            row.expires_on = _as_date(payload["expires_on"])
        if "cert_number" in payload:
            raw = payload["cert_number"]
            row.cert_number = str(raw).strip() or None if raw is not None else None
        if "assignee_id" in payload:
            assignee_id = payload["assignee_id"]
            if assignee_id is not None:
                found = session.execute(
                    select(User.id).where(User.id == int(assignee_id), User.deleted_at.is_(None))
                ).scalar_one_or_none()
                if found is None:
                    raise AppError(
                        ErrorCode.VALIDATION_INVALID_FIELD,
                        detail={"assignee_id": "담당자 계정을 찾을 수 없습니다."},
                    )
            row.assignee_id = int(assignee_id) if assignee_id is not None else None
        if "note" in payload:
            row.note = payload["note"]

        row.updated_by_id = actor.id
        # 조건 A — 만료일이 바뀌었으면 같은 트랜잭션 말미에 즉시 수렴한다.
        _converge(session, row)
        session.flush()
        return _certification_view(session, row, _market_code(session, row.template_id))


# ── 조회 ───────────────────────────────────────────────────────────────────


def list_certifications(
    *,
    template_id: int | None,
    target_type: str | None,
    status: str | None,
    offset: int,
    limit: int,
) -> tuple[list[CertificationView], int]:
    with unit_of_work() as uow:
        session = uow.session
        conditions: list[ColumnElement[bool]] = [Certification.deleted_at.is_(None)]
        if template_id is not None:
            conditions.append(Certification.template_id == template_id)
        if target_type is not None:
            conditions.append(Certification.target_type == target_type)
        if status is not None:
            conditions.append(Certification.status == status)
        total = session.execute(
            select(func.count()).select_from(Certification).where(*conditions)
        ).scalar_one()
        rows = session.execute(
            select(Certification, Market.code)
            .join(RequirementTemplate, Certification.template_id == RequirementTemplate.id)
            .join(Market, RequirementTemplate.market_id == Market.id)
            .where(*conditions)
            .order_by(Market.code, Certification.template_name, Certification.id)
            .offset(offset)
            .limit(limit)
        ).all()
        return [_certification_view(session, row, code) for row, code in rows], total


def get_certification(certification_id: int) -> CertificationView:
    with unit_of_work() as uow:
        session = uow.session
        row = require_certification(session, certification_id)
        return _certification_view(session, row, _market_code(session, row.template_id))


def list_status_log(
    *, certification_id: int, offset: int, limit: int
) -> tuple[list[StatusLogView], int]:
    with unit_of_work() as uow:
        session = uow.session
        require_certification(session, certification_id)
        conditions = (CertificationStatusLog.certification_id == certification_id,)
        total = session.execute(
            select(func.count()).select_from(CertificationStatusLog).where(*conditions)
        ).scalar_one()
        rows = (
            session.execute(
                select(CertificationStatusLog)
                .where(*conditions)
                .order_by(CertificationStatusLog.id.desc())
                .offset(offset)
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return [
            StatusLogView(
                id=row.id,
                certification_id=row.certification_id,
                occurred_at=row.occurred_at.isoformat(),
                from_status=row.from_status,
                to_status=row.to_status,
                reason=row.reason,
                actor_user_id=row.actor_user_id,
                expires_on_snapshot=row.expires_on_snapshot,
                cert_number_snapshot=row.cert_number_snapshot,
            )
            for row in rows
        ], total


# ── 태스크 (ADR-11 "태스크는 자유") ─────────────────────────────────────────


def list_tasks(*, certification_id: int, offset: int, limit: int) -> tuple[list[TaskView], int]:
    with unit_of_work() as uow:
        session = uow.session
        require_certification(session, certification_id)
        conditions = (
            CertificationTask.certification_id == certification_id,
            CertificationTask.deleted_at.is_(None),
        )
        total = session.execute(
            select(func.count()).select_from(CertificationTask).where(*conditions)
        ).scalar_one()
        rows = (
            session.execute(
                select(CertificationTask)
                .where(*conditions)
                .order_by(CertificationTask.seq)
                .offset(offset)
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return [_task_view(row) for row in rows], total


def add_task(
    *,
    actor: AuthenticatedUser,
    idempotency_key: str,
    certification_id: int,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    with unit_of_work() as uow:
        session = uow.session
        claim = idempotency.claim(
            session,
            actor_user_id=actor.id,
            endpoint=TASK_ADD_ENDPOINT,
            key=idempotency_key,
            request_body={**payload, "certification_id": certification_id},
        )
        if claim.replay is not None:
            return claim.replay.status_code, claim.replay.body

        require_certification(session, certification_id)
        row = CertificationTask(
            certification_id=certification_id,
            seq=int(payload["seq"]),
            item_name=str(payload["item_name"]).strip(),
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
                detail={"seq": f"이 인증에 이미 있는 순번입니다: {seq}."},
                log_context={"certification_id": certification_id, "seq": seq},
            ) from exc

        body = asdict(_task_view(row))
        assert claim.record is not None
        idempotency.complete(session, claim.record, status_code=201, body=body)
        return 201, body


def _resolve_task_document(session: Session, value: Any) -> int | None:
    """서류 링크 값 해석 — null=해제, 양수=활성 문서 실재 검증(§5.1 "서류 링크").

    소유자 제한을 걸지 않는다 — §5.6의 공용 참조(CFS·CoA는 SKU 소유 문서를
    여러 태스크가 참조)가 정본 용법이라, 태스크의 document_type과의 일치도
    강제하지 않는다(종류 안내는 화면 필터 몫 — 문면에 없는 차단은 발명이다).
    """
    if value is None:
        return None
    document_id = int(value)
    document = session.execute(
        select(Document).where(Document.id == document_id, Document.deleted_at.is_(None))
    ).scalar_one_or_none()
    if document is None:
        raise AppError(
            ErrorCode.VALIDATION_INVALID_FIELD,
            detail={"document_id": "존재하지 않는 문서입니다. 문서보관소에서 다시 선택해 주세요."},
            log_context={"document_id": document_id},
        )
    return document.id


def update_task(
    *, actor: AuthenticatedUser, certification_id: int, task_id: int, payload: dict[str, Any]
) -> TaskView:
    """체크 토글·항목명·메모 편집·서류 링크 — done↔done_at 쌍은 서비스가 함께
    움직인다(DB CHECK done_pair가 마지막 층). document_id는 3값 의미론
    (생략=미변경 / null=해제 / 양수=연결 — 스키마 독스트링)."""
    with unit_of_work() as uow:
        session = uow.session
        require_certification(session, certification_id)
        row = session.execute(
            select(CertificationTask).where(
                CertificationTask.id == task_id,
                CertificationTask.certification_id == certification_id,
                CertificationTask.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError(
                log_context={"certification_id": certification_id, "task_id": task_id}
            )
        if int(payload["version"]) != row.version:
            raise VersionConflictError(log_context={"task_id": task_id})

        if "done" in payload:
            done = bool(payload["done"])
            if done and not row.done:
                row.done = True
                row.done_at = utcnow()
            elif not done and row.done:
                row.done = False
                row.done_at = None
        if "item_name" in payload:
            row.item_name = str(payload["item_name"]).strip()
        if "seq" in payload:
            row.seq = int(payload["seq"])
        if "document_id" in payload:
            row.document_id = _resolve_task_document(session, payload["document_id"])
        if "note" in payload:
            row.note = payload["note"]
        row.updated_by_id = actor.id
        seq = row.seq
        try:
            session.flush()
        except IntegrityError as exc:
            raise AppError(
                ErrorCode.VALIDATION_INVALID_FIELD,
                detail={"seq": f"이 인증에 이미 있는 순번입니다: {seq}."},
                log_context={"certification_id": certification_id, "seq": seq},
            ) from exc
        return _task_view(row)


def remove_task(*, actor: AuthenticatedUser, certification_id: int, task_id: int) -> None:
    """태스크 제거 = soft delete. 재추가는 부활이 아니라 신규다(§17.4)."""
    with unit_of_work() as uow:
        session = uow.session
        require_certification(session, certification_id)
        row = session.execute(
            select(CertificationTask).where(
                CertificationTask.id == task_id,
                CertificationTask.certification_id == certification_id,
                CertificationTask.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError(
                log_context={"certification_id": certification_id, "task_id": task_id}
            )
        row.deleted_at = utcnow()
        row.updated_by_id = actor.id
