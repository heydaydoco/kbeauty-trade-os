"""C. 인증 — 준비도 매트릭스 순수 규칙 전수 (§5.3 / S2-3 판정 요청 1·2 / 조건 C).

★ 서비스 층이 아니라 **순수 함수**를 전수 파라미터라이즈한다(S2-2 조건 C 준용 —
  실 HTTP를 110회 돌리지 않는다). 규칙이 DB를 모르므로 상태 11값 × 달력 경계를 표
  하나로 고정할 수 있다.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.modules.certifications.calendar import derived_date_status, effective_status, overdue_days
from app.modules.certifications.machine import CERTIFICATION_STATUSES
from app.modules.readiness.rules import (
    GRAY,
    GREEN,
    RED,
    STATUS_COLOR,
    YELLOW,
    InstanceFacts,
    Requirement,
    RequirementResult,
    aggregate_colors,
    evaluate_requirement,
    merge_component_results,
    requirement_color,
    summarize,
)

pytestmark = pytest.mark.group_c

TODAY = date(2026, 9, 29)
FAR = date(2027, 12, 31)


# ── 상태 → 색 (판정 요청 1 — 11상태 전수) ────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("NOT_STARTED", RED),  # 미착수 = 🔴 (착수 사실 0 = 실질 미충족)
        ("PREPARING", YELLOW),
        ("SUBMITTED", YELLOW),
        ("IN_REVIEW", YELLOW),
        ("SUPPLEMENTING", YELLOW),
        ("APPROVED", GREEN),
        ("EXPIRING", YELLOW),
        ("RENEWING", YELLOW),  # 미도과
        ("REJECTED", RED),
        ("EXPIRED", RED),
        ("SUSPENDED", RED),
    ],
)
def test_every_status_has_its_color(status: str, expected: str) -> None:
    """상태 11값이 각자의 색을 가진다 — 판정 요청 1의 표 그대로"""
    assert requirement_color(status) == expected


def test_the_table_covers_exactly_the_eleven_statuses() -> None:
    """색 표는 상태 머신의 11값과 1:1 — 상태가 늘면 이 표도 함께 움직인다"""
    assert set(STATUS_COLOR) == set(CERTIFICATION_STATUSES)
    assert len(STATUS_COLOR) == 11


def test_no_active_instance_is_red() -> None:
    """활성 인스턴스 부재 = 🔴 (fail-closed)"""
    assert requirement_color(None) == RED


def test_renewing_past_expiry_is_red() -> None:
    """갱신중 도과 = 🔴, 미도과 = 🟡 (안건 ⑦ 셀 반영)"""
    assert requirement_color("RENEWING", renewing_overdue=True) == RED
    assert requirement_color("RENEWING", renewing_overdue=False) == YELLOW


# ── 집계 = 최악값 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("colors", "expected"),
    [
        ([], GRAY),  # 필수 0건 = 대상외
        ([GREEN], GREEN),
        ([GREEN, GREEN], GREEN),
        ([GREEN, YELLOW], YELLOW),
        ([YELLOW, GREEN, GREEN], YELLOW),
        ([GREEN, RED], RED),
        ([YELLOW, RED, GREEN], RED),
        ([RED], RED),
    ],
)
def test_the_cell_is_the_worst_value(colors: list[str], expected: str) -> None:
    """셀 = 최악값 집계(🔴 > 🟡 > 🟢), 요건이 하나도 없을 때만 ⚪"""
    assert aggregate_colors(colors) == expected


def test_summary_counts_add_up() -> None:
    results = [
        _result(GREEN),
        _result(YELLOW),
        _result(YELLOW),
        _result(RED),
    ]
    summary = summarize(results)
    assert (summary.color, summary.required) == (RED, 4)
    assert (summary.approved, summary.in_progress, summary.unmet) == (1, 2, 1)
    assert summary.approved + summary.in_progress + summary.unmet == summary.required


def test_an_empty_cell_is_gray_with_zero_counts() -> None:
    summary = summarize([])
    assert (summary.color, summary.required, summary.approved) == (GRAY, 0, 0)


# ── 실효 상태 — 저장 상태를 그대로 믿지 않는다 (달력 파생 3태) ────────────────


@pytest.mark.parametrize(
    ("stored", "expires_on", "lead", "expected_color"),
    [
        # 스윕 전 창: 저장은 승인인데 만료일이 어제 → 🔴 (과신 방지)
        ("APPROVED", date(2026, 9, 28), 90, RED),
        # 저장은 승인인데 리드 창에 들어옴 → 🟡
        ("APPROVED", date(2026, 10, 15), 90, YELLOW),
        # 리드 창 밖 → 🟢
        ("APPROVED", FAR, 90, GREEN),
        # 리드 미설정(임의 기본값 발명 금지) → 임박 없음
        ("APPROVED", date(2026, 10, 1), None, GREEN),
        # 무기한 → 🟢
        ("APPROVED", None, 90, GREEN),
        # 만료일 당일은 아직 유효 (도과는 다음 날부터)
        ("APPROVED", TODAY, None, GREEN),
        # 저장 만료임박인데 만료일이 정정돼 리드 밖 → 🟢 (복귀 방향)
        ("EXPIRING", FAR, 90, GREEN),
        # 저장 만료인데 만료일이 정정돼 미래 → 🟢
        ("EXPIRED", FAR, 90, GREEN),
        # 저장 만료, 정정 없음 → 🔴
        ("EXPIRED", date(2026, 1, 1), 90, RED),
    ],
)
def test_calendar_derived_states_are_recomputed(
    stored: str, expires_on: date | None, lead: int | None, expected_color: str
) -> None:
    """만료일 기준으로 달력 파생 3태를 다시 계산한다 — 스윕(06:00)의 지각에 종속되지 않는다"""
    result = evaluate_requirement(
        _requirement(),
        InstanceFacts(1, stored, expires_on, lead),
        base_date=TODAY,
    )
    assert result.color == expected_color


@pytest.mark.parametrize(
    ("stored", "expires_on", "expected_color", "note"),
    [
        ("RENEWING", date(2026, 9, 1), RED, "갱신중이지만 만료일이 지났습니다."),
        ("RENEWING", FAR, YELLOW, None),
        ("RENEWING", None, YELLOW, None),  # 무기한 갱신중은 도과가 없다
        ("PREPARING", date(2020, 1, 1), YELLOW, None),  # 진행 상태는 날짜로 바꾸지 않는다
        ("IN_REVIEW", date(2020, 1, 1), YELLOW, None),
        ("REJECTED", date(2020, 1, 1), RED, None),
    ],
)
def test_non_calendar_states_keep_their_stored_status(
    stored: str, expires_on: date | None, expected_color: str, note: str | None
) -> None:
    """진행·갱신중·종결은 사람이 기록한 전이의 결과 — 날짜로 뒤집지 않는다(도과 표시만)"""
    result = evaluate_requirement(
        _requirement(), InstanceFacts(1, stored, expires_on, 90), base_date=TODAY
    )
    assert result.color == expected_color
    assert result.note == note
    assert result.status == stored  # 실효 상태 = 저장 상태 그대로


def test_missing_manufacturer_is_red_even_with_an_instance() -> None:
    """제조사 미지정 시설 요건 = 🔴 fail-closed — 대상이 없어 충족을 확인할 수 없다"""
    requirement = Requirement(7, "GMP 증명", "FACILITY", "FACILITY", None, target_missing=True)
    result = evaluate_requirement(
        requirement, InstanceFacts(9, "APPROVED", FAR, 90), base_date=TODAY
    )
    assert result.color == RED
    assert result.status is None
    assert "제조사" in (result.note or "")


def test_no_instance_is_red_with_a_reason() -> None:
    result = evaluate_requirement(_requirement(), None, base_date=TODAY)
    assert (result.color, result.status, result.certification_id) == (RED, None, None)
    assert result.note


# ── 세트 롤업 ────────────────────────────────────────────────────────────────


def test_component_results_are_tagged_and_worst_of() -> None:
    own = [_result(GREEN, template_id=1, target_id=10)]
    merged = merge_component_results(
        own,
        [
            (101, "CMP-A", [_result(GREEN, template_id=2, target_type="SKU", target_id=101)]),
            (102, "CMP-B", [_result(RED, template_id=2, target_type="SKU", target_id=102)]),
        ],
    )
    assert summarize(merged).color == RED
    assert [(item.via_component_sku_code) for item in merged] == [None, "CMP-A", "CMP-B"]


def test_shared_requirements_are_counted_once() -> None:
    """같은 (템플릿, 대상)은 한 번만 센다 — 기업 단위 요건이 구성품마다 반복돼도 1건"""
    company = _result(GREEN, template_id=5, axis="COMPANY", target_type="COMPANY", target_id=None)
    merged = merge_component_results(
        [],
        [(1, "A", [company]), (2, "B", [company]), (3, "C", [company])],
    )
    assert len(merged) == 1
    assert merged[0].via_component_sku_code == "A"  # 첫 출처만


def test_same_product_requirement_is_counted_once_across_components() -> None:
    product = _result(YELLOW, template_id=6, axis="PRODUCT", target_type="PRODUCT", target_id=77)
    merged = merge_component_results([], [(1, "A", [product]), (2, "B", [product])])
    assert len(merged) == 1


def test_missing_manufacturers_stay_separate_per_component() -> None:
    """제조사 미지정은 구성품마다 별개다 — 하나로 뭉개면 어느 구성품이 문제인지 사라진다"""
    missing = _result(
        RED, template_id=8, axis="FACILITY", target_type="FACILITY", target_id=None, missing=True
    )
    merged = merge_component_results([], [(1, "A", [missing]), (2, "B", [missing])])
    assert [item.via_component_sku_code for item in merged] == ["A", "B"]


def test_own_requirement_wins_the_dedup_over_a_component_copy() -> None:
    company = _result(GREEN, template_id=5, axis="COMPANY", target_type="COMPANY", target_id=None)
    merged = merge_component_results([company], [(1, "A", [company])])
    assert len(merged) == 1
    assert merged[0].via_component_sku_id is None


# ── 달력 함수 (스윕·도과 표시·매트릭스가 공유하는 정의) ───────────────────────


@pytest.mark.parametrize(
    ("expires_on", "lead", "expected"),
    [
        (None, 90, "APPROVED"),
        (date(2026, 9, 28), None, "EXPIRED"),
        (TODAY, None, "APPROVED"),  # 만료일 당일 = 유효
        (date(2026, 12, 28), 90, "EXPIRING"),  # 정확히 리드 경계(90일 전)
        (date(2026, 12, 29), 90, "APPROVED"),  # 리드 경계 하루 밖
        (date(2026, 10, 1), None, "APPROVED"),  # 리드 부재 = 임박 단계 없음
    ],
)
def test_derived_date_status_boundaries(
    expires_on: date | None, lead: int | None, expected: str
) -> None:
    assert derived_date_status(expires_on, lead, TODAY) == expected


def test_effective_status_leaves_other_states_alone() -> None:
    assert effective_status("PREPARING", date(2020, 1, 1), 90, TODAY) == "PREPARING"
    assert effective_status("RENEWING", date(2020, 1, 1), 90, TODAY) == "RENEWING"
    assert effective_status("APPROVED", date(2020, 1, 1), 90, TODAY) == "EXPIRED"


@pytest.mark.parametrize(
    ("status", "expires_on", "expected"),
    [
        ("APPROVED", date(2026, 9, 28), 1),
        ("APPROVED", TODAY, None),
        ("RENEWING", date(2026, 9, 1), 28),
        ("REJECTED", date(2020, 1, 1), None),
        ("SUSPENDED", date(2020, 1, 1), None),
        ("APPROVED", None, None),
    ],
)
def test_overdue_days_definition(
    status: str, expires_on: date | None, expected: int | None
) -> None:
    assert overdue_days(status, expires_on, TODAY) == expected


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────


def _requirement() -> Requirement:
    return Requirement(1, "MoCRA 제품 리스팅", "SKU", "SKU", 10)


def _result(
    color: str,
    *,
    template_id: int = 1,
    axis: str = "SKU",
    target_type: str = "SKU",
    target_id: int | None = 10,
    missing: bool = False,
) -> RequirementResult:
    return RequirementResult(
        template_id=template_id,
        template_name=f"요건 {template_id}",
        axis=axis,
        target_type=target_type,
        target_id=target_id,
        status=None,
        color=color,
        certification_id=None,
        note=None,
        target_missing=missing,
    )
