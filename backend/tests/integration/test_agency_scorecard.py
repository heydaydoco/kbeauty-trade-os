"""C. 대행사 스코어카드 — 소요일·보완율·현행 계약 (§5.4 / S2-4 PR-1 안건 ③).

★ 원료는 상태 이력(불변)·인증 본체·계약 대장이다. 이력 행은 실제 전이 서비스를 거치지 않고
  직접 심는다(시각을 통제해 KST 날짜 경계를 재현하려는 것 — 전이 규칙 자체는 상태머신 테스트의 몫).
★ 저장하지 않는 계산값이라 "정정이 즉시 반영된다"가 곧 정의다 — 재조회만으로 결과가 바뀐다.
"""

from __future__ import annotations

import itertools
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.db.uow import unit_of_work
from app.core.time import today_kst, utcnow
from app.modules.certifications.models import Certification, CertificationStatusLog
from app.modules.collaboration import scorecard
from app.modules.collaboration.models import AgencyContract
from app.modules.partners.models import PartnerTypeLink
from tests.support.factories import (
    create_certification_instance,
    create_partner,
    create_requirement_template,
    create_sku,
)
from tests.support.sqlcount import count_statements

pytestmark = pytest.mark.group_c

_SEQ = itertools.count(1)


def _agency(name: str = "가 대행사", *, code: str | None = None) -> int:
    return create_partner(code or f"AGY-{next(_SEQ)}", name_ko=name, types=("CERT_AGENCY",))


def _case(agency_id: int, *, status: str = "PREPARING", deleted: bool = False) -> int:
    number = next(_SEQ)
    template_id = create_requirement_template("US", name=f"스코어 요건 {number}")
    certification_id = create_certification_instance(
        template_id,
        "SKU",
        create_sku(f"SKU-SC-{number}"),
        status=status,
        handling_mode="AGENCY",
        agency_partner_id=agency_id,
    )
    if deleted:
        with unit_of_work() as uow:
            row = uow.session.get(Certification, certification_id)
            assert row is not None
            row.deleted_at = utcnow()
    return certification_id


def _log(certification_id: int, to_status: str, at: datetime) -> None:
    with unit_of_work() as uow:
        uow.session.add(
            CertificationStatusLog(
                certification_id=certification_id,
                occurred_at=at,
                from_status="NOT_STARTED" if to_status == "PREPARING" else "PREPARING",
                to_status=to_status,
            )
        )


def _at(month: int, day: int, hour: int = 3) -> datetime:
    return datetime(2026, month, day, hour, 0, tzinfo=UTC)


def _contract(
    agency_id: int,
    number: str,
    *,
    start: date,
    end: date | None = None,
    fee: int | None = None,
    currency: str | None = None,
    deleted: bool = False,
) -> None:
    with unit_of_work() as uow:
        row = AgencyContract(
            partner_id=agency_id,
            contract_no=number,
            start_on=start,
            end_on=end,
            fee_amount=fee,
            fee_currency=currency,
        )
        if deleted:
            row.deleted_at = utcnow()
        uow.session.add(row)


def _one(agency_id: int) -> scorecard.AgencyScoreView:
    views, _ = scorecard.scorecard(offset=0, limit=200)
    (found,) = [view for view in views if view.partner_id == agency_id]
    return found


# ── 명단 ────────────────────────────────────────────────────────────────────


def test_an_empty_system_has_an_empty_scorecard() -> None:
    """대행사가 없으면 빈 목록(total 0) — 오류가 아니다"""
    assert scorecard.scorecard(offset=0, limit=50) == ([], 0)


def test_an_agency_without_cases_appears_with_empty_metrics() -> None:
    """인증대행 유형 거래처는 실적이 없어도 행이 있다 — 분모 0은 None(0%로 위장 금지)"""
    agency_id = _agency()
    view = _one(agency_id)
    assert (view.case_count, view.submitted_count, view.approved_count) == (0, 0, 0)
    assert view.supplement_rate is None
    assert (view.lead_sample_count, view.lead_days_avg, view.lead_days_median) == (0, None, None)
    assert view.current_contract is None


def test_non_agency_partners_are_not_listed() -> None:
    """인증대행 유형도 아니고 지정된 인증도 없는 거래처는 명단에 없다"""
    _agency("대행사")
    create_partner("SUP-1", name_ko="공급사", types=("SUPPLIER",))
    views, total = scorecard.scorecard(offset=0, limit=50)
    assert total == 1 and [view.partner_name for view in views] == ["대행사"]


def test_an_agency_with_results_stays_listed_after_losing_its_type() -> None:
    """유형이 나중에 해제돼도 지정된 인증이 있는 대행사는 실적과 함께 남는다"""
    agency_id = _agency()
    _case(agency_id)
    with unit_of_work() as uow:
        for link in uow.session.execute(
            select(PartnerTypeLink).where(PartnerTypeLink.partner_id == agency_id)
        ).scalars():
            link.deleted_at = utcnow()
    assert _one(agency_id).case_count == 1


def test_deleted_partners_are_not_listed() -> None:
    """삭제된 거래처는 명단에서 빠진다"""
    agency_id = _agency()
    from app.modules.partners.models import Partner

    with unit_of_work() as uow:
        partner = uow.session.get(Partner, agency_id)
        assert partner is not None
        partner.deleted_at = utcnow()
    assert scorecard.scorecard(offset=0, limit=50) == ([], 0)


def test_the_list_is_paged_and_ordered_by_name() -> None:
    """이름순 정렬·페이지 분할 — total은 분할과 무관"""
    for name in ("다 대행사", "가 대행사", "나 대행사"):
        _agency(name)
    first, total = scorecard.scorecard(offset=0, limit=2)
    second, _ = scorecard.scorecard(offset=2, limit=2)
    assert total == 3
    assert [view.partner_name for view in first] == ["가 대행사", "나 대행사"]
    assert [view.partner_name for view in second] == ["다 대행사"]


# ── 담당 건수 ───────────────────────────────────────────────────────────────


def test_cases_count_every_status_but_not_deleted_or_direct_ones() -> None:
    """담당 건수 — 종결 포함 삭제되지 않은 대행 처리 건. 직접 처리·삭제 건은 세지 않는다"""
    agency_id = _agency()
    for status in ("PREPARING", "APPROVED", "REJECTED", "SUSPENDED"):
        _case(agency_id, status=status)
    _case(agency_id, deleted=True)
    # 직접 처리 건 — 대행사 지정이 없어 귀속되지 않는다
    template_id = create_requirement_template("US", name="직접 처리 요건")
    create_certification_instance(template_id, "SKU", create_sku("SKU-DIRECT"), status="PREPARING")
    assert _one(agency_id).case_count == 4


def test_cases_are_attributed_to_their_own_agency() -> None:
    """대행사별 귀속이 섞이지 않는다"""
    first, second = _agency("가"), _agency("나")
    _case(first)
    _case(first)
    _case(second)
    assert (_one(first).case_count, _one(second).case_count) == (2, 1)


# ── 소요일 ──────────────────────────────────────────────────────────────────


def test_lead_time_is_the_kst_date_gap_between_first_preparation_and_first_approval() -> None:
    """소요일 = 첫 서류준비 진입일 → 첫 승인 진입일. 평균·중앙값·표본 수, 승인 미도달 건은 표본 밖"""
    agency_id = _agency()
    ten, four, eleven = _case(agency_id), _case(agency_id), _case(agency_id)
    for certification_id, days in ((ten, 10), (four, 4), (eleven, 11)):
        _log(certification_id, "PREPARING", _at(9, 1))
        _log(certification_id, "APPROVED", _at(9, 1) + timedelta(days=days))
    unfinished = _case(agency_id)
    _log(unfinished, "PREPARING", _at(9, 1))
    view = _one(agency_id)
    assert view.case_count == 4 and view.approved_count == 3
    assert view.lead_sample_count == 3
    assert view.lead_days_avg == 8.3  # (10+4+11)/3 = 8.33 → 소수 1자리
    assert view.lead_days_median == 10.0


def test_the_median_of_an_even_sample_is_the_midpoint() -> None:
    """짝수 표본의 중앙값은 가운데 두 값의 평균"""
    agency_id = _agency()
    for days in (4, 10):
        certification_id = _case(agency_id)
        _log(certification_id, "PREPARING", _at(9, 1))
        _log(certification_id, "APPROVED", _at(9, 1) + timedelta(days=days))
    view = _one(agency_id)
    assert view.lead_days_median == 7.0 and view.lead_days_avg == 7.0


def test_lead_days_are_computed_on_kst_dates_not_utc_dates() -> None:
    """UTC 9/1 15:30 = KST 9/2 → 승인 UTC 9/11 14:00 = KST 9/11 — 9일(UTC 날짜로 재면 10일)"""
    agency_id = _agency()
    certification_id = _case(agency_id)
    _log(certification_id, "PREPARING", _at(9, 1, hour=15) + timedelta(minutes=30))
    _log(certification_id, "APPROVED", _at(9, 11, hour=14))
    assert _one(agency_id).lead_days_avg == 9.0


def test_approval_on_the_same_day_is_zero_days_not_a_missing_sample() -> None:
    """같은 날 승인은 0일이고 표본에 든다"""
    agency_id = _agency()
    certification_id = _case(agency_id)
    _log(certification_id, "PREPARING", _at(9, 1, hour=1))
    _log(certification_id, "APPROVED", _at(9, 1, hour=6))
    view = _one(agency_id)
    assert (view.lead_sample_count, view.lead_days_avg) == (1, 0.0)


def test_only_the_first_approval_counts_so_renewals_do_not_shift_it() -> None:
    """갱신 재승인은 첫 승인 뒤의 사건이라 소요일에 영향이 없다 — 최초 진입만 쓴다"""
    agency_id = _agency()
    certification_id = _case(agency_id)
    _log(certification_id, "PREPARING", _at(9, 1))
    _log(certification_id, "APPROVED", _at(9, 6))
    _log(certification_id, "APPROVED", _at(12, 6))  # 갱신 재승인
    view = _one(agency_id)
    assert view.lead_days_avg == 5.0 and view.approved_count == 1


def test_a_case_without_history_still_counts_as_a_case_only() -> None:
    """이력이 전혀 없는 건은 담당 건수에만 든다 — 표본·분모에는 들지 않는다"""
    agency_id = _agency()
    _case(agency_id)
    view = _one(agency_id)
    assert view.case_count == 1 and view.lead_sample_count == 0 and view.submitted_count == 0


# ── 보완율 ──────────────────────────────────────────────────────────────────


def test_the_supplement_rate_counts_cases_not_loops() -> None:
    """보완율 = 보완요청을 거친 건 ÷ 신청제출 도달 건 — 재신청 루프가 있어도 건당 1"""
    agency_id = _agency()
    for index in range(4):
        certification_id = _case(agency_id)
        _log(certification_id, "PREPARING", _at(9, 1))
        _log(certification_id, "SUBMITTED", _at(9, 2))
        if index == 0:
            for day in (3, 5, 7):  # 보완 루프 3회 — 그래도 1건
                _log(certification_id, "SUPPLEMENTING", _at(9, day))
    never_submitted = _case(agency_id)
    _log(never_submitted, "PREPARING", _at(9, 1))
    view = _one(agency_id)
    assert (view.submitted_count, view.supplemented_count) == (4, 1)
    assert view.supplement_rate == 0.25
    assert view.case_count == 5  # 제출 전 건은 분모에서 빠진다


def test_the_rate_is_none_when_nothing_was_submitted() -> None:
    """제출 도달 건이 없으면 None — 0%가 아니다(아직 모른다)"""
    agency_id = _agency()
    certification_id = _case(agency_id)
    _log(certification_id, "PREPARING", _at(9, 1))
    assert _one(agency_id).supplement_rate is None


def test_a_zero_rate_is_reported_as_zero_when_cases_were_submitted() -> None:
    """제출은 있었으나 보완이 한 번도 없으면 0.0이다 — None(모름)과 구분"""
    agency_id = _agency()
    certification_id = _case(agency_id)
    _log(certification_id, "SUBMITTED", _at(9, 2))
    assert _one(agency_id).supplement_rate == 0.0


# ── 현행 계약(비용) ─────────────────────────────────────────────────────────


def test_the_current_contract_is_the_one_in_force_today() -> None:
    """현행 계약 = 오늘이 기간 안인 계약 — 종료·미래·삭제 계약은 제외, 수수료는 최소단위 그대로"""
    today = today_kst()
    agency_id = _agency()
    _contract(agency_id, "OLD", start=today - timedelta(days=90), end=today - timedelta(days=1))
    _contract(agency_id, "FUTURE", start=today + timedelta(days=1))
    _contract(agency_id, "DELETED", start=today - timedelta(days=5), deleted=True)
    _contract(agency_id, "NOW", start=today - timedelta(days=30), fee=123456, currency="USD")
    contract = _one(agency_id).current_contract
    assert contract is not None
    assert (contract.contract_no, contract.fee_amount, contract.fee_currency) == (
        "NOW",
        123456,
        "USD",
    )


def test_overlapping_contracts_prefer_the_later_start() -> None:
    """겹치면 시작일이 늦은 계약이 현행 — 같으면 id가 큰 것"""
    today = today_kst()
    agency_id = _agency()
    _contract(agency_id, "A", start=today - timedelta(days=60))
    _contract(agency_id, "B", start=today - timedelta(days=10))
    _contract(agency_id, "C", start=today - timedelta(days=10))
    contract = _one(agency_id).current_contract
    assert contract is not None and contract.contract_no == "C"


def test_contract_boundaries_are_inclusive() -> None:
    """시작일·종료일 당일은 현행 — 경계 포함"""
    today = today_kst()
    starts_today, ends_today = _agency("가"), _agency("나")
    _contract(starts_today, "S", start=today)
    _contract(ends_today, "E", start=today - timedelta(days=5), end=today)
    assert _one(starts_today).current_contract is not None
    assert _one(ends_today).current_contract is not None


def test_currencies_are_shown_as_written_never_merged() -> None:
    """서로 다른 대행사의 통화가 섞여도 환산·합산이 없다 — 각자의 계약 수수료 그대로"""
    today = today_kst()
    first, second = _agency("가"), _agency("나")
    _contract(first, "K", start=today, fee=500000, currency="KRW")
    _contract(second, "U", start=today, fee=45000, currency="USD")
    a, b = _one(first).current_contract, _one(second).current_contract
    assert a is not None and b is not None
    assert (a.fee_amount, a.fee_currency, b.fee_amount, b.fee_currency) == (
        500000,
        "KRW",
        45000,
        "USD",
    )


# ── 계산값 — 정정이 즉시 반영 ────────────────────────────────────────────────


def test_corrections_show_up_on_the_next_read_without_any_batch() -> None:
    """저장하지 않는 계산값 — 대행사 재지정·이력 추가가 다음 조회에 곧바로 반영된다"""
    first, second = _agency("가"), _agency("나")
    certification_id = _case(first)
    assert (_one(first).case_count, _one(second).case_count) == (1, 0)
    with unit_of_work() as uow:
        row = uow.session.get(Certification, certification_id)
        assert row is not None
        row.agency_partner_id = second
    assert (_one(first).case_count, _one(second).case_count) == (0, 1)


# ── 질의 수 ────────────────────────────────────────────────────────────────


def test_the_query_count_does_not_grow_with_the_number_of_agencies() -> None:
    """질의 수는 대행사 수와 무관(§18.4) — 1곳과 12곳이 같다. 측정이 0을 세는 빈 검증 방지"""
    today = today_kst()
    first = _agency("가 대행사 0")
    certification_id = _case(first)
    _log(certification_id, "PREPARING", _at(9, 1))
    _log(certification_id, "APPROVED", _at(9, 5))
    _contract(first, "C0", start=today, fee=1, currency="USD")
    single = count_statements(lambda: scorecard.scorecard(offset=0, limit=50))
    for index in range(1, 12):
        agency_id = _agency(f"가 대행사 {index}")
        extra = _case(agency_id)
        _log(extra, "PREPARING", _at(9, 1))
        _log(extra, "SUBMITTED", _at(9, 2))
        _contract(agency_id, f"C{index}", start=today, fee=1, currency="USD")
    many = count_statements(lambda: scorecard.scorecard(offset=0, limit=50))
    assert single > 0
    assert many == single
