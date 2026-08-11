"""C·J — 인증 상태머신: 110쌍 전수·생성 게이트·스냅샷·수렴·멱등 (§5.2 / S2-2 조건 A·C·E).

★ 110쌍 전수는 서비스 층 파라미터라이즈다(조건 C — 실 HTTP 110회 금지).
  허용 21방향=성공+이력 정확히 1행, 미허용 89방향(자동 6방향 포함 — 사람
  통로로는 막힌다)=409+상태 불변+이력 무기록. **양방향 자기검사**가 GC-C9의
  원 계약이고, e2e(커밋 ⑤)가 대표 쌍으로 실 API를 확인한다.

★ 전수 픽스처는 수렴 비발화 날짜로 고정한다(조건 A 문면 — 이력 1행 단언 유지).
  수렴 연쇄(갱신 완료의 만료일이 과거 → 즉시 만료)는 전용 케이스가 고정한다.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select

from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, VersionConflictError
from app.core.time import today_kst
from app.modules.certifications import service
from app.modules.certifications.machine import (
    CERTIFICATION_STATUSES,
    HUMAN_TRANSITIONS,
    REASON_REQUIRED_TO,
    TRANSITION_REQUIRED_FIELDS,
)
from app.modules.certifications.models import Certification, CertificationStatusLog
from app.modules.identity.models import RoleCode
from app.modules.identity.service import AuthenticatedUser
from app.modules.outbox.models import Event
from app.modules.requirements.models import RequirementTemplate, TemplateChecklistItem
from tests.support.factories import create_market, create_sku, create_user

pytestmark = pytest.mark.group_c

_FAR_FUTURE = date(2100, 1, 1)
_PAST = date(2020, 1, 1)


@pytest.fixture
def actor() -> AuthenticatedUser:
    user_id = create_user("cert-machine@example.com", roles=(RoleCode.CERT,))
    return AuthenticatedUser(
        id=user_id,
        email="cert-machine@example.com",
        display_name="인증 담당",
        roles=frozenset({RoleCode.CERT}),
        session_id=0,
    )


def _confirmed_template(
    *, applies_to: str = "SKU", market: str = "US", status: str = "CONFIRMED", **overrides: Any
) -> int:
    with unit_of_work() as uow:
        row = RequirementTemplate(
            market_id=create_market(market),
            name=overrides.pop("name", f"{market} {applies_to} 등록"),
            applies_to=applies_to,
            requirement_type="REGISTRATION",
            source_url="https://example.test/rule",
            last_verified_on=date(2026, 8, 1),
            status=status,
            **overrides,
        )
        uow.session.add(row)
        uow.session.flush()
        return row.id


def _create(
    actor: AuthenticatedUser, *, key: str, template_id: int | None = None, **overrides: Any
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "template_id": template_id or _confirmed_template(),
        "target_type": overrides.pop("target_type", "SKU"),
    }
    if payload["target_type"] == "SKU" and "target_id" not in overrides:
        overrides["target_id"] = create_sku()
    payload.update(overrides)
    status_code, body = service.create_certification(
        actor=actor, idempotency_key=key, payload=payload
    )
    assert status_code == 201, body
    return body


#: 상태별 자기 일관 날짜 세트 — 달력 파생 3태는 파생 함수의 정답과 일치시키고
#: (EXPIRING=리드 창 안, EXPIRED=도과), 나머지는 수렴 비발화(무기한·먼 미래)다.
def _dates_for(status: str) -> dict[str, Any]:
    if status == "APPROVED":
        return {
            "approved_on": date(2026, 1, 1),
            "valid_from": date(2026, 1, 1),
            "expires_on": _FAR_FUTURE,
        }
    if status == "EXPIRING":
        return {
            "approved_on": date(2026, 1, 1),
            "valid_from": date(2026, 1, 1),
            "expires_on": today_kst() + timedelta(days=10),
            "renewal_lead_days": 30,
        }
    if status == "EXPIRED":
        return {
            "approved_on": date(2019, 1, 1),
            "valid_from": date(2019, 1, 1),
            "expires_on": _PAST,
        }
    if status == "RENEWING":
        return {
            "approved_on": date(2026, 1, 1),
            "valid_from": date(2026, 1, 1),
            "expires_on": _FAR_FUTURE,
        }
    return {}


def _force_state(certification_id: int, status: str) -> None:
    """전수 픽스처 — 상태·날짜를 직접 심는다(서비스 우회는 픽스처 한정)."""
    if status == "NOT_STARTED":
        return
    with unit_of_work() as uow:
        row = uow.session.execute(
            select(Certification).where(Certification.id == certification_id)
        ).scalar_one()
        row.status = status
        for field, value in _dates_for(status).items():
            setattr(row, field, value)


def _log_count(certification_id: int) -> int:
    with unit_of_work() as uow:
        return uow.session.execute(
            select(func.count())
            .select_from(CertificationStatusLog)
            .where(CertificationStatusLog.certification_id == certification_id)
        ).scalar_one()


def _current(certification_id: int) -> tuple[str, int]:
    view = service.get_certification(certification_id)
    return view.status, view.version


def _transition_payload(from_status: str, to_status: str, version: int) -> dict[str, Any]:
    payload: dict[str, Any] = {"to": to_status, "version": version}
    if to_status in REASON_REQUIRED_TO:
        payload["reason"] = "전수 검사 사유"
    for field in TRANSITION_REQUIRED_FIELDS.get((from_status, to_status), frozenset()):
        payload[field] = "2026-08-01"
    return payload


# ── 110쌍 전수 (조건 C·E — 양방향 자기검사) ─────────────────────────────────

_ALL_PAIRS = [(a, b) for a in CERTIFICATION_STATUSES for b in CERTIFICATION_STATUSES if a != b]
assert len(_ALL_PAIRS) == 110


@pytest.mark.parametrize(("from_status", "to_status"), _ALL_PAIRS)
def test_every_ordered_pair_through_the_human_channel(
    actor: AuthenticatedUser, from_status: str, to_status: str
) -> None:
    """허용 21방향=성공+이력 1행 / 나머지 89방향=409+상태 불변+이력 무기록"""
    body = _create(actor, key=f"pair-{from_status}-{to_status}")
    certification_id = body["id"]
    _force_state(certification_id, from_status)
    status_before, version = _current(certification_id)
    assert status_before == from_status
    logs_before = _log_count(certification_id)

    payload = _transition_payload(from_status, to_status, version)
    if (from_status, to_status) in HUMAN_TRANSITIONS:
        status_code, result = service.transition_certification(
            actor=actor,
            idempotency_key=f"go-{from_status}-{to_status}",
            certification_id=certification_id,
            payload=payload,
        )
        assert status_code == 200
        assert result["status"] == to_status
        assert _log_count(certification_id) == logs_before + 1  # 수렴 비발화 픽스처
        with unit_of_work() as uow:
            log = (
                uow.session.execute(
                    select(CertificationStatusLog)
                    .where(CertificationStatusLog.certification_id == certification_id)
                    .order_by(CertificationStatusLog.id.desc())
                )
                .scalars()
                .first()
            )
            assert log is not None
            assert (log.from_status, log.to_status) == (from_status, to_status)
            assert log.actor_user_id == actor.id
    else:
        with pytest.raises(AppError) as caught:
            service.transition_certification(
                actor=actor,
                idempotency_key=f"deny-{from_status}-{to_status}",
                certification_id=certification_id,
                payload=payload,
            )
        assert caught.value.code == ErrorCode.CERTIFICATIONS_TRANSITION_NOT_ALLOWED
        status_after, _ = _current(certification_id)
        assert status_after == from_status  # 상태 불변
        assert _log_count(certification_id) == logs_before  # 이력 무기록


# ── 생성 게이트 (안건 ① — 3층 확정 게이트의 소비자) ─────────────────────────


def test_create_from_confirmed_template_succeeds(actor: AuthenticatedUser) -> None:
    """성공 방향 자기검사 — CONFIRMED 템플릿이면 등록되고 미착수로 시작한다"""
    body = _create(actor, key="gate-ok")
    assert body["status"] == "NOT_STARTED"
    assert body["market_code"] == "US"


@pytest.mark.parametrize("template_status", ["DRAFT", "RETIRED"])
def test_create_from_unconfirmed_template_is_rejected(
    actor: AuthenticatedUser, template_status: str
) -> None:
    template_id = _confirmed_template(status=template_status)
    with pytest.raises(AppError) as caught:
        _create(actor, key=f"gate-{template_status}", template_id=template_id)
    assert caught.value.code == ErrorCode.CERTIFICATIONS_TEMPLATE_NOT_CONFIRMED


def test_create_rejects_applies_to_mismatch(actor: AuthenticatedUser) -> None:
    """PRODUCT 템플릿에 SKU 대상 — 폴리모픽 정합 강제(안건 ②)"""
    template_id = _confirmed_template(applies_to="PRODUCT")
    with pytest.raises(AppError) as caught:
        _create(actor, key="mismatch", template_id=template_id, target_type="SKU")
    assert caught.value.code == ErrorCode.CERTIFICATIONS_TARGET_APPLIES_TO_MISMATCH


def test_create_rejects_missing_target(actor: AuthenticatedUser) -> None:
    with pytest.raises(AppError) as caught:
        _create(actor, key="ghost-target", target_id=999_999)
    assert caught.value.code == ErrorCode.CERTIFICATIONS_TARGET_NOT_FOUND


def test_company_certification_has_no_target(actor: AuthenticatedUser) -> None:
    """COMPANY=자사 단일 — target 없이 등록되고, target을 주면 거부"""
    template_id = _confirmed_template(applies_to="COMPANY", market="EU")
    body = _create(actor, key="company-ok", template_id=template_id, target_type="COMPANY")
    assert body["target_id"] is None
    assert body["target_label"] == "자사(기업 단위)"
    template_id2 = _confirmed_template(applies_to="COMPANY", market="JP")
    with pytest.raises(AppError):
        _create(
            actor,
            key="company-bad",
            template_id=template_id2,
            target_type="COMPANY",
            target_id=1,
        )


def test_duplicate_open_instance_is_guided(actor: AuthenticatedUser) -> None:
    """활성 중복은 유니크가 막고 안내 문구로 나간다"""
    template_id = _confirmed_template()
    sku_id = create_sku()
    _create(actor, key="dup-1", template_id=template_id, target_id=sku_id)
    with pytest.raises(AppError) as caught:
        _create(actor, key="dup-2", template_id=template_id, target_id=sku_id)
    assert caught.value.code == ErrorCode.VALIDATION_INVALID_FIELD


# ── 스냅샷 동결 (안건 ① — "그때 요건", 템플릿 개정 비소급) ──────────────────


def test_snapshot_survives_template_revision(actor: AuthenticatedUser) -> None:
    template_id = _confirmed_template(renewal_lead_days=90)
    body = _create(actor, key="snap", template_id=template_id)
    with unit_of_work() as uow:
        template = uow.session.execute(
            select(RequirementTemplate).where(RequirementTemplate.id == template_id)
        ).scalar_one()
        template.status = "DRAFT"
        template.name = "개정된 이름"
        template.renewal_lead_days = 7
        template.status = "CONFIRMED"
    view = service.get_certification(body["id"])
    assert view.template_name != "개정된 이름"  # 스냅샷 불변
    assert view.renewal_lead_days == 90


def test_checklist_is_copied_into_tasks(actor: AuthenticatedUser) -> None:
    """체크리스트 3필드 참조 복사 (ADR-05 — [M2] 보강 S2-1 PR-2 ④ 지시분)"""
    template_id = _confirmed_template()
    with unit_of_work() as uow:
        uow.session.add_all(
            [
                TemplateChecklistItem(
                    template_id=template_id, seq=1, item_name="CFS 발급", is_required=True
                ),
                TemplateChecklistItem(
                    template_id=template_id, seq=2, item_name="라벨 검토", is_required=False
                ),
            ]
        )
    body = _create(actor, key="copy", template_id=template_id)
    tasks, total = service.list_tasks(certification_id=body["id"], offset=0, limit=50)
    assert total == 2
    assert [(t.seq, t.item_name, t.is_required, t.done) for t in tasks] == [
        (1, "CFS 발급", True, False),
        (2, "라벨 검토", False, False),
    ]


# ── 전이 부속 데이터·사유 (§17.1 — 같은 요청·같은 트랜잭션) ─────────────────


def test_submission_requires_applied_on(actor: AuthenticatedUser) -> None:
    body = _create(actor, key="need-date")
    _force_state(body["id"], "PREPARING")
    _, version = _current(body["id"])
    with pytest.raises(AppError) as caught:
        service.transition_certification(
            actor=actor,
            idempotency_key="need-date-go",
            certification_id=body["id"],
            payload={"to": "SUBMITTED", "version": version},
        )
    assert caught.value.code == ErrorCode.VALIDATION_INVALID_FIELD
    assert _current(body["id"])[0] == "PREPARING"


def test_approval_records_result_fields(actor: AuthenticatedUser) -> None:
    body = _create(actor, key="approve")
    _force_state(body["id"], "IN_REVIEW")
    _, version = _current(body["id"])
    status_code, result = service.transition_certification(
        actor=actor,
        idempotency_key="approve-go",
        certification_id=body["id"],
        payload={
            "to": "APPROVED",
            "version": version,
            "approved_on": "2026-08-01",
            "valid_from": "2026-08-01",
            "expires_on": "2100-01-01",
            "cert_number": "CPNP-12345",
        },
    )
    assert status_code == 200
    assert result["status"] == "APPROVED"
    assert result["approved_on"] == "2026-08-01"
    assert result["cert_number"] == "CPNP-12345"


def test_stray_field_is_rejected_not_ignored(actor: AuthenticatedUser) -> None:
    """허용 밖 부속 필드는 무시가 아니라 422 — 조용한 무시 금지"""
    body = _create(actor, key="stray")
    _, version = _current(body["id"])
    with pytest.raises(AppError) as caught:
        service.transition_certification(
            actor=actor,
            idempotency_key="stray-go",
            certification_id=body["id"],
            payload={"to": "PREPARING", "version": version, "cert_number": "X-1"},
        )
    assert caught.value.code == ErrorCode.VALIDATION_INVALID_FIELD


def test_rejection_requires_reason(actor: AuthenticatedUser) -> None:
    body = _create(actor, key="why")
    _force_state(body["id"], "IN_REVIEW")
    _, version = _current(body["id"])
    with pytest.raises(AppError) as caught:
        service.transition_certification(
            actor=actor,
            idempotency_key="why-go",
            certification_id=body["id"],
            payload={"to": "REJECTED", "version": version},
        )
    assert caught.value.code == ErrorCode.CERTIFICATIONS_TRANSITION_REASON_REQUIRED
    assert _current(body["id"])[0] == "IN_REVIEW"


def test_renewal_completion_and_cancel_paths(actor: AuthenticatedUser) -> None:
    """갱신 완료=새 값 반영, 부속 생략=기존 값 유지(취소 복귀) — 입력값이 구분"""
    body = _create(actor, key="renew")
    _force_state(body["id"], "RENEWING")
    _, version = _current(body["id"])
    # 취소 복귀 — 부속 생략.
    service.transition_certification(
        actor=actor,
        idempotency_key="renew-cancel",
        certification_id=body["id"],
        payload={"to": "APPROVED", "version": version, "reason": "갱신 취소 — 기존 유효분 유지"},
    )
    view = service.get_certification(body["id"])
    assert view.expires_on == _FAR_FUTURE  # 기존 값 유지
    # 다시 갱신 착수 → 완료 — 새 만료일 반영.
    service.transition_certification(
        actor=actor,
        idempotency_key="renew-again",
        certification_id=body["id"],
        payload={"to": "RENEWING", "version": view.version},
    )
    _, version = _current(body["id"])
    service.transition_certification(
        actor=actor,
        idempotency_key="renew-done",
        certification_id=body["id"],
        payload={
            "to": "APPROVED",
            "version": version,
            "approved_on": "2026-08-10",
            "expires_on": "2100-06-30",
        },
    )
    view = service.get_certification(body["id"])
    assert view.expires_on == date(2100, 6, 30)
    assert view.approved_on == date(2026, 8, 10)


# ── 달력 즉시 수렴 (조건 A — 전용 케이스) ───────────────────────────────────


def test_renewal_with_past_expiry_converges_immediately(actor: AuthenticatedUser) -> None:
    """갱신 완료 기록의 만료일이 과거 → 같은 트랜잭션에서 즉시 만료 수렴(이력 2행)"""
    body = _create(actor, key="chain")
    _force_state(body["id"], "RENEWING")
    _, version = _current(body["id"])
    logs_before = _log_count(body["id"])
    status_code, result = service.transition_certification(
        actor=actor,
        idempotency_key="chain-go",
        certification_id=body["id"],
        payload={
            "to": "APPROVED",
            "version": version,
            "approved_on": "2020-01-01",
            "valid_from": "2020-01-01",  # 기간 순서 CHECK — 과거 만료일과 한 쌍으로 정정
            "expires_on": "2020-12-31",
        },
    )
    assert status_code == 200
    assert result["status"] == "EXPIRED"  # 응답부터 수렴 후 상태 — 거짓 상태 창 없음
    assert _log_count(body["id"]) == logs_before + 2  # 사람 1행 + 자동 수렴 1행
    with unit_of_work() as uow:
        last = (
            uow.session.execute(
                select(CertificationStatusLog)
                .where(CertificationStatusLog.certification_id == body["id"])
                .order_by(CertificationStatusLog.id.desc())
            )
            .scalars()
            .first()
        )
        assert last is not None
        assert (last.from_status, last.to_status) == ("APPROVED", "EXPIRED")
        assert last.actor_user_id is None  # 자동 — actor NULL
        assert last.reason is not None  # 조건 B — 사유 자동 기록


def test_approval_into_lead_window_converges_to_expiring(actor: AuthenticatedUser) -> None:
    """승인 기록의 만료일이 리드 창 안 → 즉시 만료임박 수렴"""
    template_id = _confirmed_template(renewal_lead_days=90)
    body = _create(actor, key="lead", template_id=template_id)
    _force_state(body["id"], "IN_REVIEW")
    _, version = _current(body["id"])
    _, result = service.transition_certification(
        actor=actor,
        idempotency_key="lead-go",
        certification_id=body["id"],
        payload={
            "to": "APPROVED",
            "version": version,
            "approved_on": today_kst().isoformat(),
            "valid_from": today_kst().isoformat(),
            "expires_on": (today_kst() + timedelta(days=30)).isoformat(),
        },
    )
    assert result["status"] == "EXPIRING"


def test_expires_on_patch_converges_and_is_state_locked(actor: AuthenticatedUser) -> None:
    """만료일 정정: 3태 안이면 정정+즉시 수렴, 밖이면 409 (조건 A·전이 17·18)"""
    body = _create(actor, key="patch")
    _force_state(body["id"], "EXPIRED")
    _, version = _current(body["id"])
    view = service.update_certification(
        actor=actor,
        certification_id=body["id"],
        payload={"version": version, "expires_on": "2100-01-01"},
    )
    assert view.status == "APPROVED"  # 만료→승인 자동 복귀 (전이 27)
    # 진행 상태에서는 만료일 정정이 잠긴다 — 승인 부속으로만 기록.
    # (같은 테스트에서 템플릿을 둘 만들면 (시장, 이름) 유니크에 걸린다 — 함정 ⑥)
    body2 = _create(
        actor,
        key="patch-locked",
        template_id=_confirmed_template(name="US SKU 등록 2차"),
        target_id=create_sku("SKU-002"),
    )
    _, version2 = _current(body2["id"])
    with pytest.raises(AppError) as caught:
        service.update_certification(
            actor=actor,
            certification_id=body2["id"],
            payload={"version": version2, "expires_on": "2100-01-01"},
        )
    assert caught.value.code == ErrorCode.CERTIFICATIONS_EXPIRES_ON_STATE_LOCKED


# ── 멱등·동시성·아웃박스 (J / 판정 ⑩ 대표 확인) ────────────────────────────


def _event_count() -> int:
    with unit_of_work() as uow:
        return uow.session.execute(select(func.count()).select_from(Event)).scalar_one()


@pytest.mark.group_j
def test_create_double_click_yields_one_instance(actor: AuthenticatedUser) -> None:
    template_id = _confirmed_template()
    sku_id = create_sku()
    payload = {"template_id": template_id, "target_type": "SKU", "target_id": sku_id}
    first = service.create_certification(actor=actor, idempotency_key="dc", payload=payload)
    replay = service.create_certification(actor=actor, idempotency_key="dc", payload=payload)
    assert first == replay  # 최초 결과 재생 (§17.4)
    with unit_of_work() as uow:
        count = uow.session.execute(
            select(func.count())
            .select_from(Certification)
            .where(Certification.template_id == template_id)
        ).scalar_one()
    assert count == 1


@pytest.mark.group_j
def test_transition_double_click_records_once(actor: AuthenticatedUser) -> None:
    body = _create(actor, key="tdc")
    _, version = _current(body["id"])
    payload = {"to": "PREPARING", "version": version}
    first = service.transition_certification(
        actor=actor, idempotency_key="tdc-go", certification_id=body["id"], payload=payload
    )
    replay = service.transition_certification(
        actor=actor, idempotency_key="tdc-go", certification_id=body["id"], payload=payload
    )
    assert first == replay
    assert _log_count(body["id"]) == 1


@pytest.mark.group_j
def test_stale_version_is_rejected(actor: AuthenticatedUser) -> None:
    body = _create(actor, key="stale")
    _, version = _current(body["id"])
    service.transition_certification(
        actor=actor,
        idempotency_key="stale-1",
        certification_id=body["id"],
        payload={"to": "PREPARING", "version": version},
    )
    with pytest.raises(VersionConflictError):
        service.transition_certification(
            actor=actor,
            idempotency_key="stale-2",
            certification_id=body["id"],
            payload={"to": "SUBMITTED", "version": version, "applied_on": "2026-08-01"},
        )


@pytest.mark.group_j
def test_failed_transition_leaves_no_event_and_no_log(actor: AuthenticatedUser) -> None:
    """실패 롤백이 이벤트까지 되돌린다 — 아웃박스 '커밋 실패 시 발송 0' 계보(§20 J)"""
    body = _create(actor, key="rollback")
    _force_state(body["id"], "IN_REVIEW")
    _, version = _current(body["id"])
    events_before = _event_count()
    logs_before = _log_count(body["id"])
    with pytest.raises(AppError):
        service.transition_certification(
            actor=actor,
            idempotency_key="rollback-go",
            certification_id=body["id"],
            payload={"to": "REJECTED", "version": version},  # 사유 결여 — 422
        )
    assert _event_count() == events_before
    assert _log_count(body["id"]) == logs_before


def test_events_are_published_for_create_and_transition(actor: AuthenticatedUser) -> None:
    """판정 ⑩ 대표 확인 — 생성·전이 전건이 아웃박스에 기록된다(발송은 S2-3)"""
    events_before = _event_count()
    body = _create(actor, key="evt")
    assert _event_count() == events_before + 1  # created
    _, version = _current(body["id"])
    service.transition_certification(
        actor=actor,
        idempotency_key="evt-go",
        certification_id=body["id"],
        payload={"to": "PREPARING", "version": version},
    )
    assert _event_count() == events_before + 2  # + status_changed
    with unit_of_work() as uow:
        last = uow.session.execute(select(Event).order_by(Event.id.desc())).scalars().first()
        assert last is not None
        assert last.event_type == "certifications.certification.status_changed"
        assert last.payload["automatic"] is False
