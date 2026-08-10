"""C·G·J — 요건 템플릿 서비스: 확정 게이트·편집 잠금·순환 거부·멱등 (§5.5 / 조건 1·7·11).

★ 서비스 레이어 직접 호출로 계약을 실측한다 — API 왕복(권한·봉투 포함)은
  e2e(test_requirement_templates)가 본다. GC-C8의 양방향(거부·성공)은 여기와
  e2e 양쪽에 있다 — 이 파일이 게이트의 원 계약, e2e가 실 API 자기검사다.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError, VersionConflictError
from app.modules.identity.models import RoleCode
from app.modules.identity.service import AuthenticatedUser
from app.modules.requirements import service
from tests.support.factories import create_market, create_user

pytestmark = pytest.mark.group_c

_EVIDENCE = {"source_url": "https://example.test/mocra", "last_verified_on": "2026-08-01"}


@pytest.fixture
def actor() -> AuthenticatedUser:
    user_id = create_user("cert-svc@example.com", roles=(RoleCode.CERT,))
    return AuthenticatedUser(
        id=user_id,
        email="cert-svc@example.com",
        display_name="인증 담당",
        roles=frozenset({RoleCode.CERT}),
        session_id=0,
    )


def _template(
    actor: AuthenticatedUser, *, key: str, name: str = "MoCRA 시설등록", **overrides: Any
) -> dict[str, Any]:
    market_id = overrides.pop("market_id", None)
    if market_id is None:
        market_id = create_market("US")
    payload: dict[str, Any] = {
        "market_id": market_id,
        "name": name,
        "applies_to": "FACILITY",
        "requirement_type": "REGISTRATION",
    }
    payload.update(overrides)
    status_code, body = service.create_template(actor=actor, idempotency_key=key, payload=payload)
    assert status_code == 201, body
    return body


def _update_payload(body: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    payload = {
        "version": body["version"],
        "name": body["name"],
        "applies_to": body["applies_to"],
        "requirement_type": body["requirement_type"],
        "validity_months": body["validity_months"],
        "renewal_cycle_months": body["renewal_cycle_months"],
        "renewal_lead_days": body["renewal_lead_days"],
        "estimated_cost_amount": body["estimated_cost_amount"],
        "estimated_cost_currency": body["estimated_cost_currency"],
        "source_url": body["source_url"],
        "last_verified_on": body["last_verified_on"],
        "status": "DRAFT",
        "note": body["note"],
    }
    payload.update(overrides)
    return payload


# ── 확정 게이트 (§5.5 / GC-C8 — 거부 방향) ─────────────────────────────────


@pytest.mark.group_g
def test_confirm_without_evidence_is_rejected_and_the_draft_survives(
    actor: AuthenticatedUser,
) -> None:
    """근거 2필드 결여 확정 = 422 거부 + 초안 유지 + 사유 2건 표시 (GC-C8 Then)"""
    body = _template(actor, key="g-none")
    with pytest.raises(AppError) as exc:
        service.confirm_template(
            actor=actor,
            idempotency_key="g-none-confirm",
            template_id=body["id"],
            payload={"version": body["version"]},
        )
    assert exc.value.code == ErrorCode.REQUIREMENTS_TEMPLATE_EVIDENCE_REQUIRED
    assert set(exc.value.detail) == {"source_url", "last_verified_on"}
    after = service.get_template(body["id"])
    assert after.status == "DRAFT", "거부가 상태를 남기면 안 된다 — 초안 유지"


@pytest.mark.group_g
def test_confirm_with_only_the_url_is_rejected_with_the_missing_field(
    actor: AuthenticatedUser,
) -> None:
    """근거링크만으로는 부족하다 — 부족한 필드만 사유로 표시된다 (ADR-03 둘 다 필수)"""
    body = _template(actor, key="g-url", source_url=_EVIDENCE["source_url"])
    with pytest.raises(AppError) as exc:
        service.confirm_template(
            actor=actor,
            idempotency_key="g-url-confirm",
            template_id=body["id"],
            payload={"version": body["version"]},
        )
    assert exc.value.code == ErrorCode.REQUIREMENTS_TEMPLATE_EVIDENCE_REQUIRED
    assert set(exc.value.detail) == {"last_verified_on"}


@pytest.mark.group_g
def test_confirm_with_full_evidence_succeeds(actor: AuthenticatedUser) -> None:
    """근거 완비 확정 = 성공 (GC-C8 양방향의 성공 방향 — 게이트 과차단 자기검사)"""
    body = _template(actor, key="g-full", **_EVIDENCE)
    status_code, confirmed = service.confirm_template(
        actor=actor,
        idempotency_key="g-full-confirm",
        template_id=body["id"],
        payload={"version": body["version"]},
    )
    assert status_code == 200
    assert confirmed["status"] == "CONFIRMED"
    assert confirmed["version"] == body["version"] + 1


# ── 확정 상태 편집 잠금 (조건 11 — ADR-0033) ────────────────────────────────


def _confirmed(actor: AuthenticatedUser, *, key: str, **overrides: Any) -> dict[str, Any]:
    body = _template(actor, key=key, **_EVIDENCE, **overrides)
    _, confirmed = service.confirm_template(
        actor=actor,
        idempotency_key=f"{key}-confirm",
        template_id=body["id"],
        payload={"version": body["version"]},
    )
    return confirmed


def test_editing_a_confirmed_template_is_locked(actor: AuthenticatedUser) -> None:
    """CONFIRMED 헤더 편집 = 409 — 편집 경로는 초안 전환뿐이다"""
    confirmed = _confirmed(actor, key="lock-head")
    with pytest.raises(AppError) as exc:
        service.update_template(
            actor=actor,
            template_id=confirmed["id"],
            payload=_update_payload(confirmed, name="바꿔치기"),
        )
    assert exc.value.code == ErrorCode.REQUIREMENTS_TEMPLATE_CONFIRMED_LOCKED


def test_the_checklist_of_a_confirmed_template_is_locked(actor: AuthenticatedUser) -> None:
    """체크리스트도 템플릿의 내용이다 — 확정 상태에서는 추가·수정·제거 전부 409"""
    confirmed = _confirmed(actor, key="lock-list")
    with pytest.raises(AppError) as exc:
        service.add_checklist_item(
            actor=actor,
            idempotency_key="lock-list-add",
            template_id=confirmed["id"],
            payload={"seq": 1, "item_name": "시설 정보 양식", "is_required": True},
        )
    assert exc.value.code == ErrorCode.REQUIREMENTS_TEMPLATE_CONFIRMED_LOCKED


def test_the_prerequisites_of_a_confirmed_template_are_locked(
    actor: AuthenticatedUser,
) -> None:
    confirmed = _confirmed(actor, key="lock-pre")
    other = _template(actor, key="lock-pre-other", name="US Agent 지정")
    with pytest.raises(AppError) as exc:
        service.add_prerequisite(
            actor=actor,
            idempotency_key="lock-pre-add",
            template_id=confirmed["id"],
            payload={"prerequisite_template_id": other["id"]},
        )
    assert exc.value.code == ErrorCode.REQUIREMENTS_TEMPLATE_CONFIRMED_LOCKED


def test_revert_then_edit_then_reconfirm_is_the_only_edit_path(
    actor: AuthenticatedUser,
) -> None:
    """재확정 경로 — 초안 전환(근거 잔존) → 수정 → 재확정이 성립한다 (조건 11)"""
    confirmed = _confirmed(actor, key="path")
    reverted = service.revert_template_to_draft(
        actor=actor, template_id=confirmed["id"], payload={"version": confirmed["version"]}
    )
    assert reverted.status == "DRAFT"
    assert reverted.source_url == _EVIDENCE["source_url"], "전환이 근거를 지우면 안 된다"

    edited = service.update_template(
        actor=actor,
        template_id=confirmed["id"],
        payload=_update_payload(
            {**confirmed, "version": reverted.version}, name="MoCRA 시설등록(개정)"
        ),
    )
    status_code, reconfirmed = service.confirm_template(
        actor=actor,
        idempotency_key="path-reconfirm",
        template_id=confirmed["id"],
        payload={"version": edited.version},
    )
    assert status_code == 200
    assert reconfirmed["status"] == "CONFIRMED"
    assert reconfirmed["name"] == "MoCRA 시설등록(개정)"


def test_reverting_a_draft_is_rejected(actor: AuthenticatedUser) -> None:
    body = _template(actor, key="rev-draft")
    with pytest.raises(AppError) as exc:
        service.revert_template_to_draft(
            actor=actor, template_id=body["id"], payload={"version": body["version"]}
        )
    assert exc.value.code == ErrorCode.REQUIREMENTS_TEMPLATE_NOT_CONFIRMED


def test_confirming_a_confirmed_template_with_a_new_key_is_rejected(
    actor: AuthenticatedUser,
) -> None:
    """새 키의 재확정은 409다 — 같은 키의 재수신(더블클릭)만 최초 결과 재생이다"""
    confirmed = _confirmed(actor, key="already")
    with pytest.raises(AppError) as exc:
        service.confirm_template(
            actor=actor,
            idempotency_key="already-again",
            template_id=confirmed["id"],
            payload={"version": confirmed["version"]},
        )
    assert exc.value.code == ErrorCode.REQUIREMENTS_TEMPLATE_ALREADY_CONFIRMED


def test_the_update_status_field_cannot_smuggle_a_confirmation(
    actor: AuthenticatedUser,
) -> None:
    """편집의 status로 CONFIRMED를 넣는 우회는 서비스도 거부한다 (스키마 Literal 아래 층)"""
    body = _template(actor, key="smuggle", **_EVIDENCE)
    with pytest.raises(AppError) as exc:
        service.update_template(
            actor=actor,
            template_id=body["id"],
            payload=_update_payload(body, status="CONFIRMED"),
        )
    assert exc.value.code == ErrorCode.VALIDATION_INVALID_FIELD
    assert service.get_template(body["id"]).status == "DRAFT"


@pytest.mark.group_j
def test_a_stale_version_cannot_confirm(actor: AuthenticatedUser) -> None:
    """낡은 version의 확정은 409다 — 보지 않은 내용을 확정하지 않는다 (§17.2)"""
    body = _template(actor, key="stale", **_EVIDENCE)
    service.update_template(
        actor=actor, template_id=body["id"], payload=_update_payload(body, note="개정 중")
    )
    with pytest.raises(VersionConflictError):
        service.confirm_template(
            actor=actor,
            idempotency_key="stale-confirm",
            template_id=body["id"],
            payload={"version": body["version"]},  # 편집 전 낡은 토큰
        )


# ── 확정 멱등 (§17.4 / GC-A3 계보 — J) ─────────────────────────────────────


@pytest.mark.group_j
def test_confirm_double_click_replays_the_first_result(actor: AuthenticatedUser) -> None:
    """확정 더블클릭 = 확정 1건 + 동일 응답 재생 (§20 J — 핵심 계약)"""
    body = _template(actor, key="dbl", **_EVIDENCE)
    first = service.confirm_template(
        actor=actor,
        idempotency_key="dbl-confirm",
        template_id=body["id"],
        payload={"version": body["version"]},
    )
    second = service.confirm_template(
        actor=actor,
        idempotency_key="dbl-confirm",
        template_id=body["id"],
        payload={"version": body["version"]},
    )
    assert first == second, "재수신은 최초 결과 그대로여야 한다"
    assert service.get_template(body["id"]).version == body["version"] + 1, (
        "확정이 두 번 일어나면 version이 두 번 오른다 — 한 번이어야 한다"
    )


@pytest.mark.group_j
def test_create_double_click_creates_one_template(actor: AuthenticatedUser) -> None:
    market_id = create_market("US")
    first = _template(actor, key="one", market_id=market_id)
    second = _template(actor, key="one", market_id=market_id)
    assert first["id"] == second["id"]
    _, total = service.list_templates(market_id=market_id, status=None, offset=0, limit=50)
    assert total == 1


# ── 선행요건 순환 거부 (조건 1 — ADR-0034) ─────────────────────────────────


def _link(actor: AuthenticatedUser, key: str, template_id: int, prerequisite_id: int) -> None:
    status_code, _ = service.add_prerequisite(
        actor=actor,
        idempotency_key=key,
        template_id=template_id,
        payload={"prerequisite_template_id": prerequisite_id},
    )
    assert status_code == 201


def test_a_two_step_cycle_is_rejected(actor: AuthenticatedUser) -> None:
    """A→B 뒤의 B→A는 422다 — 서로가 서로를 기다리는 그래프는 순서가 없다"""
    market_id = create_market("CN")
    a = _template(actor, key="c2-a", name="NMPA 비안", market_id=market_id)
    b = _template(actor, key="c2-b", name="경내책임자 지정", market_id=market_id)
    _link(actor, "c2-ab", a["id"], b["id"])
    with pytest.raises(AppError) as exc:
        service.add_prerequisite(
            actor=actor,
            idempotency_key="c2-ba",
            template_id=b["id"],
            payload={"prerequisite_template_id": a["id"]},
        )
    assert exc.value.code == ErrorCode.REQUIREMENTS_PREREQUISITE_CYCLE


def test_a_three_step_cycle_is_rejected(actor: AuthenticatedUser) -> None:
    """깊이 3의 순환도 잡는다 — DB CHECK(깊이 1) 너머는 전부 이 검증 몫이다"""
    market_id = create_market("EU")
    a = _template(actor, key="c3-a", name="CPNP 통보", market_id=market_id)
    b = _template(actor, key="c3-b", name="RP 지정", market_id=market_id)
    c = _template(actor, key="c3-c", name="PIF 작성", market_id=market_id)
    _link(actor, "c3-ab", a["id"], b["id"])
    _link(actor, "c3-bc", b["id"], c["id"])
    with pytest.raises(AppError) as exc:
        service.add_prerequisite(
            actor=actor,
            idempotency_key="c3-ca",
            template_id=c["id"],
            payload={"prerequisite_template_id": a["id"]},
        )
    assert exc.value.code == ErrorCode.REQUIREMENTS_PREREQUISITE_CYCLE


def test_a_self_prerequisite_is_rejected_by_the_service(actor: AuthenticatedUser) -> None:
    """자기참조는 서비스가 먼저 잡는다 — DB CHECK(no_self_reference)는 그 아래 층"""
    a = _template(actor, key="self-a")
    with pytest.raises(AppError) as exc:
        service.add_prerequisite(
            actor=actor,
            idempotency_key="self-add",
            template_id=a["id"],
            payload={"prerequisite_template_id": a["id"]},
        )
    assert exc.value.code == ErrorCode.REQUIREMENTS_PREREQUISITE_CYCLE


def test_a_diamond_is_not_a_cycle(actor: AuthenticatedUser) -> None:
    """다이아몬드(공통 선행)는 순환이 아니다 — 과차단 자기검사"""
    market_id = create_market("US")
    a = _template(actor, key="d-a", name="제품 리스팅", market_id=market_id)
    b = _template(actor, key="d-b", name="시설등록", market_id=market_id)
    c = _template(actor, key="d-c", name="US Agent 지정", market_id=market_id)
    d = _template(actor, key="d-d", name="기업 정보 등록", market_id=market_id)
    _link(actor, "d-ab", a["id"], b["id"])
    _link(actor, "d-ac", a["id"], c["id"])
    _link(actor, "d-bd", b["id"], d["id"])
    _link(actor, "d-cd", c["id"], d["id"])  # 순환 아님 — 성공해야 한다


def test_a_removed_link_no_longer_blocks_the_reverse_direction(
    actor: AuthenticatedUser,
) -> None:
    """해제(soft delete)된 선행은 순환 판정에서 빠진다 — 활성 그래프만 본다"""
    market_id = create_market("GB")
    a = _template(actor, key="r-a", name="SCPN 통보", market_id=market_id)
    b = _template(actor, key="r-b", name="UK RP 지정", market_id=market_id)
    _link(actor, "r-ab", a["id"], b["id"])
    links, _ = service.list_prerequisites(template_id=a["id"], offset=0, limit=50)
    service.remove_prerequisite(actor=actor, template_id=a["id"], link_id=links[0].id)
    _link(actor, "r-ba", b["id"], a["id"])  # 역방향이 이제 성립한다


# ── 금액 쌍 (안건 4 — 서비스 층 안내) ──────────────────────────────────────


def test_an_amount_without_a_currency_is_rejected_with_guidance(
    actor: AuthenticatedUser,
) -> None:
    with pytest.raises(AppError) as exc:
        _template(actor, key="cost-half", estimated_cost_amount=500_000)
    assert exc.value.code == ErrorCode.VALIDATION_INVALID_FIELD
    assert "estimated_cost" in exc.value.detail
