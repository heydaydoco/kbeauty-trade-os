"""K(아키텍처) — 상태머신 전이 표·차단 3층의 구조 계약 (§5.2 / S2-2 조건 E / 판정 ③).

★ 이 파일이 고정하는 것:
  ① **총수**(조건 E) — 허용=사람 21+자동 6=27, 미허용=83. 전이를 더하거나 빼면
    여기와 machine.py 독스트링을 함께 고친다(불일치=대사 — 선례 #11).
  ② **자동 전이의 출발·도달은 달력 파생 3태뿐** — 시스템이 승인을 새로 부여하는
    경로가 구조적으로 없다(4금 ① 법적 판정 비저촉의 기계화).
  ③ **상태 대입의 단일 통로** — row.status를 바꾸는 코드는 service.py 하나뿐
    (auto_confirm 텍스트 스캔 선례 방식 + 공회전 방지).
  ④ **도달성·탈출성** — 도달 불가 상태 0, 종결 밖 상태의 탈출 전이 0건 금지
    (진입만 있고 못 나가는 상태는 설계 결손이다 — 적대 검증 계보).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.modules.certifications import models
from app.modules.certifications.machine import (
    ALLOWED_TRANSITIONS,
    AUTO_TRANSITIONS,
    CERTIFICATION_STATUSES,
    DATE_DERIVED_STATUSES,
    HUMAN_TRANSITIONS,
    REASON_REQUIRED_TO,
    TERMINAL_STATUSES,
)

pytestmark = pytest.mark.group_k

_MODULE_DIR = Path(models.__file__).resolve().parent


# ── 총수 고정 (조건 E) ──────────────────────────────────────────────────────


def test_transition_counts_match_the_approved_totals() -> None:
    """허용 27(사람 21+자동 6)·미허용 83 — 판정 조건 E의 수와 일치한다"""
    assert len(HUMAN_TRANSITIONS) == 21
    assert len(AUTO_TRANSITIONS) == 6
    assert HUMAN_TRANSITIONS.isdisjoint(AUTO_TRANSITIONS)
    assert len(ALLOWED_TRANSITIONS) == 27
    total_ordered_pairs = len(CERTIFICATION_STATUSES) * (len(CERTIFICATION_STATUSES) - 1)
    assert total_ordered_pairs == 110
    assert total_ordered_pairs - len(ALLOWED_TRANSITIONS) == 83


def test_all_transition_endpoints_are_valid_statuses() -> None:
    statuses = set(CERTIFICATION_STATUSES)
    for from_status, to_status in ALLOWED_TRANSITIONS:
        assert from_status in statuses and to_status in statuses
        assert from_status != to_status  # 제자리 전이 없음 — DB CHECK와 한 몸


# ── 자동 전이의 한계 (4금 ① — 자동 승인 부여 경로 부재) ─────────────────────


def test_auto_transitions_stay_inside_date_derived_states() -> None:
    """스윕·수렴의 출발·도달 전부가 {승인, 만료임박, 만료} 안이다"""
    derived = set(DATE_DERIVED_STATUSES)
    for from_status, to_status in AUTO_TRANSITIONS:
        assert from_status in derived
        assert to_status in derived
    # 3태 상호 6방향 전부가 자동 표다 — 빠진 방향이 있으면 수렴이 못 가는
    # 정답이 생긴다(비대칭 결손 — #16 계보).
    assert {(a, b) for a in derived for b in derived if a != b} == AUTO_TRANSITIONS


def test_no_auto_path_enters_the_date_family_from_outside() -> None:
    """진행 상태→승인은 사람 기록뿐 — 시스템이 승인을 '새로' 부여할 수 없다"""
    human_only_into_approved = {
        pair
        for pair in ALLOWED_TRANSITIONS
        if pair[1] == "APPROVED" and pair[0] not in DATE_DERIVED_STATUSES
    }
    assert human_only_into_approved <= HUMAN_TRANSITIONS


# ── 종결·사유·모델 술어의 일치 ──────────────────────────────────────────────


def test_terminal_statuses_have_no_exit() -> None:
    """종결 2태의 탈출 전이 0 — 재도전=신규 인스턴스(활성 한정 유니크)"""
    assert frozenset({"REJECTED", "SUSPENDED"}) == TERMINAL_STATUSES
    for from_status, _to in ALLOWED_TRANSITIONS:
        assert from_status not in TERMINAL_STATUSES


def test_reason_required_matches_terminal_set() -> None:
    """사람 전이의 사유 필수=종결 2태 (만료는 자동 기록 — DB CHECK 3종이 마지막 층)"""
    assert REASON_REQUIRED_TO == TERMINAL_STATUSES


def test_open_predicate_excludes_exactly_the_terminal_statuses() -> None:
    """모델의 유니크 술어와 machine의 종결 정의가 같은 값이다 (이중 정의 대사)"""
    for status in TERMINAL_STATUSES:
        assert status in models._OPEN_PREDICATE
    assert "EXPIRED" not in models._OPEN_PREDICATE  # 만료는 유니크 안(비종결)


# ── 도달성·탈출성 (적대 검증 계보 — 결손 없는 그래프) ───────────────────────


def test_every_status_is_reachable_from_not_started() -> None:
    reachable = {"NOT_STARTED"}
    frontier = {"NOT_STARTED"}
    while frontier:
        frontier = {
            to
            for from_status, to in ALLOWED_TRANSITIONS
            if from_status in frontier and to not in reachable
        }
        reachable |= frontier
    assert reachable == set(CERTIFICATION_STATUSES)


def test_every_non_terminal_status_has_an_exit() -> None:
    exits = {from_status for from_status, _to in ALLOWED_TRANSITIONS}
    for status in CERTIFICATION_STATUSES:
        if status not in TERMINAL_STATUSES:
            assert status in exits, f"{status}에서 나가는 전이가 없습니다"


# ── 상태 대입의 단일 통로 (층3 — auto_confirm 스캔 선례 방식) ────────────────

_STATUS_ASSIGNMENT = re.compile(r"\.status\s*=(?!=)")


def test_status_assignment_lives_only_in_the_service() -> None:
    """certifications 모듈에서 row.status 대입은 service.py에만 있다"""
    offenders: list[str] = []
    for path in sorted(_MODULE_DIR.glob("*.py")):
        if path.name == "service.py":
            continue
        if _STATUS_ASSIGNMENT.search(path.read_text(encoding="utf-8")):
            offenders.append(path.name)
    assert offenders == [], f"상태 대입이 전이 통로 밖에 있습니다: {offenders}"


def test_status_assignment_scan_is_not_idle() -> None:
    """공회전 방지 — 스캔 패턴이 실제 대입 코드를 알아본다"""
    service_source = (_MODULE_DIR / "service.py").read_text(encoding="utf-8")
    assert _STATUS_ASSIGNMENT.search(service_source) is not None
    assert _STATUS_ASSIGNMENT.search("row.status = 'APPROVED'") is not None
    assert _STATUS_ASSIGNMENT.search("row.status == 'APPROVED'") is None


# ── 쓰기 스키마의 status 구조적 부재 (층2 — #16 "PATCH의 status 불가" 전면화) ─


def test_status_is_structurally_absent_from_write_schemas() -> None:
    """생성·편집 요청에 status 필드가 없고, 밖에서 실어 보내면 422다(extra=forbid)"""

    from app.modules.certifications import schemas

    for model in (
        schemas.CertificationCreateRequest,
        schemas.CertificationUpdateRequest,
        schemas.CertificationTransitionRequest,
    ):
        assert "status" not in model.model_fields, model.__name__
        assert model.model_config.get("extra") == "forbid", model.__name__


def test_every_write_schema_forbids_unknown_fields() -> None:
    """쓰기 스키마 전건이 extra=forbid다 — 이름이 `…Request`면 예외 없다.

    S2-3 판정 요청 18: 태스크 쓰기 스키마 2종만 forbid 없이 서 있던 비대칭을
    닫는다. 열거가 아니라 **모듈 스캔**이라, 앞으로 추가되는 쓰기 스키마도
    forbid 없이는 이 테스트를 통과하지 못한다(조용한 무시 금지의 기계화).
    """
    from pydantic import BaseModel

    from app.modules.certifications import schemas

    write_models = sorted(
        name
        for name, obj in vars(schemas).items()
        if isinstance(obj, type) and issubclass(obj, BaseModel) and name.endswith("Request")
    )
    #: 공회전 방지 — 스캔이 실제로 쓰기 스키마를 집어야 한다.
    assert len(write_models) >= 5, write_models

    offenders = [
        name
        for name in write_models
        if getattr(schemas, name).model_config.get("extra") != "forbid"
    ]
    assert offenders == [], f"extra=forbid 없는 쓰기 스키마: {offenders}"


def test_transition_target_literal_matches_the_machine() -> None:
    """스키마의 전이 도달값 열거가 machine의 상태 11값과 같다 (이중 정의 대사)"""
    from typing import get_args

    from app.modules.certifications import schemas

    assert set(get_args(schemas.Status)) == set(CERTIFICATION_STATUSES)
