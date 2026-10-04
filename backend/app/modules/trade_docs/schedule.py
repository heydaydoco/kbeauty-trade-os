"""기일 산식 — 순수 함수 (S3-2 PR-2a / ADR-0080·0081 / design-B §B4~B7·B10 / design-integrated §9 R-06·R-10·R-11·R-29).

DESIGN §7.5 "자동 계산: 대금만기=기산점+일수(결제유형 분기: T/T=약정 기산점 / L/C=네고·인수 기준), 적재의무=수리일+30일,
L/C 제시기한=MIN(B/L+21, 유효기일)"과 §20 검증 K "L/C 제시기한 MIN·tolerance 상하한"의 **유일한 정의**다.
화면(선적 상세 조립 — PR-4a)과 기일 스캔(PR-6)이 같은 함수를 부른다(정의 이원화 금지 — DESIGN §5.4 ③ 계보).

■ **순수**: DB·세션·시계에 의존하지 않는다. "오늘"은 인자로 받는다(`today_kst()`를 부르지 않는다 — 아키텍처 테스트가 임포트를 고정).
  예외는 잘못된 인자(계약 위반)의 `ValueError`뿐이다 — 업무상 "모름"은 예외가 아니라 `DueResult(status=UNKNOWN, reason=…)`다.
■ **대체 금지(fail-visible — GC-A13 계보)**: 앵커 미확정·인보이스일(S3-3 이전 원천 없음)·L/C 조건 미등록(운영 경로 — `lc_terms`는 S3-3)·
  수리 전은 전부 UNKNOWN + 사유 코드다. ETD·오늘·0일로 대신 채우지 않는다.
■ **파생값은 저장하지 않는다**(ADR-0080) — 이 모듈의 결과는 읽기 시점 계산값이다. 휴일로 날짜를 옮기지 않는다(ADR-0082 — 경고만).
■ 날짜는 저장된 현지 달력일(DATE)끼리의 달력일 산술이다(시간대 개입 없음). 시각이 들어오는 곳은 ORDER_DATE(확정·발행 시각 → KST 날짜)와
  시각형 마일스톤의 기준일(`cutoff_scan_date`)뿐이다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from zoneinfo import ZoneInfo

from app.core.time import KST
from app.core.tzdb import bundled_zone
from app.modules.trade_docs.constants import BalanceAnchor, PaymentType

#: 적재의무 기한 = 수리일 + 30 달력일(DESIGN §7.5).
LOADING_OBLIGATION_DAYS = 30
#: L/C 서류 제시기간 기본값(B/L 후 21일 — DESIGN §7.5). 실제 값은 S3-3 `lc_terms`가 공급한다(인자로 받는다).
DEFAULT_PRESENTATION_DAYS = 21
PRESENTATION_DAYS_RANGE = (1, 365)
USANCE_DAYS_RANGE = (1, 365)
#: tolerance(과부족 허용) bp 범위 — 0%~100%.
TOLERANCE_BP_RANGE = (0, 10_000)
_BP_DENOMINATOR = 10_000
#: TT_ADVANCE 100%(= 10000bp)는 잔금이 없다.
FULL_ADVANCE_BP = 10_000


class Basis(StrEnum):
    """결과 날짜의 출처 — 실적이면 ACTUAL, 계획(예정)이면 PLANNED(화면: '예정 기준 — 실적 입력 시 재계산')."""

    ACTUAL = "ACTUAL"
    PLANNED = "PLANNED"


class DueStatus(StrEnum):
    OK = "OK"
    #: 평가 불능 — 통과가 아니다(사유 코드 동반, 대체값 없음).
    UNKNOWN = "UNKNOWN"
    #: 해당 없음(예: 100% 선수금 → 잔금 없음). UNKNOWN과 다르다.
    NOT_APPLICABLE = "NOT_APPLICABLE"


class DueReason(StrEnum):
    """UNKNOWN·NOT_APPLICABLE의 사유 코드(와이어 값 — 화면이 한글 문구로 바꾼다)."""

    TERMS_MISSING = "TERMS_MISSING"  # 결제조건 4열 결측(동결 전 초안 방어)
    ANCHOR_PENDING = "ANCHOR_PENDING"  # 앵커 날짜(확정일·ETD·B/L·ETA)가 아직 없다
    INVOICE_NOT_ISSUED = (
        "INVOICE_NOT_ISSUED"  # 인보이스일 — S3-3 CI 이전에는 원천 없음(ETD 대체 금지)
    )
    RECEIPT_NOT_RECORDED = "RECEIPT_NOT_RECORDED"  # 입고 확정일 — S4-1 이전에는 원천 없음
    LC_TERMS_NOT_REGISTERED = "LC_TERMS_NOT_REGISTERED"  # 운영 L/C — `lc_terms`(S3-3) 미공급
    LC_TENOR_UNSUPPORTED = "LC_TENOR_UNSUPPORTED"  # SIGHT·USANCE 밖의 지급 형태
    LC_INPUT_MISSING = "LC_INPUT_MISSING"  # 네고일·인수일·usance 일수 결측
    BL_MISSING = "BL_MISSING"  # 제시기한 — B/L일 없음(B/L+21로 대체 금지 대상)
    EXPIRY_MISSING = "EXPIRY_MISSING"  # 제시기한 — L/C 유효기일 없음(B/L+21로 대체 금지)
    NOT_CLEARED = "NOT_CLEARED"  # 적재기한 — 수리 실적 없음(계획 수리일 미사용)
    NO_BALANCE = "NO_BALANCE"  # 100% 선수금 — 잔금 없음(NOT_APPLICABLE)
    # 날짜 산술이 달력 범위를 넘는다(보조 방어선 — 입력·DB는 2000~2999로 막는다, PR-4a 적대 검토 반영 ⑤)
    DATE_OUT_OF_RANGE = "DATE_OUT_OF_RANGE"
    # 저장된 시간대를 앱 버전 고정 tzdata가 모른다 — 현지 날짜·D-N·도과를 KST로 추정하지 않는다(PR-4a 적대 검토 반영 ①)
    TZ_UNRESOLVED = "TZ_UNRESOLVED"


class LcTenor(StrEnum):
    """L/C 지급 형태 — DESIGN §7.5 "네고·인수 기준" 문면의 둘만 연다(B/L 기준 usance 등은 S3-3 `lc_terms` 설계 시 가산)."""

    SIGHT = "SIGHT"  # 만기 = 네고일
    USANCE = "USANCE"  # 만기 = 인수일 + usance 일수


class LoadingState(StrEnum):
    """적재의무 이행 판정(계산값 — 저장하지 않음)."""

    MET = "MET"  # 이행일 ≤ 기한
    MET_LATE = "MET_LATE"  # 이행일 > 기한
    OPEN = "OPEN"  # 미이행·기한 전(오늘 ≤ 기한)
    OVERDUE = "OVERDUE"  # 미이행·오늘 > 기한
    UNKNOWN = "UNKNOWN"  # 기한 자체가 UNKNOWN(수리 전)


class CustomsState(StrEnum):
    """신고수리 행 상태(R-06) — 통관 기록 없음 / 전건 수리 / 미수리 1건 이상."""

    NONE = "NONE"
    CLEARED = "CLEARED"
    PARTIAL = "PARTIAL"


@dataclass(frozen=True, slots=True)
class DateValue:
    """유효값(실적 우선, 없으면 계획)과 그 출처."""

    value: date
    basis: Basis


@dataclass(frozen=True, slots=True)
class DueResult:
    status: DueStatus
    value: date | None
    basis: Basis | None
    reason: DueReason | None

    @classmethod
    def ok(cls, value: date, basis: Basis) -> DueResult:
        return cls(DueStatus.OK, value, basis, None)

    @classmethod
    def unknown(cls, reason: DueReason) -> DueResult:
        return cls(DueStatus.UNKNOWN, None, None, reason)

    @classmethod
    def not_applicable(cls, reason: DueReason) -> DueResult:
        return cls(DueStatus.NOT_APPLICABLE, None, None, reason)


class TermsLike(Protocol):
    """결제조건 4열(선적 헤더 사본 — X-01). `trade_docs.payment_terms.PaymentTerms`가 이 모양이다(이 모듈은 그것을 임포트하지 않는다 — 순수성)."""

    @property
    def payment_type(self) -> str | None: ...
    @property
    def advance_pct_bp(self) -> int | None: ...
    @property
    def balance_anchor(self) -> str | None: ...
    @property
    def balance_days(self) -> int | None: ...


@dataclass(frozen=True, slots=True)
class AnchorContext:
    """앵커 원천(design-B §B4 표). 조립자(PR-4a)가 채운다.

    `order_at`: 수출 = SO `confirmed_at`, 수입 = **PO `frozen_at`**(R-29 — 사람 입력 `doc_date`는 쓰지 않는다). tz-aware 시각.
    `etd`·`bl`·`eta`: 마일스톤 ETD·BL_ISSUED·ETA의 유효값(`effective()` 결과 — 실적 우선).
    인보이스일·입고 확정일은 원천이 아직 없어 필드가 없다(항상 UNKNOWN — 대체 금지).
    """

    order_at: datetime | None = None
    etd: DateValue | None = None
    bl: DateValue | None = None
    eta: DateValue | None = None


@dataclass(frozen=True, slots=True)
class LcInputs:
    """L/C 만기 입력 — S3-3 `lc_terms`가 공급한다(S3-2 운영 경로에서는 항상 None → UNKNOWN `LC_TERMS_NOT_REGISTERED`)."""

    tenor: str | None
    negotiated_on: date | None = None
    accepted_on: date | None = None
    usance_days: int | None = None


@dataclass(frozen=True, slots=True)
class ClearanceSummary:
    """통관 기록들의 수리일 요약(R-06·X-02) — 유효 수리일 = 수리된 기록의 MIN(가장 이른 수리일 → 적재기한도 가장 이르게)."""

    state: CustomsState
    cleared_on: date | None
    pending_count: int


def _require_int(name: str, value: object, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f"{name}는 {low}~{high} 사이 정수여야 합니다: {value!r}")
    return value


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("시간대 정보가 없는 시각(naive datetime)은 받지 않습니다.")
    return value


def _shifted(value: DateValue, days: int) -> DueResult:
    """달력일 산술 → OK, 달력 범위(0001~9999)를 넘으면 UNKNOWN `DATE_OUT_OF_RANGE`(OverflowError로 응답 500을 내지 않는다 —
    입력·DB는 2000~2999로 막으므로 보조 방어선, PR-4a 적대 검토 반영 ⑤)."""
    try:
        return DueResult.ok(value.value + timedelta(days=days), value.basis)
    except OverflowError:
        return DueResult.unknown(DueReason.DATE_OUT_OF_RANGE)


# ── 유효값·시각형 기준일 ──────────────────────────────────────────────────────


def effective(planned: date | None, actual: date | None) -> DateValue | None:
    """유효값 = 실적이 있으면 실적(ACTUAL), 없으면 계획(PLANNED), 둘 다 없으면 None."""
    if actual is not None:
        return DateValue(actual, Basis.ACTUAL)
    if planned is not None:
        return DateValue(planned, Basis.PLANNED)
    return None


def zone(tz: str) -> ZoneInfo:
    """IANA 시간대 — **앱 버전에 고정된 tzdata**(`app.core.tzdb`)에서만 연다. 모르는 이름은 ValueError
    (쓰기는 422 `TIMEZONE_INVALID`로 번역, 읽기 조립은 UNKNOWN `TZ_UNRESOLVED`로 표시 — PR-4a 적대 검토 반영).

    호스트 OS 시간대 데이터로 대체하지 않는다(이미지마다 이름 집합이 달라 같은 행이 어디서는 해석되고 어디서는 500이 되던 구멍).
    'localtime'·'Factory'·'posixrules' 같은 비지역 키는 받지 않는다(`tzdb.zone_names`가 뺀다).
    """
    if not isinstance(tz, str) or not tz or tz != tz.strip():
        raise ValueError(f"IANA 시간대 이름이 아닙니다: {tz!r}")
    try:
        return bundled_zone(tz)
    except (KeyError, ValueError, OSError):
        raise ValueError(f"IANA 시간대 이름이 아닙니다: {tz!r}") from None


def cutoff_scan_date(instant_utc: datetime, tz: str) -> date:
    """시각형 마일스톤(서류마감·Cargo Closing)의 D-N 기준일 = min(현지 날짜, KST 날짜) — 이른 쪽으로 더 일찍 경고한다(B3 ④).

    도과(`is_overdue`) 판정에는 쓰지 않는다 — 시각형 도과는 UTC 시각 비교다(R-20, PR-6).
    """
    moment = _require_aware(instant_utc)
    return min(moment.astimezone(zone(tz)).date(), moment.astimezone(KST).date())


def kst_date(instant: datetime) -> date:
    """시각의 KST 날짜(ORDER_DATE = 확정·발행 시각의 KST 날짜 — A:248·R-29)."""
    return _require_aware(instant).astimezone(KST).date()


# ── 대금만기 (B4·B5) ─────────────────────────────────────────────────────────


def resolve_anchor(anchor: str, ctx: AnchorContext) -> DateValue | DueResult:
    """잔금 기산점 → 날짜(design-B §B4 표 — 재정의 금지, 매핑만). 없으면 UNKNOWN 사유(대체 금지)."""
    try:
        kind = BalanceAnchor(anchor)
    except ValueError:
        raise ValueError(f"알 수 없는 잔금 기산점: {anchor!r}") from None
    if kind is BalanceAnchor.ORDER_DATE:
        if ctx.order_at is None:
            return DueResult.unknown(DueReason.ANCHOR_PENDING)
        return DateValue(kst_date(ctx.order_at), Basis.ACTUAL)
    if kind is BalanceAnchor.INVOICE_DATE:
        return DueResult.unknown(DueReason.INVOICE_NOT_ISSUED)  # ETD로 대체 금지(GC-A13)
    if kind is BalanceAnchor.RECEIPT_DATE:
        return DueResult.unknown(DueReason.RECEIPT_NOT_RECORDED)
    found = {
        BalanceAnchor.ETD_DATE: ctx.etd,
        BalanceAnchor.BL_DATE: ctx.bl,
        BalanceAnchor.ARRIVAL_DATE: ctx.eta,
    }[kind]
    return found if found is not None else DueResult.unknown(DueReason.ANCHOR_PENDING)


def lc_payment_due(
    tenor: str | None,
    negotiated_on: date | None,
    accepted_on: date | None,
    usance_days: int | None,
) -> DueResult:
    """L/C 대금만기(B5) — SIGHT = 네고일 / USANCE = 인수일 + usance 일수. 그 밖의 형태·결측은 UNKNOWN."""
    if tenor == LcTenor.SIGHT.value:
        if negotiated_on is None:
            return DueResult.unknown(DueReason.LC_INPUT_MISSING)
        return DueResult.ok(negotiated_on, Basis.ACTUAL)
    if tenor == LcTenor.USANCE.value:
        if accepted_on is None or usance_days is None:
            return DueResult.unknown(DueReason.LC_INPUT_MISSING)
        days = _require_int("usance_days", usance_days, *USANCE_DAYS_RANGE)
        return _shifted(DateValue(accepted_on, Basis.ACTUAL), days)
    return DueResult.unknown(DueReason.LC_TENOR_UNSUPPORTED)


def payment_due(terms: TermsLike | None, ctx: AnchorContext, lc: LcInputs | None) -> DueResult:
    """대금만기 — 결제유형 분기(DESIGN §7.5): T/T = 약정 기산점 + 일수 / L/C = 네고·인수 기준(`lc_payment_due`).

    - 결제조건 없음 → UNKNOWN `TERMS_MISSING`
    - TT_ADVANCE 100% → NOT_APPLICABLE `NO_BALANCE`(잔금 없음 ≠ UNKNOWN)
    - TT_ADVANCE <100%·TT_DEFERRED → 앵커일 + `balance_days`(달력일, 음수는 ETD 앵커 전용 — DB CHECK 승계, 위반 인자는 ValueError)
    - LC → `lc`가 None이면 UNKNOWN `LC_TERMS_NOT_REGISTERED`(S3-2 운영 경로 — ADR-0081), 있으면 `lc_payment_due`
    휴일로 날짜를 옮기지 않는다(ADR-0082).
    """
    if terms is None or terms.payment_type is None:
        return DueResult.unknown(DueReason.TERMS_MISSING)
    try:
        ptype = PaymentType(terms.payment_type)
    except ValueError:
        raise ValueError(f"알 수 없는 결제유형: {terms.payment_type!r}") from None
    _require_terms_shape(ptype, terms.advance_pct_bp)
    if ptype is PaymentType.LC:
        if lc is None:
            return DueResult.unknown(DueReason.LC_TERMS_NOT_REGISTERED)
        return lc_payment_due(lc.tenor, lc.negotiated_on, lc.accepted_on, lc.usance_days)
    if ptype is PaymentType.TT_ADVANCE and terms.advance_pct_bp == FULL_ADVANCE_BP:
        return DueResult.not_applicable(DueReason.NO_BALANCE)
    anchor, days = terms.balance_anchor, terms.balance_days
    if anchor is None or days is None:
        return DueResult.unknown(DueReason.TERMS_MISSING)
    days = _require_int("balance_days", days, -90, 365)
    if days < 0 and anchor != BalanceAnchor.ETD_DATE.value:
        raise ValueError("음수 잔금 일수는 ETD 기산점에서만 쓸 수 있습니다.")
    resolved = resolve_anchor(anchor, ctx)
    if isinstance(resolved, DueResult):
        return resolved
    return _shifted(resolved, days)


def _require_terms_shape(ptype: PaymentType, advance_pct_bp: int | None) -> None:
    """DB CHECK `payment_terms_shape`와 같은 형태 검사 — 모순된 조건으로 만기를 만들어 내지 않는다(위반 = ValueError)."""
    if ptype is PaymentType.TT_ADVANCE:
        if advance_pct_bp is None or not (1 <= advance_pct_bp <= FULL_ADVANCE_BP):
            raise ValueError("선수금 T/T는 선수율(1~10000bp)이 있어야 합니다.")
    elif advance_pct_bp is not None:
        raise ValueError("선수금 T/T가 아닌 결제조건에는 선수율을 둘 수 없습니다.")


# ── L/C 제시기한·tolerance (B6 — 수출·수입 L/C 공통, R-11) ─────────────────────


def presentation_deadline(
    bl: DateValue | None,
    expiry_on: date | None,
    presentation_days: int = DEFAULT_PRESENTATION_DAYS,
) -> DueResult:
    """L/C 서류 제시기한 = MIN(B/L일 + 제시기간, 유효기일). 어느 쪽이든 결측이면 UNKNOWN(B/L+21로 대체 금지).

    휴일 연장(UCP 600 제29조 a항)은 DESIGN 문면에 없어 하지 않는다 — 연장하지 않는 쪽이 이르고 안전하다. basis는 B/L의 basis를 승계한다.
    """
    days = _require_int("presentation_days", presentation_days, *PRESENTATION_DAYS_RANGE)
    if bl is None:
        return DueResult.unknown(DueReason.BL_MISSING)
    if expiry_on is None:
        return DueResult.unknown(DueReason.EXPIRY_MISSING)
    shifted = _shifted(bl, days)
    if shifted.value is None:
        return shifted
    return DueResult.ok(min(shifted.value, expiry_on), bl.basis)


def tolerance_bounds(amount_minor: int, plus_bp: int, minus_bp: int) -> tuple[int, int]:
    """과부족 허용 범위(하한, 상한) — 정수 최소단위, **허용폭을 좁히는 쪽**으로 반올림(하한 올림·상한 내림). 경계값은 통과.

    파이썬 정수로만 계산한다(SQL 곱 금지 — BIGINT 넘침, A:251 선례).
    """
    amount = _require_int("amount_minor", amount_minor, 0, 2**63 - 1)
    plus = _require_int("plus_bp", plus_bp, *TOLERANCE_BP_RANGE)
    minus = _require_int("minus_bp", minus_bp, *TOLERANCE_BP_RANGE)
    low = -((-amount * (_BP_DENOMINATOR - minus)) // _BP_DENOMINATOR)  # 올림
    high = (amount * (_BP_DENOMINATOR + plus)) // _BP_DENOMINATOR  # 내림
    return low, high


def within_tolerance(value_minor: int, amount_minor: int, plus_bp: int, minus_bp: int) -> bool:
    """`low ≤ value ≤ high`(경계 포함)."""
    low, high = tolerance_bounds(amount_minor, plus_bp, minus_bp)
    value = _require_int("value_minor", value_minor, 0, 2**63 - 1)
    return low <= value <= high


# ── 적재의무 (B7 — 수출만, 수리일 = 통관 기록 MIN, 이행일 = ETD·B/L 실적 MAX) ──────


def customs_clearance(accepted_on_values: Iterable[date | None]) -> ClearanceSummary:
    """살아 있는 통관 기록들의 `accepted_on`(미수리 = None) → 유효 수리일·상태(X-02·R-06).

    유효 수리일 = 수리된 기록의 MIN(분할 신고 중 가장 이른 수리일 — 적재기한도 가장 이르게, fail-closed).
    미수리 기록이 1건이라도 있으면 PARTIAL(산식 값은 MIN 유지 — '일부 미수리 n건' 배지), 기록 없음 = NONE.
    살아 있는 기록만 넘기는 것은 호출자(조립자 PR-4a)의 몫이다.
    """
    values = list(accepted_on_values)
    cleared = [v for v in values if v is not None]
    pending = len(values) - len(cleared)
    if not values:
        state = CustomsState.NONE
    elif pending:
        state = CustomsState.PARTIAL
    else:
        state = CustomsState.CLEARED
    return ClearanceSummary(state, min(cleared) if cleared else None, pending)


def loading_deadline(cleared_actual_on: date | None) -> DueResult:
    """적재의무 기한 = 수리일 + 30 달력일. 수리 실적이 없으면 UNKNOWN `NOT_CLEARED`(계획 수리일로 계산하지 않는다)."""
    if cleared_actual_on is None:
        return DueResult.unknown(DueReason.NOT_CLEARED)
    return _shifted(DateValue(cleared_actual_on, Basis.ACTUAL), LOADING_OBLIGATION_DAYS)


def loading_fulfilment(
    deadline: DueResult,
    etd_actual_on: date | None,
    bl_actual_on: date | None,
    today: date,
) -> LoadingState:
    """적재의무 이행 판정 — 이행일 = ETD·BL_ISSUED **실적** 중 존재하는 값의 MAX(R-10 가정, ADR-0080).

    둘 다 있으면 늦은 쪽(MET_LATE를 놓치지 않는다). 이행일 없음: 오늘(KST — 인자) > 기한이면 OVERDUE, 아니면 OPEN.
    """
    if deadline.status is not DueStatus.OK or deadline.value is None:
        return LoadingState.UNKNOWN
    done = sorted(d for d in (etd_actual_on, bl_actual_on) if d is not None)
    if done:
        latest = done[-1]  # 존재값의 MAX(전표 모듈은 내장 max 호출 0 — 채번 MAX+1 금지 스캔)
        return LoadingState.MET if latest <= deadline.value else LoadingState.MET_LATE
    return LoadingState.OVERDUE if today > deadline.value else LoadingState.OPEN


# ── PO 라인 입고예정 (B17 — 계산값, 열 없음 · S3-2 PR-5a / ADR-0085 · P-05) ─────────────────────


class ReceiptStatus(StrEnum):
    """PO 라인 입고예정 판정 — 살아 있는 수입선적 0건 / ETA 없는 선적 1건↑(판정 불가 — 날짜 없음) / 전 선적 ETA 있음."""

    NONE = "NONE"
    UNSCHEDULED = "UNSCHEDULED"
    SCHEDULED = "SCHEDULED"


@dataclass(frozen=True, slots=True)
class ReceiptEstimate:
    """입고예정 계산값 — `value`는 SCHEDULED일 때만(가장 늦은 ETA), `basis`는 **전 선적이 ETA 실적이면 ACTUAL**(하나라도 계획이면 PLANNED)."""

    status: ReceiptStatus
    value: date | None
    basis: Basis | None
    shipment_count: int
    unscheduled_count: int


def expected_receipt(etas: Iterable[DateValue | None]) -> ReceiptEstimate:
    """그 PO 라인을 참조하는 **살아 있는 수입선적들의 ETA 유효값**(선적당 1개 — 실적 우선 `effective`, 없으면 None) → 입고예정.

    대표값 = **가장 늦은 ETA**(전량이 도착해야 입고가 완결 — design-B B17 보수값). ETA가 없는 선적이 하나라도 있으면 가장 늦은 날을 알 수
    없으므로 **UNSCHEDULED·값 없음**이다(아는 날짜 중 가장 늦은 값으로 대신 채우지 않는다 — fail-visible, 자율 확정). 선적이 없으면 NONE
    ('입고예정 미정'). 배정되지 않은 PO 수량(배정 가능량 > 0)은 이 값이 덮지 않는다 — 화면은 배정 가능량을 함께 보인다(호출자 몫).
    """
    values = list(etas)
    known = sorted((v for v in values if v is not None), key=lambda v: v.value)
    unscheduled = len(values) - len(known)
    if not values:
        return ReceiptEstimate(ReceiptStatus.NONE, None, None, 0, 0)
    if unscheduled:
        return ReceiptEstimate(ReceiptStatus.UNSCHEDULED, None, None, len(values), unscheduled)
    latest = known[-1]  # 존재값의 MAX(전표 모듈은 내장 max 호출 0 — 채번 MAX+1 금지 스캔)
    all_actual = all(v.basis is Basis.ACTUAL for v in known)
    return ReceiptEstimate(
        ReceiptStatus.SCHEDULED,
        latest.value,
        Basis.ACTUAL if all_actual else Basis.PLANNED,
        len(values),
        0,
    )
