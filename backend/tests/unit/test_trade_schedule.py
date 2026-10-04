"""A·K. 기일 산식 순수 함수 — design-B §B20 경계 표(함수 층) · GC-A16·A17·A18·A19 (S3-2 PR-2a / ADR-0080·0081·0082).

WBS S3-2 DoD ① "T/T와 L/C 만기 계산 분기"와 검증 K "L/C 제시기한 MIN·tolerance 상하한"이 이 파일에서 green이 된다
(L/C는 순수 함수로 충족 — 운영 경로는 UNKNOWN, ADR-0081). "오늘"은 전부 인자로 주입한다(시계 무의존).
행 번호(`B20-nn`)는 design-B §B20 경계 표의 행이고, 공식 케이스 ID는 GC v1.5(A16~A19)다(X-30).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

import pytest

from app.modules.holidays.calc import HolidayFlag, holiday_flag
from app.modules.trade_docs import schedule as s
from app.modules.trade_docs.schedule import (
    AnchorContext,
    Basis,
    CustomsState,
    DateValue,
    DueReason,
    DueResult,
    DueStatus,
    LcInputs,
    LoadingState,
)

pytestmark = pytest.mark.group_a


@dataclass(frozen=True)
class Terms:
    """결제조건 4열(선적 헤더 사본 모양 — `TermsLike`)."""

    payment_type: str | None
    advance_pct_bp: int | None = None
    balance_anchor: str | None = None
    balance_days: int | None = None


def _ok(value: date, basis: Basis) -> DueResult:
    return DueResult(DueStatus.OK, value, basis, None)


def _unknown(reason: DueReason) -> DueResult:
    return DueResult(DueStatus.UNKNOWN, None, None, reason)


ACT, PLN = Basis.ACTUAL, Basis.PLANNED


# ── GC-A16 대금만기 분기 (B20 행 01~10·32) ────────────────────────────────────


@pytest.mark.golden
def test_gc_a16_tt_deferred_bl_plus_30_crosses_the_year() -> None:
    """GC-A16 / B20-01 — T/T 후불, B/L일+30, B/L 실적 2026-12-15 → 2027-01-14(ACTUAL — 연도 넘김)"""
    ctx = AnchorContext(bl=s.effective(None, date(2026, 12, 15)))
    got = s.payment_due(Terms("TT_DEFERRED", None, "BL_DATE", 30), ctx, None)
    assert got == _ok(date(2027, 1, 14), ACT)


@pytest.mark.golden
def test_gc_a16_negative_days_on_etd_planned_then_recomputed_on_actual() -> None:
    """GC-A16 / B20-02·03 — 선수금 30%, ETD −7, ETD 계획 11-05 → 10-29(PLANNED) / 실적 11-09 입력 → 11-02(ACTUAL — 실적 우선 재계산)"""
    terms = Terms("TT_ADVANCE", 3000, "ETD_DATE", -7)
    planned_only = AnchorContext(etd=s.effective(date(2026, 11, 5), None))
    assert s.payment_due(terms, planned_only, None) == _ok(date(2026, 10, 29), PLN)
    with_actual = AnchorContext(etd=s.effective(date(2026, 11, 5), date(2026, 11, 9)))
    assert s.payment_due(terms, with_actual, None) == _ok(date(2026, 11, 2), ACT)


@pytest.mark.golden
def test_gc_a16_full_advance_is_not_applicable_not_unknown() -> None:
    """GC-A16 / B20-04 — T/T 선수금 100% → NOT_APPLICABLE(잔금 없음 ≠ UNKNOWN)"""
    got = s.payment_due(Terms("TT_ADVANCE", 10000), AnchorContext(), None)
    assert got.status is DueStatus.NOT_APPLICABLE and got.reason is DueReason.NO_BALANCE
    assert got.value is None
    # 99.99%는 잔금이 있다 — 앵커가 없으면 UNKNOWN(NOT_APPLICABLE로 새지 않는다)
    partial = s.payment_due(Terms("TT_ADVANCE", 9999, "ETD_DATE", 0), AnchorContext(), None)
    assert partial == _unknown(DueReason.ANCHOR_PENDING)


@pytest.mark.golden
def test_gc_a16_invoice_anchor_is_unknown_never_etd() -> None:
    """GC-A16 / B20-05 — 앵커 인보이스일 → UNKNOWN INVOICE_NOT_ISSUED(ETD·B/L·ETA가 있어도 대체 금지)"""
    full = AnchorContext(
        order_at=datetime(2026, 10, 3, tzinfo=UTC),
        etd=DateValue(date(2026, 11, 1), ACT),
        bl=DateValue(date(2026, 11, 1), ACT),
        eta=DateValue(date(2026, 11, 20), ACT),
    )
    got = s.payment_due(Terms("TT_DEFERRED", None, "INVOICE_DATE", 60), full, None)
    assert got == _unknown(DueReason.INVOICE_NOT_ISSUED)


@pytest.mark.golden
def test_gc_a16_missing_anchor_is_unknown_not_today_or_zero() -> None:
    """GC-A16 / B20-06 — 앵커 ETD인데 계획·실적 모두 없음 → UNKNOWN ANCHOR_PENDING(0일·오늘 대체 금지)"""
    for anchor in ("ETD_DATE", "BL_DATE", "ARRIVAL_DATE", "ORDER_DATE"):
        got = s.payment_due(Terms("TT_DEFERRED", None, anchor, 0), AnchorContext(), None)
        assert got == _unknown(DueReason.ANCHOR_PENDING), anchor
    receipt = s.payment_due(Terms("TT_DEFERRED", None, "RECEIPT_DATE", 30), AnchorContext(), None)
    assert receipt == _unknown(DueReason.RECEIPT_NOT_RECORDED)
    # 다른 앵커의 날짜가 있어도 자기 앵커가 없으면 UNKNOWN — 이웃 마일스톤으로 대체하지 않는다
    other = DateValue(date(2026, 11, 1), ACT)
    neighbours = {
        "ETD_DATE": AnchorContext(order_at=datetime(2026, 10, 1, tzinfo=UTC), bl=other, eta=other),
        "BL_DATE": AnchorContext(order_at=datetime(2026, 10, 1, tzinfo=UTC), etd=other, eta=other),
        "ARRIVAL_DATE": AnchorContext(
            order_at=datetime(2026, 10, 1, tzinfo=UTC), etd=other, bl=other
        ),
        "ORDER_DATE": AnchorContext(etd=other, bl=other, eta=other),
    }
    for anchor, ctx in neighbours.items():
        got = s.payment_due(Terms("TT_DEFERRED", None, anchor, 0), ctx, None)
        assert got == _unknown(DueReason.ANCHOR_PENDING), anchor


@pytest.mark.golden
def test_gc_a16_order_date_is_the_kst_date_of_the_export_confirmation() -> None:
    """GC-A16 / B20-07 — 주문일+0, 수출 SO confirmed_at 2026-10-03T15:30Z → 2026-10-04(KST 날짜)"""
    ctx = AnchorContext(order_at=datetime(2026, 10, 3, 15, 30, tzinfo=UTC))
    got = s.payment_due(Terms("TT_DEFERRED", None, "ORDER_DATE", 0), ctx, None)
    assert got == _ok(date(2026, 10, 4), ACT)
    # KST 자정 경계: 14:59:59Z = KST 23:59:59(10-03) / 15:00Z = KST 10-04 00:00
    assert s.kst_date(datetime(2026, 10, 3, 14, 59, 59, tzinfo=UTC)) == date(2026, 10, 3)
    assert s.kst_date(datetime(2026, 10, 3, 15, 0, tzinfo=UTC)) == date(2026, 10, 4)


@pytest.mark.golden
def test_gc_a16_import_order_date_is_po_frozen_at_not_doc_date() -> None:
    """GC-A16 / B20-32(R-29) — 수입 ORDER_DATE = PO frozen_at 2026-10-03T15:30Z의 KST 날짜 2026-10-04(doc_date 10-01 미사용)

    컨텍스트에 doc_date 필드 자체가 없다 — 조립자가 넣을 자리가 없어 사람 입력 날짜가 섞일 수 없다."""
    frozen_at = datetime(2026, 10, 3, 15, 30, tzinfo=UTC)
    got = s.payment_due(
        Terms("TT_DEFERRED", None, "ORDER_DATE", 0), AnchorContext(order_at=frozen_at), None
    )
    assert got == _ok(date(2026, 10, 4), ACT)
    assert "doc_date" not in AnchorContext.__dataclass_fields__


@pytest.mark.golden
def test_gc_a16_lc_operational_path_is_unknown() -> None:
    """GC-A16 / B20-08 — L/C 운영 경로(L/C 조건 입력 없음) → UNKNOWN LC_TERMS_NOT_REGISTERED(ADR-0081)"""
    ctx = AnchorContext(bl=DateValue(date(2027, 3, 1), ACT), etd=DateValue(date(2027, 3, 1), ACT))
    assert s.payment_due(Terms("LC"), ctx, None) == _unknown(DueReason.LC_TERMS_NOT_REGISTERED)


@pytest.mark.golden
def test_gc_a16_lc_sight_is_the_negotiation_date() -> None:
    """GC-A16 / B20-09 — L/C SIGHT·네고 2027-02-10 → 2027-02-10 · 네고일 없음 → UNKNOWN"""
    assert s.lc_payment_due("SIGHT", date(2027, 2, 10), None, None) == _ok(date(2027, 2, 10), ACT)
    assert s.lc_payment_due("SIGHT", None, date(2027, 2, 1), 30) == _unknown(
        DueReason.LC_INPUT_MISSING
    )
    via_terms = s.payment_due(
        Terms("LC"), AnchorContext(), LcInputs("SIGHT", negotiated_on=date(2027, 2, 10))
    )
    assert via_terms == _ok(date(2027, 2, 10), ACT)


@pytest.mark.golden
def test_gc_a16_lc_usance_is_acceptance_plus_n_and_missing_is_unknown() -> None:
    """GC-A16 / B20-10 — USANCE 90·인수 2027-01-31 → 2027-05-01 / 인수일 없음 → UNKNOWN / 지원 밖 형태 → UNKNOWN"""
    assert s.lc_payment_due("USANCE", None, date(2027, 1, 31), 90) == _ok(date(2027, 5, 1), ACT)
    # 네고일이 함께 있어도 USANCE 기산은 인수일(네고일+90 = 04-10이면 실패) / SIGHT는 인수일을 보지 않는다
    assert s.lc_payment_due("USANCE", date(2027, 1, 10), date(2027, 1, 31), 90) == _ok(
        date(2027, 5, 1), ACT
    )
    assert s.lc_payment_due("SIGHT", date(2027, 2, 10), date(2027, 3, 1), 90) == _ok(
        date(2027, 2, 10), ACT
    )
    assert s.lc_payment_due("USANCE", date(2027, 1, 1), None, 90) == _unknown(
        DueReason.LC_INPUT_MISSING
    )
    assert s.lc_payment_due("USANCE", None, date(2027, 1, 31), None) == _unknown(
        DueReason.LC_INPUT_MISSING
    )
    for tenor in (None, "DEFERRED", "sight", "BL_PLUS"):
        assert s.lc_payment_due(tenor, date(2027, 1, 1), date(2027, 1, 1), 30) == _unknown(
            DueReason.LC_TENOR_UNSUPPORTED
        )
    with pytest.raises(ValueError):
        s.lc_payment_due("USANCE", None, date(2027, 1, 31), 0)


def test_payment_due_branch_guards() -> None:
    """결제조건 없음 = UNKNOWN TERMS_MISSING · 앵커/일수 결측 T/T = TERMS_MISSING · 계약 위반 인자(모르는 유형·앵커·음수 비-ETD·범위 밖) = ValueError"""
    assert s.payment_due(None, AnchorContext(), None) == _unknown(DueReason.TERMS_MISSING)
    assert s.payment_due(Terms(None), AnchorContext(), None) == _unknown(DueReason.TERMS_MISSING)
    assert s.payment_due(Terms("TT_DEFERRED"), AnchorContext(), None) == _unknown(
        DueReason.TERMS_MISSING
    )
    ctx = AnchorContext(bl=DateValue(date(2027, 1, 1), ACT))
    for bad in (
        Terms("WIRE", None, "BL_DATE", 1),
        Terms("TT_DEFERRED", None, "SHIP_DATE", 1),
        Terms("TT_DEFERRED", None, "BL_DATE", -1),  # 음수는 ETD 전용
        Terms("TT_DEFERRED", None, "BL_DATE", 366),
        Terms("TT_DEFERRED", None, "BL_DATE", True),  # 불리언은 정수가 아니다
    ):
        with pytest.raises(ValueError):
            s.payment_due(bad, ctx, None)
    with pytest.raises(ValueError):  # naive 시각 거부
        s.payment_due(
            Terms("TT_DEFERRED", None, "ORDER_DATE", 0),
            AnchorContext(order_at=datetime(2026, 10, 3, 15, 30)),  # noqa: DTZ001
            None,
        )


def test_effective_prefers_actual_then_planned() -> None:
    """유효값 = 실적 우선(ACTUAL) → 계획(PLANNED) → 없음(None)"""
    assert s.effective(date(2026, 1, 1), date(2026, 1, 3)) == DateValue(date(2026, 1, 3), ACT)
    assert s.effective(date(2026, 1, 1), None) == DateValue(date(2026, 1, 1), PLN)
    assert s.effective(None, None) is None


# ── GC-A17 제시기한 MIN·tolerance (B20 행 11~13·27·28) — WBS 검증 K ──────────


@pytest.mark.golden
@pytest.mark.group_k
def test_gc_a17_presentation_deadline_takes_the_earlier_of_bl_plus_21_and_expiry() -> None:
    """GC-A17 / B20-11·12 — B/L 03-01: 유효 03-31 → 03-22(B/L+21) / 유효 03-15 → 03-15(유효기일) / 유효 03-22 → 03-22(같은 날 경계)"""
    bl = DateValue(date(2027, 3, 1), ACT)
    assert s.presentation_deadline(bl, date(2027, 3, 31)) == _ok(date(2027, 3, 22), ACT)
    assert s.presentation_deadline(bl, date(2027, 3, 15)) == _ok(date(2027, 3, 15), ACT)
    assert s.presentation_deadline(bl, date(2027, 3, 22)) == _ok(date(2027, 3, 22), ACT)
    # 하루 차 양쪽 — +21 경계가 +20·+22로 밀리면 실패한다
    assert s.presentation_deadline(bl, date(2027, 3, 23)).value == date(2027, 3, 22)
    assert s.presentation_deadline(bl, date(2027, 3, 21)).value == date(2027, 3, 21)
    # basis는 B/L의 basis를 승계(예정 B/L이면 예정 기준)
    planned = s.presentation_deadline(DateValue(date(2027, 3, 1), PLN), date(2027, 3, 31))
    assert planned == _ok(date(2027, 3, 22), PLN)


@pytest.mark.golden
@pytest.mark.group_k
def test_gc_a17_presentation_missing_input_is_unknown_and_days_is_an_argument() -> None:
    """GC-A17 / B20-13 — 유효기일 또는 B/L 결측 → UNKNOWN(B/L+21 대체 금지) / 제시일수 15 → B/L+15 03-16과 유효기일의 MIN"""
    bl = DateValue(date(2027, 3, 1), ACT)
    assert s.presentation_deadline(bl, None) == _unknown(DueReason.EXPIRY_MISSING)
    assert s.presentation_deadline(None, date(2027, 3, 31)) == _unknown(DueReason.BL_MISSING)
    assert s.presentation_deadline(bl, date(2027, 3, 31), 15) == _ok(date(2027, 3, 16), ACT)
    assert s.presentation_deadline(bl, date(2027, 3, 10), 15) == _ok(date(2027, 3, 10), ACT)
    for bad in (0, 366, -21, True):
        with pytest.raises(ValueError):
            s.presentation_deadline(bl, date(2027, 3, 31), bad)


@pytest.mark.golden
@pytest.mark.group_k
def test_gc_a17_tolerance_rounds_toward_the_narrower_band() -> None:
    """GC-A17 / B20-27 — 1,000,001 ±5% → (950,001, 1,050,001): 하한 올림·상한 내림(좁은 쪽)"""
    assert s.tolerance_bounds(1_000_001, 500, 500) == (950_001, 1_050_001)
    # 정수 산술 검산: 950,000.95 → 올림 950,001 / 1,050,001.05 → 내림 1,050,001
    assert s.within_tolerance(950_001, 1_000_001, 500, 500)
    assert not s.within_tolerance(950_000, 1_000_001, 500, 500)
    assert s.within_tolerance(1_050_001, 1_000_001, 500, 500)
    assert not s.within_tolerance(1_050_002, 1_000_001, 500, 500)


@pytest.mark.golden
@pytest.mark.group_k
def test_gc_a17_tolerance_boundaries_are_inclusive() -> None:
    """GC-A17 / B20-28 — 1,000,000 +10%·−0% → (1,000,000, 1,100,000), x = 1,100,000 통과·1,100,001 거부·999,999 거부"""
    assert s.tolerance_bounds(1_000_000, 1000, 0) == (1_000_000, 1_100_000)
    assert s.within_tolerance(1_100_000, 1_000_000, 1000, 0)
    assert s.within_tolerance(1_000_000, 1_000_000, 1000, 0)
    assert not s.within_tolerance(1_100_001, 1_000_000, 1000, 0)
    assert not s.within_tolerance(999_999, 1_000_000, 1000, 0)


@pytest.mark.group_k
def test_tolerance_argument_contract() -> None:
    """bp는 0~10000 정수, 금액은 0 이상 정수(불리언·음수·범위 밖 = ValueError) · 2^53 넘는 금액도 정수로 정확"""
    assert s.tolerance_bounds(0, 500, 500) == (0, 0)
    assert s.tolerance_bounds(10, 0, 10000) == (0, 10)
    big = 2**60 + 7
    low, high = s.tolerance_bounds(big, 1, 1)
    assert low == -((-big * 9999) // 10000) and high == (big * 10001) // 10000
    for args in ((-1, 0, 0), (1, -1, 0), (1, 0, 10001), (1, True, 0), (1.5, 0, 0)):
        with pytest.raises(ValueError):
            s.tolerance_bounds(*args)


# ── GC-A18 적재의무 = 수리일+30 (B20 행 14·33·34 — 함수 층) ────────────────────


@pytest.mark.golden
def test_gc_a18_loading_deadline_is_clearance_plus_30_calendar_days() -> None:
    """GC-A18 / B20-14 — 수리 2027-01-31 → 2027-03-02 / 2028-01-31 → 2028-03-01(윤년 달력일) / 수리 실적 없음 → UNKNOWN NOT_CLEARED"""
    assert s.loading_deadline(date(2027, 1, 31)) == _ok(date(2027, 3, 2), ACT)
    assert s.loading_deadline(date(2028, 1, 31)) == _ok(date(2028, 3, 1), ACT)
    assert s.loading_deadline(None) == _unknown(DueReason.NOT_CLEARED)
    assert s.LOADING_OBLIGATION_DAYS == 30


@pytest.mark.golden
def test_gc_a18_clearance_is_min_of_accepted_records_and_partial_is_visible() -> None:
    """GC-A18 / B20-33(R-06) — 통관 2건(수리 2027-01-31·미수리) → 유효 수리일 01-31(MIN 유지)·PARTIAL(미수리 1)

    분할 신고의 가장 이른 수리일을 쓴다(MAX면 적재기한이 늦어진다 — fail-closed)."""
    partial = s.customs_clearance([date(2027, 1, 31), None])
    assert partial == s.ClearanceSummary(CustomsState.PARTIAL, date(2027, 1, 31), 1)
    assert s.loading_deadline(partial.cleared_on) == _ok(date(2027, 3, 2), ACT)
    two = s.customs_clearance([date(2027, 2, 10), date(2027, 1, 31)])
    assert two == s.ClearanceSummary(CustomsState.CLEARED, date(2027, 1, 31), 0)
    assert s.customs_clearance([]) == s.ClearanceSummary(CustomsState.NONE, None, 0)
    pending_only = s.customs_clearance([None, None])
    assert pending_only == s.ClearanceSummary(CustomsState.PARTIAL, None, 2)
    assert s.loading_deadline(pending_only.cleared_on) == _unknown(DueReason.NOT_CLEARED)


@pytest.mark.golden
def test_gc_a18_fulfilment_date_is_the_max_of_etd_and_bl_actuals() -> None:
    """GC-A18 / B20-34(R-10) — 기한 03-02: ETD 03-01·B/L 03-03 → MET_LATE(MAX 03-03) / ETD만 03-01 → MET / B/L만 03-02 → MET(경계 포함)"""
    deadline = s.loading_deadline(date(2027, 1, 31))
    today = date(2027, 3, 10)
    assert s.loading_fulfilment(deadline, date(2027, 3, 1), date(2027, 3, 3), today) is (
        LoadingState.MET_LATE
    )
    assert s.loading_fulfilment(deadline, date(2027, 3, 1), None, today) is LoadingState.MET
    assert s.loading_fulfilment(deadline, None, date(2027, 3, 2), today) is LoadingState.MET
    assert s.loading_fulfilment(deadline, None, date(2027, 3, 3), today) is LoadingState.MET_LATE


def test_loading_fulfilment_open_overdue_and_unknown() -> None:
    """이행일 없음: 오늘 ≤ 기한 → OPEN(경계 포함) / 오늘 > 기한 → OVERDUE · 기한 UNKNOWN(수리 전) → UNKNOWN"""
    deadline = s.loading_deadline(date(2027, 1, 31))  # 03-02
    assert s.loading_fulfilment(deadline, None, None, date(2027, 3, 2)) is LoadingState.OPEN
    assert s.loading_fulfilment(deadline, None, None, date(2027, 3, 3)) is LoadingState.OVERDUE
    unknown = s.loading_deadline(None)
    assert s.loading_fulfilment(unknown, date(2027, 3, 1), None, date(2027, 3, 3)) is (
        LoadingState.UNKNOWN
    )


# ── 시각형 기준일 (B20 행 15) ──────────────────────────────────────────────────


def test_cutoff_scan_date_takes_the_earlier_of_local_and_kst() -> None:
    """B20-15(GC 비배정 경계 행 — PR-4a·6 소비) — 2026-10-11T00:00Z·America/Los_Angeles → 2026-10-10(현지 10-10 < KST 10-11) · 서울은 같은 날 · 모르는 시간대·naive = ValueError"""
    instant = datetime(2026, 10, 11, 0, 0, tzinfo=UTC)
    assert s.cutoff_scan_date(instant, "America/Los_Angeles") == date(2026, 10, 10)
    assert s.cutoff_scan_date(instant, "Asia/Seoul") == date(2026, 10, 11)
    # 동쪽(KST보다 앞선 지역): 현지 10-11 > KST 10-10 → KST 10-10
    assert s.cutoff_scan_date(datetime(2026, 10, 10, 13, 30, tzinfo=UTC), "Pacific/Auckland") == (
        date(2026, 10, 10)
    )
    for bad in ("KST", "America", "", " Asia/Seoul", "../etc/passwd"):
        with pytest.raises(ValueError):
            s.cutoff_scan_date(instant, bad)
    with pytest.raises(ValueError):
        s.cutoff_scan_date(datetime(2026, 10, 11), "Asia/Seoul")  # noqa: DTZ001


# ── GC-A19 ETA 현지 연휴 경고 (B20 행 16~18 — 함수 층) ────────────────────────

CN_2026 = {date(2026, 10, 1): "국경절", date(2026, 10, 2): "국경절 연휴"}


@pytest.mark.golden
def test_gc_a19_eta_on_a_declared_holiday_warns_and_a_weekday_is_clear() -> None:
    """GC-A19 / B20-16 — CN 2026 선언에 10-01 '국경절' → HOLIDAY('국경절') / 같은 선언의 10-09 → CLEAR(자기검사)"""
    assert holiday_flag(date(2026, 10, 1), "CN", {2026}, CN_2026) == (
        HolidayFlag.HOLIDAY,
        "국경절",
    )
    assert holiday_flag(date(2026, 10, 9), "CN", {2026}, CN_2026) == (HolidayFlag.CLEAR, None)


@pytest.mark.golden
def test_gc_a19_undeclared_year_is_unverified_not_clear() -> None:
    """GC-A19 / B20-17 — 도착국 2027 선언 없음, ETA 2027-01-04 → UNVERIFIED(경고 없음 ≠ 평일) · 빈 선언(0건 = '휴일 없음 확인')은 CLEAR"""
    assert holiday_flag(date(2027, 1, 4), "CN", {2026}, CN_2026) == (HolidayFlag.UNVERIFIED, None)
    assert holiday_flag(date(2027, 1, 4), "CN", set(), {}) == (HolidayFlag.UNVERIFIED, None)
    assert holiday_flag(date(2027, 1, 4), "CN", {2027}, {}) == (HolidayFlag.CLEAR, None)
    # 국가 없음(방어 계약 — X-28)도 UNVERIFIED
    assert holiday_flag(date(2026, 10, 1), None, {2026}, CN_2026) == (HolidayFlag.UNVERIFIED, None)
    assert holiday_flag(date(2026, 10, 1), "", {2026}, CN_2026) == (HolidayFlag.UNVERIFIED, None)


@pytest.mark.golden
def test_gc_a19_a_due_date_on_a_holiday_is_not_moved() -> None:
    """GC-A19 / B20-18 — 대금만기가 도착국 휴일과 같은 날이어도 값 불변(자동 순연 0): 판정은 HOLIDAY, 만기 값은 그대로"""
    ctx = AnchorContext(bl=DateValue(date(2026, 9, 1), ACT))
    due = s.payment_due(Terms("TT_DEFERRED", None, "BL_DATE", 30), ctx, None)
    assert due == _ok(date(2026, 10, 1), ACT) and due.value is not None
    assert holiday_flag(due.value, "CN", {2026}, CN_2026) == (HolidayFlag.HOLIDAY, "국경절")
    # 연휴 이틀째도 그 날짜 그대로 판정 — 다음 영업일로 옮겨 판정하지 않는다
    assert holiday_flag(date(2026, 10, 2), "CN", {2026}, CN_2026) == (
        HolidayFlag.HOLIDAY,
        "국경절 연휴",
    )


# ── 검토 반영(PR #55): 비지역 시간대 키·결제조건 형태 ─────────────────────────


@pytest.mark.parametrize("tz", ["localtime", "Factory", "posixrules"])
def test_zone_rejects_host_dependent_keys(tz: str) -> None:
    """'localtime' 등은 호스트 /etc/localtime에 따라 오프셋이 바뀐다 — 순수 함수 계약상 거부"""
    with pytest.raises(ValueError):
        s.zone(tz)
    assert s.zone("Asia/Seoul").key == "Asia/Seoul"


@pytest.mark.parametrize(
    "terms",
    [
        Terms("TT_ADVANCE", None, "ETD_DATE", 5),
        Terms("TT_ADVANCE", 0, "ETD_DATE", 5),
        Terms("TT_ADVANCE", 10001, "ETD_DATE", 5),
        Terms("TT_DEFERRED", 3000, "BL_DATE", 30),
        Terms("LC", 3000, None, None),
    ],
)
def test_payment_due_rejects_terms_that_violate_the_db_shape(terms: Terms) -> None:
    """DB CHECK payment_terms_shape와 같은 형태 검사 — 모순된 조건으로 만기를 만들지 않는다"""
    ctx = AnchorContext(
        etd=s.effective(None, date(2026, 11, 5)), bl=s.effective(None, date(2026, 11, 5))
    )
    with pytest.raises(ValueError):
        s.payment_due(terms, ctx, None)


@pytest.mark.group_k
def test_calendar_overflow_is_unknown_not_an_exception() -> None:
    """적대 검토 반영 ⑤(보조 방어선) — 달력 끝 근처 산술은 OverflowError(응답 500) 대신 UNKNOWN `DATE_OUT_OF_RANGE`.
    입력·DB는 2000~2999로 막으므로 이 경로는 우회 데이터에서만 열린다"""
    edge = date(9999, 12, 20)
    unknown = _unknown(DueReason.DATE_OUT_OF_RANGE)
    assert s.loading_deadline(edge) == unknown
    ctx = AnchorContext(bl=s.effective(None, edge))
    assert s.payment_due(Terms("TT_DEFERRED", None, "BL_DATE", 30), ctx, None) == unknown
    assert s.presentation_deadline(DateValue(edge, ACT), date(9999, 12, 31)) == unknown
    assert s.lc_payment_due("USANCE", None, edge, 90) == unknown
    early = AnchorContext(etd=s.effective(None, date(1, 1, 3)))
    assert s.payment_due(Terms("TT_ADVANCE", 3000, "ETD_DATE", -7), early, None) == unknown
    assert s.loading_fulfilment(unknown, None, None, date(2026, 10, 1)) is LoadingState.UNKNOWN


# ── PO 라인 입고예정 (S3-2 PR-5a / design-B B17 / ADR-0085 — 계산값, 열 없음) ─────────────────────────


def test_expected_receipt_is_the_latest_eta_of_the_live_import_shipments() -> None:
    """B17 — 대표값 = 살아 있는 수입선적 ETA(유효값) 중 **가장 늦은 날**(전량 도착이 입고 완결 — 보수값). 입력 순서와 무관하다.
    basis = 전 선적이 ETA 실적이면 ACTUAL, 하나라도 계획이면 PLANNED(하나만 도착한 라인을 '도착 완료'로 보이지 않는다)"""
    early = DateValue(date(2026, 11, 2), ACT)
    late = DateValue(date(2026, 11, 20), PLN)
    for order in ([early, late], [late, early]):
        got = s.expected_receipt(order)
        assert got == s.ReceiptEstimate(s.ReceiptStatus.SCHEDULED, date(2026, 11, 20), PLN, 2, 0)
    # 늦은 쪽이 실적이어도 다른 선적이 계획이면 PLANNED
    mixed = s.expected_receipt(
        [DateValue(date(2026, 11, 2), PLN), DateValue(date(2026, 12, 1), ACT)]
    )
    assert (mixed.value, mixed.basis) == (date(2026, 12, 1), PLN)
    arrived = s.expected_receipt(
        [DateValue(date(2026, 11, 2), ACT), DateValue(date(2026, 11, 3), ACT)]
    )
    assert (arrived.status, arrived.value, arrived.basis) == (
        s.ReceiptStatus.SCHEDULED,
        date(2026, 11, 3),
        ACT,
    )


def test_expected_receipt_without_an_eta_on_any_shipment_is_unscheduled_not_the_known_max() -> None:
    """fail-visible(자율 확정) — ETA가 없는 선적이 하나라도 있으면 UNSCHEDULED·값 없음(아는 날짜 중 가장 늦은 값으로 대신 채우지 않는다),
    선적이 없으면 NONE('입고예정 미정')"""
    partly = s.expected_receipt([DateValue(date(2026, 11, 2), PLN), None])
    assert partly == s.ReceiptEstimate(s.ReceiptStatus.UNSCHEDULED, None, None, 2, 1)
    assert s.expected_receipt([None]) == s.ReceiptEstimate(
        s.ReceiptStatus.UNSCHEDULED, None, None, 1, 1
    )
    assert s.expected_receipt([]) == s.ReceiptEstimate(s.ReceiptStatus.NONE, None, None, 0, 0)
