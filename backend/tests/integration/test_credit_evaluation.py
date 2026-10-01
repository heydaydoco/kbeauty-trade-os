"""A. 여신 평가 — 경계·NULL/0·통화·미결 술어·미수 provider·SO 쪼개기·증적 (S3-1 PR-9a / ADR-0064 / design-E E1·E2).

노출 산식의 각 항이 "못 셈"을 "없음"으로 바꾸지 않는지(fail-visible)가 핵심이다: 한도 경계는 strict, 통화 비교 불가는 UNEVALUABLE(통과 아님), 미수 항은 0으로 합산하지 않는다.
테스트 데이터는 서비스를 거치지 않고 SQL로 만든다(라인 없음 — 총액만 지정). 한도는 USD 센트 단위 정수다(100_000 = 1,000.00 USD).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.core.db.uow import unit_of_work
from app.modules.credit import evaluation, providers
from app.modules.credit.evaluation import CreditVerdict, ReasonCode
from app.modules.credit.exposure import CLOSED_STATUSES, open_orders_stmt
from app.modules.credit.providers import ReceivableTerm
from tests.factories.approvals import evaluate, raw_open_so, set_credit_limit
from tests.factories.trade import create_buyer

pytestmark = pytest.mark.group_a


@pytest.fixture(autouse=True)
def _default_provider() -> Iterator[None]:
    providers.reset_receivable_provider_for_tests()
    yield
    providers.reset_receivable_provider_for_tests()


def _buyer(limit: int | None = 100_000, currency: str | None = "USD") -> int:
    buyer = create_buyer()
    set_credit_limit(buyer, limit, currency)
    return buyer


# ── 경계·NULL·0 ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("this_total", "verdict", "excess"),
    [(99_999, "WITHIN_LIMIT", 0), (100_000, "WITHIN_LIMIT", 0), (100_001, "EXCEEDED", 1)],
)
def test_the_limit_boundary_is_strictly_greater(this_total: int, verdict: str, excess: int) -> None:
    """한도 100,000: 노출 99,999·100,000은 통과(같으면 통과), 100,001은 초과 — strict >. 초과분은 그 차이다"""
    buyer = _buyer()
    result = evaluate(raw_open_so(buyer, status="RECEIVED", total=this_total))
    assert result.verdict.value == verdict and result.excess_amount == excess
    assert result.exposure_after_amount == this_total
    assert result.limit_amount == 100_000 and result.limit_currency == "USD"


def test_a_limit_of_zero_means_no_credit_but_a_free_order_is_not_blocked() -> None:
    """한도 0 = 신용 거래 불가: 금액>0이면 항상 초과, 금액 0(전 라인 무상)이면 NO_INCREMENT로 통과(기존 초과 상태 때문에 막히지 않는다)"""
    buyer = _buyer(limit=0)
    over = evaluate(raw_open_so(buyer, status="RECEIVED", total=1))
    assert over.verdict is CreditVerdict.EXCEEDED and over.excess_amount == 1
    free = evaluate(raw_open_so(buyer, status="RECEIVED", total=0))
    assert free.verdict is CreditVerdict.WITHIN_LIMIT
    assert free.reason_codes == (ReasonCode.NO_INCREMENT.value,)
    assert free.this_order_amount == 0


def test_no_limit_means_not_managed_and_skips_conversion() -> None:
    """한도 NULL = 여신 관리 안 함(NOT_MANAGED) — 환산·노출 계산을 건너뛰므로 비교 불가 통화의 미결 SO가 있어도 막히지 않는다"""
    buyer = _buyer(limit=None, currency=None)
    raw_open_so(buyer, status="CONFIRMED", total=500, currency="EUR", fx_rate=1500)
    result = evaluate(raw_open_so(buyer, status="RECEIVED", total=10**9))
    assert result.verdict is CreditVerdict.NOT_MANAGED
    assert result.exposure_after_amount is None and result.excess_amount == 0
    assert result.unconverted == ()


# ── 통화 ─────────────────────────────────────────────────────────────────────


def test_same_currency_adds_the_open_orders_and_this_order() -> None:
    """같은 통화 — 노출 = 미결 SO 합 + 이번 SO. 한도 100,000에 미결 60,000 + 이번 40,001 → 초과분 1"""
    buyer = _buyer()
    raw_open_so(buyer, status="CONFIRMED", total=60_000)
    this = evaluate(raw_open_so(buyer, status="RECEIVED", total=40_001))
    assert this.open_orders_amount == 60_000 and this.this_order_amount == 40_001
    assert this.exposure_after_amount == 100_001 and this.excess_amount == 1
    assert this.verdict is CreditVerdict.EXCEEDED


def test_a_krw_limit_converts_foreign_orders_with_each_orders_own_rate() -> None:
    """KRW 한도 + USD 전표 — 각 SO를 **자기 확정 시점 환율**로 환산한다(HALF_UP). 미결 USD 10.00@1300 + 이번 USD 5.01@1350"""
    buyer = _buyer(limit=50_000, currency="KRW")
    raw_open_so(buyer, status="CONFIRMED", total=1000, currency="USD", fx_rate=1300)  # 13,000원
    result = evaluate(
        raw_open_so(buyer, status="RECEIVED", total=501, currency="USD", fx_rate=1350)
    )
    # 5.01 × 1350 = 6,763.5 → HALF_UP 6,764
    assert result.open_orders_amount == 13_000 and result.this_order_amount == 6_764
    assert result.exposure_after_amount == 19_764 and result.verdict is CreditVerdict.WITHIN_LIMIT
    assert result.fx_doc_currency == "USD" and result.fx_rate is not None


def test_a_non_krw_limit_with_a_different_currency_is_unevaluable_not_a_pass() -> None:
    """한도 USD + 전표 KRW(역환산 없음)·한도 USD + 미결 EUR은 비교 불가 → UNEVALUABLE(통과 아님) — 사유 코드와 미환산 목록이 실린다"""
    buyer = _buyer()
    krw = evaluate(raw_open_so(buyer, status="RECEIVED", total=100, currency="KRW"))
    assert krw.verdict is CreditVerdict.UNEVALUABLE
    assert krw.reason_codes == (ReasonCode.CURRENCY_NOT_CONVERTIBLE.value,)
    assert (
        krw.excess_amount == 0 and krw.exposure_after_amount is None
    )  # 금액을 모른다 — 0으로 대신하지 않는다
    raw_open_so(buyer, status="CONFIRMED", total=50, currency="EUR", fx_rate=1500)
    mixed = evaluate(raw_open_so(buyer, status="RECEIVED", total=100, currency="USD"))
    assert mixed.verdict is CreditVerdict.UNEVALUABLE
    assert {u["currency"] for u in mixed.unconverted} == {"EUR"}


def test_the_unconverted_list_is_capped_at_five_documents() -> None:
    """미환산 SO 목록은 최대 5건(문서번호·통화만)이다"""
    buyer = _buyer()
    for _ in range(7):
        raw_open_so(buyer, status="CONFIRMED", total=1, currency="EUR", fx_rate=1500)
    result = evaluate(raw_open_so(buyer, status="RECEIVED", total=1))
    assert result.verdict is CreditVerdict.UNEVALUABLE and len(result.unconverted) == 5
    assert all(set(u) == {"doc_number", "currency"} for u in result.unconverted)


# ── 미결 SO 술어 — 전수 ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "confirmed", "deleted", "counted"),
    [
        ("CONFIRMED", None, False, True),
        ("PARTIALLY_ALLOCATED", None, False, True),
        ("ALLOCATED", None, False, True),
        ("IN_SHIPMENT", None, False, True),
        ("ON_HOLD", True, False, True),  # 확정 이력이 있는 보류는 노출에 남는다
        ("ON_HOLD", False, False, False),  # 접수 단계 보류(확정 전)는 약정이 아니다
        ("RECEIVED", None, False, False),  # 접수(게이트 전)
        ("COMPLETED", None, False, False),  # 선적 완료 — 채권 영역(S3-3 provider)
        ("CANCELLED", False, False, False),
        ("CONFIRMED", None, True, False),  # soft delete
    ],
)
def test_the_open_order_predicate_covers_exactly_the_committed_orders(
    status: str, confirmed: bool | None, deleted: bool, counted: bool
) -> None:
    """미결 SO 술어 전수 — 한 번이라도 확정돼 종결되지 않은 SO만 노출에 산입된다(확정 이력 있는 ON_HOLD 포함, 접수·완료·취소·삭제 제외)"""
    buyer = _buyer(limit=10_000_000)
    raw_open_so(buyer, status=status, total=700, confirmed=confirmed, deleted=deleted)
    result = evaluate(raw_open_so(buyer, status="RECEIVED", total=300))
    assert result.open_orders_amount == (700 if counted else 0), status


def test_other_buyers_orders_and_the_order_itself_are_excluded() -> None:
    """타 거래처의 미결 SO와 평가 대상 자기 자신(이미 확정된 재평가)은 노출에 두 번 산입되지 않는다"""
    buyer, other = _buyer(limit=10_000_000), _buyer(limit=10_000_000)
    raw_open_so(other, status="CONFIRMED", total=999)
    mine = raw_open_so(buyer, status="CONFIRMED", total=400)  # 이미 확정된 자기 자신을 재평가한다
    result = evaluate(mine)
    assert result.open_orders_amount == 0 and result.this_order_amount == 400
    assert result.exposure_after_amount == 400


def test_splitting_an_order_does_not_escape_the_limit() -> None:
    """SO 쪼개기 — 한도 100,000에 60,000 + 60,000을 순차 확정하면 두 번째는 첫 번째를 보고 초과다"""
    buyer = _buyer()
    first = raw_open_so(buyer, status="RECEIVED", total=60_000)
    assert evaluate(first).verdict is CreditVerdict.WITHIN_LIMIT
    with unit_of_work() as uow:  # 첫 번째 확정(확정 통로의 흉내)
        uow.session.execute(
            text(
                "UPDATE sales_orders SET status='CONFIRMED', confirmed_at = now(), credit_verdict = 'NOT_MANAGED', pi_gate_verdict = 'NOT_APPLICABLE' WHERE id = :i"
            ),
            {"i": first},
        )
    second = evaluate(raw_open_so(buyer, status="RECEIVED", total=60_000))
    assert second.verdict is CreditVerdict.EXCEEDED and second.excess_amount == 20_000


def test_the_open_order_predicate_is_a_negative_set_so_new_statuses_count_by_default() -> None:
    """술어는 음수 집합(완료·취소 제외) 형태다 — 후속 세션이 새 상태를 추가해도 조용히 0으로 빠지지 않고 기본 산입된다"""
    assert set(CLOSED_STATUSES) == {"COMPLETED", "CANCELLED"}
    sql = str(open_orders_stmt(1).compile(compile_kwargs={"literal_binds": True}))
    assert "NOT IN ('COMPLETED', 'CANCELLED')" in sql and "confirmed_at IS NOT NULL" in sql
    assert "deleted_at IS NULL" in sql


# ── 미수 provider ────────────────────────────────────────────────────────────


def test_the_default_provider_is_unreflected_and_never_adds_zero() -> None:
    """기본 provider는 (reflected=False, amount=None) — 노출에 0이 더해지지 않고 exposure_is_partial=true(미수 미반영 표시)"""
    term = providers.get_receivable_provider().outstanding(None, 1, "USD")  # type: ignore[arg-type]
    assert term.reflected is False and term.amount is None
    assert providers.is_default_provider()
    buyer = _buyer()
    result = evaluate(raw_open_so(buyer, status="RECEIVED", total=10))
    assert result.receivables_reflected is False and result.receivable_amount is None
    assert result.exposure_is_partial is True
    assert result.to_snapshot()["receivables_reflected"] is False


def test_the_reflected_term_must_carry_an_amount_and_vice_versa() -> None:
    """미수 항 값 객체 불변식 — 반영(reflected=True)이면 금액 필수, 미반영이면 금액 없음(0으로 대신 금지)"""
    with pytest.raises(ValueError):
        ReceivableTerm(reflected=True, amount=None)
    with pytest.raises(ValueError):
        ReceivableTerm(reflected=False, amount=0)
    assert ReceivableTerm(reflected=True, amount=0).amount == 0


def test_a_registered_provider_adds_its_amount_and_clears_the_partial_flag() -> None:
    """실 provider가 등록되면(S3-3 시뮬레이션) 반영 미수가 노출에 더해지고 partial 표시가 꺼진다 — 등록은 1회만"""

    class Fake:
        def outstanding(
            self, session: object, partner_id: int, limit_currency: str
        ) -> ReceivableTerm:
            return ReceivableTerm(reflected=True, amount=30_000)

    providers.register_receivable_provider(Fake())
    assert not providers.is_default_provider()
    buyer = _buyer()
    result = evaluate(raw_open_so(buyer, status="RECEIVED", total=70_001))
    assert result.exposure_after_amount == 100_001 and result.verdict is CreditVerdict.EXCEEDED
    assert result.receivables_reflected is True and result.exposure_is_partial is False
    with pytest.raises(RuntimeError):
        providers.register_receivable_provider(Fake())


def test_a_failing_provider_makes_the_evaluation_unevaluable_not_a_pass() -> None:
    """provider가 예외를 던지면 통과가 아니라 UNEVALUABLE(RECEIVABLE_PROVIDER_ERROR) — fail-closed"""

    class Broken:
        def outstanding(
            self, session: object, partner_id: int, limit_currency: str
        ) -> ReceivableTerm:
            raise RuntimeError("채권 조회 실패")

    providers.register_receivable_provider(Broken())
    buyer = _buyer()
    result = evaluate(raw_open_so(buyer, status="RECEIVED", total=1))
    assert result.verdict is CreditVerdict.UNEVALUABLE
    assert ReasonCode.RECEIVABLE_PROVIDER_ERROR.value in result.reason_codes


# ── 잠금 변형·증적 ───────────────────────────────────────────────────────────


def test_the_advisory_evaluation_is_marked_and_agrees_with_the_locked_one() -> None:
    """잠금 없는 참고 평가는 advisory=True로 표시되고, 같은 데이터에서 잠금 평가와 같은 판정·초과분이다"""
    buyer = _buyer()
    so = raw_open_so(buyer, status="RECEIVED", total=100_500)
    locked, advisory = evaluate(so), evaluate(so, locked=False)
    assert locked.advisory is False and advisory.advisory is True
    assert (locked.verdict, locked.excess_amount) == (advisory.verdict, advisory.excess_amount)


def test_evaluate_credit_refuses_a_buyer_lock_for_another_partner() -> None:
    """잠근 거래처와 SO의 거래처가 다르면 거부한다(잠금 순서·결속 위반 방어)"""
    from app.modules.credit.locking import lock_buyer_for_credit
    from app.modules.sales_orders.models import SalesOrder

    buyer, other = _buyer(), _buyer()
    so = raw_open_so(buyer, status="RECEIVED", total=10)
    with unit_of_work() as uow:
        locked_other = lock_buyer_for_credit(uow.session, other)
        order = uow.session.get(SalesOrder, so)
        assert order is not None
        with pytest.raises(ValueError):
            evaluation.evaluate_credit(uow.session, locked_other, order)


def test_lock_buyer_returns_the_freshly_read_limit() -> None:
    """잠금 후 한도를 새로 읽는다(populate_existing) — 같은 세션이 캐시한 옛 한도를 쓰지 않는다"""
    from app.modules.credit.locking import lock_buyer_for_credit
    from app.modules.partners.models import Partner

    buyer = _buyer(limit=100)
    with unit_of_work() as uow:
        cached = uow.session.get(Partner, buyer)
        assert cached is not None and cached.credit_limit_amount == 100
        with owner_engine.begin() as connection:
            connection.execute(
                text("UPDATE partners SET credit_limit_amount = 777 WHERE id = :i"),
                {"i": buyer},
            )
        locked = lock_buyer_for_credit(uow.session, buyer)
        assert locked.partner.credit_limit_amount == 777


def test_the_snapshot_is_scalar_small_and_free_of_cost_keys() -> None:
    """평가 스냅샷(승인 표시용 상세)은 스칼라·키 20개 이하이고 원가·마진 계열 키가 없다(E2 — 노출은 판매금액 합)"""
    from app.core.logging.redaction import is_sensitive_key

    buyer = _buyer()
    snapshot = evaluate(raw_open_so(buyer, status="RECEIVED", total=100_100)).to_snapshot()
    assert len(snapshot) <= 20
    assert all(isinstance(v, int | str | bool | type(None)) for v in snapshot.values())
    assert not [k for k in snapshot if is_sensitive_key(k)]
    assert not [k for k in snapshot if any(w in k for w in ("cost", "margin", "purchase"))]
    assert snapshot["verdict"] == "EXCEEDED" and snapshot["excess_amount"] == 100
