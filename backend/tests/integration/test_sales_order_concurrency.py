"""J. SO 동시성 — 잔량 초과 동시 생성=1건·PI→SO 1:1 경합·취소 vs 후속 생성 20회·교차 잠금 데드락 0·중복 바이어 PO 경합·롤백 원자성·잠금 순서
(S3-1 PR-7a / ADR-0052·0059 / design-A A4·A13 / design-B B3).

★ 순차 실행은 증거가 아니다 — 실제 스레드를 Barrier로 동시에 출발시킨다(tests/support/concurrency.py). 각 스레드는 자기 세션을 연다.
  이 파일의 테스트는 커밋을 동반하므로 병렬(pytest-xdist)로 돌리지 않는다. 결과 불변식(초과 소비 0·고아 후속 0·중복 PO 0·이중 전이 0)을 본다.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError

from app.core.db.session import engine, owner_engine
from app.core.db.uow import unit_of_work
from app.core.errors.exceptions import AppError
from app.core.time import today_kst
from app.modules.identity.models import RoleCode
from app.modules.identity.service import AuthenticatedUser
from app.modules.quotations import service as quotations
from app.modules.sales_orders import service as sos
from app.modules.trade_chain import lifecycle, reference, so_reference
from app.modules.trade_docs.locking import LOCK_ORDER
from app.modules.trade_docs.snapshot import LineSnapshot
from tests.factories.trade import (
    PAYMENT_TT_ADVANCE_30,
    create_bank_account,
    create_buyer,
    create_priced_sku,
    unique,
)
from tests.support.concurrency import run_concurrently
from tests.support.factories import create_market, create_user

pytestmark = pytest.mark.group_j


def _actor() -> AuthenticatedUser:
    address = f"{unique('so-conc')}@example.com"
    user_id = create_user(address, roles=(RoleCode.TRADE,))
    return AuthenticatedUser(
        id=user_id,
        email=address,
        display_name="동시성",
        roles=frozenset({RoleCode.TRADE}),
        session_id=0,
    )


def _scalar(sql: str, **params: Any) -> Any:
    with owner_engine.connect() as connection:
        return connection.execute(text(sql), params).scalar_one()


def _issued_qt(
    actor: AuthenticatedUser, *, quantity: int = 10, valid_days: int = 30, buyer: int | None = None
) -> dict[str, Any]:
    """발행된 견적 1건(라인 1개·수량 quantity) — 서비스 계층으로 만든다."""
    create_market("US")
    _s, draft = quotations.create_quotation(
        actor=actor,
        idempotency_key=unique("qt"),
        payload={
            "buyer_partner_id": buyer or create_buyer(),
            "dest_market_code": "US",
            "currency": "USD",
            "fx_rate": "1350",
            "valid_until": today_kst() + timedelta(days=valid_days),
            "payment_terms": PAYMENT_TT_ADVANCE_30,
            "incoterm": {"code": "FOB", "place": "Busan", "year": 2020},
            "lines": [{"sku_id": create_priced_sku(amount=100), "quantity": quantity}],
        },
    )
    _s, issued = lifecycle.issue_quotation(
        actor=actor, idempotency_key=unique("iss"), qt_id=draft["id"], version=draft["version"]
    )
    return issued


def _pi(actor: AuthenticatedUser, qt: dict[str, Any]) -> dict[str, Any]:
    _s, body = reference.create_proforma_invoice(
        actor=actor,
        idempotency_key=unique("pi"),
        qt_id=qt["id"],
        payload={
            "version": qt["version"],
            "valid_until": today_kst() + timedelta(days=30),
            "bank_account_id": create_bank_account("USD"),
        },
    )
    return body


def _so_from_qt(
    actor: AuthenticatedUser, qt: dict[str, Any], key: str, **extra: Any
) -> tuple[int, dict[str, Any]]:
    return so_reference.create_sales_order_from_quotation(
        actor=actor,
        idempotency_key=key,
        qt_id=qt["id"],
        payload={"version": qt["version"], **extra},
    )


def _so_from_pi(
    actor: AuthenticatedUser, pi: dict[str, Any], key: str, **extra: Any
) -> tuple[int, dict[str, Any]]:
    return so_reference.create_sales_order_from_proforma_invoice(
        actor=actor,
        idempotency_key=key,
        pi_id=pi["id"],
        payload={"version": pi["version"], **extra},
    )


def _cancel_so(actor: AuthenticatedUser, so: dict[str, Any], key: str) -> Any:
    return lifecycle.transition_sales_order(
        actor=actor,
        idempotency_key=key,
        so_id=so["id"],
        to="CANCELLED",
        version=so["version"],
        reason="동시 취소",
    )


def _only_conflicts(outcomes: list[Any]) -> None:
    """실패는 전부 정의된 409(AppError)여야 한다 — 데드락·IntegrityError·500이 새면 실패"""
    for outcome in outcomes:
        if not outcome.ok:
            assert isinstance(outcome.error, AppError), repr(outcome.error)
            assert outcome.error.status_code == 409, outcome.error


def _live_sos(where: str, **params: Any) -> int:
    return int(
        _scalar(
            f"SELECT count(*) FROM sales_orders WHERE {where} AND status <> 'CANCELLED'"
            " AND deleted_at IS NULL",
            **params,
        )
    )


# ── 잔량 초과 동시 생성 ─────────────────────────────────────────────────────


def test_two_concurrent_full_quantity_creations_admit_exactly_one() -> None:
    """같은 QT에서 잔량 전부를 요구하는 SO 생성 2건 동시(다른 키) → 정확히 1건 성공, 나머지는 409 EXCEEDS_OPEN — 초과 소비 0·흔적 0"""
    actor = _actor()
    qt = _issued_qt(actor, quantity=10)

    def worker(i: int) -> Any:
        return _so_from_qt(actor, qt, f"full-{i}")

    outcomes = run_concurrently(worker, workers=2)
    wins = [o for o in outcomes if o.ok]
    losses = [o for o in outcomes if not o.ok]
    assert len(wins) == 1 and len(losses) == 1
    assert isinstance(losses[0].error, AppError)
    assert losses[0].error.code == "TRADE_DOCS.QUANTITY.EXCEEDS_OPEN"
    assert _scalar("SELECT count(*) FROM sales_orders") == 1
    assert _scalar("SELECT coalesce(sum(quantity), 0) FROM sales_order_lines") == 10
    assert _scalar("SELECT count(*) FROM sales_order_status_log") == 1  # 실패한 쪽은 흔적이 없다
    assert (
        _scalar("SELECT count(*) FROM events WHERE event_type = 'sales_orders.sales_order.created'")
        == 1
    )


@pytest.mark.parametrize("round_no", range(3))
def test_many_small_creations_never_oversubscribe_the_source_line(round_no: int) -> None:
    """수량 10에 2씩 6스레드 동시 → 정확히 5건 성공·1건 EXCEEDS_OPEN·소비 합 = 10(잔량 정확히 0), 번호 결번 0"""
    actor = _actor()
    qt = _issued_qt(actor, quantity=10)
    line_id = qt["lines"][0]["id"]

    def worker(i: int) -> Any:
        return _so_from_qt(
            actor, qt, f"small-{round_no}-{i}", lines=[{"source_line_id": line_id, "quantity": 2}]
        )

    outcomes = run_concurrently(worker, workers=6)
    _only_conflicts(outcomes)
    assert sum(1 for o in outcomes if o.ok) == 5
    assert [o.error.code for o in outcomes if not o.ok] == ["TRADE_DOCS.QUANTITY.EXCEEDS_OPEN"]  # type: ignore[union-attr]
    assert _scalar("SELECT sum(quantity) FROM sales_order_lines") == 10
    with owner_engine.connect() as connection:
        numbers = sorted(
            int(n.rsplit("-", 1)[1])
            for (n,) in connection.execute(text("SELECT doc_number FROM sales_orders"))
        )
    assert numbers == [1, 2, 3, 4, 5]  # 실패한 시도가 번호를 소비하지 않았다(결번 0)


def test_double_click_with_the_same_key_creates_one_so() -> None:
    """생성 더블클릭(같은 키 동시 2요청) → 둘 다 같은 결과·SO 1건·이력 1행·이벤트 1건(멱등 claim 직렬화)"""
    actor = _actor()
    qt = _issued_qt(actor)

    def worker(_i: int) -> Any:
        return _so_from_qt(actor, qt, "dbl-create")[1]

    outcomes = run_concurrently(worker, workers=2)
    assert all(o.ok for o in outcomes), [o.error for o in outcomes]
    assert outcomes[0].value == outcomes[1].value
    assert _scalar("SELECT count(*) FROM sales_orders") == 1
    assert _scalar("SELECT count(*) FROM sales_order_status_log") == 1


def test_creations_from_different_quotations_do_not_block_each_other() -> None:
    """서로 다른 QT의 SO 생성 8건 동시 → 전부 성공(직렬화는 같은 원천 안에서만) — 번호 8개 연속·중복 0"""
    actor = _actor()
    quotes = [_issued_qt(actor) for _ in range(8)]

    def worker(i: int) -> Any:
        return _so_from_qt(actor, quotes[i], f"diff-qt-{i}")[1]["doc_number"]

    outcomes = run_concurrently(worker, workers=8)
    assert all(o.ok for o in outcomes), [o.error for o in outcomes]
    numbers = sorted(int(o.value.rsplit("-", 1)[1]) for o in outcomes)
    assert numbers == list(range(1, 9))


# ── PI → SO 활성 1:1 경합 ───────────────────────────────────────────────────


@pytest.mark.parametrize("round_no", range(3))
def test_two_concurrent_creations_from_one_pi_admit_exactly_one(round_no: int) -> None:
    """같은 PI에서 SO 2건 동시(다른 키) → 정확히 1건 성공·나머지 409 ALREADY_CONVERTED — 활성 SO 1건·고아 없음"""
    actor = _actor()
    qt = _issued_qt(actor)
    pi = _pi(actor, qt)

    def worker(i: int) -> Any:
        return _so_from_pi(actor, pi, f"pi-{round_no}-{i}")

    outcomes = run_concurrently(worker, workers=3)
    _only_conflicts(outcomes)
    assert sum(1 for o in outcomes if o.ok) == 1
    assert {o.error.code for o in outcomes if not o.ok} == {  # type: ignore[union-attr]
        "TRADE_DOCS.REFERENCE.ALREADY_CONVERTED"
    }
    assert _live_sos("pi_id = :p", p=pi["id"]) == 1


@pytest.mark.parametrize("round_no", range(4))
def test_a_pi_creation_racing_a_cancel_of_its_so_ends_with_one_live_so(round_no: int) -> None:
    """SO 취소 vs 같은 PI의 새 SO 생성 동시 — (취소 후 생성 성공) 또는 (생성 409 ALREADY_CONVERTED·취소 성공) 중 하나, 활성 SO는 항상 ≤ 1"""
    actor = _actor()
    qt = _issued_qt(actor)
    pi = _pi(actor, qt)
    first = _so_from_pi(actor, pi, f"seed-{round_no}")[1]

    def worker(i: int) -> Any:
        if i == 0:
            return _cancel_so(actor, first, f"c-{round_no}")
        return _so_from_pi(actor, pi, f"re-{round_no}")

    outcomes = run_concurrently(worker, workers=2)
    _only_conflicts(outcomes)
    assert outcomes[0].ok
    assert _live_sos("pi_id = :p", p=pi["id"]) <= 1
    assert _live_sos("pi_id = :p", p=pi["id"]) == (1 if outcomes[1].ok else 0)


# ── 취소 vs 후속 생성 (20회) ────────────────────────────────────────────────


@pytest.mark.parametrize("round_no", range(10))
def test_quotation_cancel_versus_direct_so_creation_never_orphans_a_live_so(round_no: int) -> None:
    """QT 취소 vs 그 QT의 SO 생성 동시(10회) — 정확히 한쪽 결과로 수렴: 취소된 QT 아래 살아 있는 SO 0건, 실패는 정의된 409뿐(데드락 0)"""
    actor = _actor()
    qt = _issued_qt(actor)

    def worker(i: int) -> Any:
        if i == 0:
            return lifecycle.transition_quotation(
                actor=actor,
                idempotency_key=f"qc-{round_no}",
                qt_id=qt["id"],
                to="CANCELLED",
                version=qt["version"],
                reason="동시 취소",
            )
        return _so_from_qt(actor, qt, f"sc-{round_no}")

    outcomes = run_concurrently(worker, workers=2)
    _only_conflicts(outcomes)
    assert any(o.ok for o in outcomes)
    status = _scalar("SELECT status FROM quotations WHERE id = :i", i=qt["id"])
    live = _live_sos("qt_id = :i", i=qt["id"])
    if status == "CANCELLED":
        assert live == 0, "취소된 QT 아래 살아 있는 SO(고아 후속)"
        assert not outcomes[1].ok
    else:
        assert live == 1 and not outcomes[0].ok  # 생성이 이겼으면 QT 취소는 후속 생존으로 409


@pytest.mark.parametrize("round_no", range(10))
def test_pi_cancel_versus_so_creation_never_orphans_a_live_so(round_no: int) -> None:
    """PI 취소 vs 그 PI의 SO 생성 동시(10회) — 취소된 PI 아래 살아 있는 SO 0건, 실패는 정의된 409뿐(PARENT.NOT_USABLE 또는 SUCCESSOR_ALIVE)"""
    actor = _actor()
    qt = _issued_qt(actor)
    pi = _pi(actor, qt)

    def worker(i: int) -> Any:
        if i == 0:
            return lifecycle.transition_proforma_invoice(
                actor=actor,
                idempotency_key=f"pc-{round_no}",
                pi_id=pi["id"],
                to="CANCELLED",
                version=pi["version"],
                reason="동시 PI 취소",
            )
        return _so_from_pi(actor, pi, f"ps-{round_no}")

    outcomes = run_concurrently(worker, workers=2)
    _only_conflicts(outcomes)
    assert any(o.ok for o in outcomes)
    status = _scalar("SELECT status FROM proforma_invoices WHERE id = :i", i=pi["id"])
    live = _live_sos("pi_id = :i", i=pi["id"])
    if status == "CANCELLED":
        assert live == 0, "취소된 PI 아래 살아 있는 SO(고아 후속)"
        assert not outcomes[1].ok
    else:
        assert live == 1 and not outcomes[0].ok


@pytest.mark.parametrize("round_no", range(6))
def test_so_cancel_versus_quotation_cancel_is_deadlock_free_and_ordered(round_no: int) -> None:
    """SO 취소 vs 그 QT 취소 동시 — 데드락 없이 정의된 409만, SO 취소는 항상 성공·QT는 (SO 취소 뒤 성공) 또는 (후속 생존 409·ISSUED 유지) 중 하나"""
    actor = _actor()
    qt = _issued_qt(actor)
    so = _so_from_qt(actor, qt, f"mk-{round_no}")[1]

    def worker(i: int) -> Any:
        if i == 0:
            return _cancel_so(actor, so, f"sx-{round_no}")
        return lifecycle.transition_quotation(
            actor=actor,
            idempotency_key=f"qx-{round_no}",
            qt_id=qt["id"],
            to="CANCELLED",
            version=qt["version"],
            reason="동시 QT 취소",
        )

    outcomes = run_concurrently(worker, workers=2)
    _only_conflicts(outcomes)
    assert outcomes[0].ok
    assert _scalar("SELECT status FROM sales_orders WHERE id = :i", i=so["id"]) == "CANCELLED"
    qt_status = _scalar("SELECT status FROM quotations WHERE id = :i", i=qt["id"])
    assert qt_status == ("CANCELLED" if outcomes[1].ok else "ISSUED")
    assert (
        _scalar(
            "SELECT count(*) FROM sales_order_status_log WHERE sales_order_id = :i AND to_status = 'CANCELLED'",
            i=so["id"],
        )
        == 1
    )


@pytest.mark.parametrize("round_no", range(6))
def test_cross_chain_operations_never_deadlock_and_never_orphan(round_no: int) -> None:
    """교차 잠금 — 같은 체인(QT→PI→SO)에 SO 취소·PI 취소·QT 취소·QT 직접 SO 생성·PI→SO 생성 5개가 동시에 덮쳐도 데드락 0(정의된 409만)이고
    끝났을 때 죽은 부모 아래 살아 있는 후속이 0건이다"""
    actor = _actor()
    qt = _issued_qt(actor, quantity=20)
    pi = _pi(actor, qt)
    so = _so_from_pi(actor, pi, f"seed-{round_no}")[1]
    line_id = qt["lines"][0]["id"]

    def worker(i: int) -> Any:
        if i == 0:
            return _cancel_so(actor, so, f"x0-{round_no}")
        if i == 1:
            return lifecycle.transition_proforma_invoice(
                actor=actor,
                idempotency_key=f"x1-{round_no}",
                pi_id=pi["id"],
                to="CANCELLED",
                version=pi["version"],
                reason="교차",
            )
        if i == 2:
            return lifecycle.transition_quotation(
                actor=actor,
                idempotency_key=f"x2-{round_no}",
                qt_id=qt["id"],
                to="CANCELLED",
                version=qt["version"],
                reason="교차",
            )
        if i == 3:
            return _so_from_qt(
                actor, qt, f"x3-{round_no}", lines=[{"source_line_id": line_id, "quantity": 5}]
            )
        return _so_from_pi(actor, pi, f"x4-{round_no}")

    outcomes = run_concurrently(worker, workers=5)
    for outcome in outcomes:
        if not outcome.ok:
            assert not isinstance(outcome.error, DBAPIError), repr(
                outcome.error
            )  # 데드락·잠금 초과
            assert isinstance(outcome.error, AppError) and outcome.error.status_code in (409, 422)
    qt_dead = _scalar("SELECT status FROM quotations WHERE id = :i", i=qt["id"]) in (
        "CANCELLED",
        "EXPIRED",
    )
    pi_dead = _scalar("SELECT status FROM proforma_invoices WHERE id = :i", i=pi["id"]) in (
        "CANCELLED",
        "EXPIRED",
    )
    if qt_dead:
        assert _live_sos("qt_id = :i", i=qt["id"]) == 0 and (
            _scalar(
                "SELECT count(*) FROM proforma_invoices WHERE qt_id = :i AND status NOT IN ('CANCELLED', 'EXPIRED')",
                i=qt["id"],
            )
            == 0
        ), "취소된 QT 아래 살아 있는 후속"
    if pi_dead:
        assert _live_sos("pi_id = :i", i=pi["id"]) == 0, "취소된 PI 아래 살아 있는 SO"
    assert _live_sos("pi_id = :i", i=pi["id"]) <= 1  # PI→SO 활성 1:1
    consumed = _scalar(
        "SELECT coalesce(sum(l.quantity), 0) FROM sales_order_lines l JOIN sales_orders s ON s.id = l.so_id"
        " WHERE s.status <> 'CANCELLED' AND s.deleted_at IS NULL AND l.qt_line_id = :q",
        q=line_id,
    )
    assert consumed <= 20  # 직접 경로 소비가 주문량을 넘지 않는다


# ── 소비 경합: 수량 증가 vs 다른 생성 ───────────────────────────────────────


@pytest.mark.parametrize("round_no", range(4))
def test_quantity_increase_versus_another_creation_never_exceeds_the_source(round_no: int) -> None:
    """SO(4) 수량을 10으로 늘리는 편집 vs 같은 QT의 잔량 6 전부를 가져가는 새 SO 생성 동시 — 정확히 한쪽만 성공(EXCEEDS_OPEN), 총 소비 ≤ 10"""
    actor = _actor()
    qt = _issued_qt(actor, quantity=10)
    line_id = qt["lines"][0]["id"]
    first = _so_from_qt(
        actor, qt, f"base-{round_no}", lines=[{"source_line_id": line_id, "quantity": 4}]
    )[1]
    so_line = first["lines"][0]["id"]

    def worker(i: int) -> Any:
        if i == 0:
            return sos.update_line(
                actor=actor,
                so_id=first["id"],
                line_id=so_line,
                payload={"version": first["version"], "quantity": 10},
            )
        return _so_from_qt(
            actor, qt, f"more-{round_no}", lines=[{"source_line_id": line_id, "quantity": 6}]
        )

    outcomes = run_concurrently(worker, workers=2)
    _only_conflicts(outcomes)
    assert sum(1 for o in outcomes if o.ok) == 1
    failed = next(o for o in outcomes if not o.ok)
    assert isinstance(failed.error, AppError)
    assert failed.error.code == "TRADE_DOCS.QUANTITY.EXCEEDS_OPEN"
    assert (
        _scalar(
            "SELECT coalesce(sum(quantity), 0) FROM sales_order_lines l JOIN sales_orders s ON s.id = l.so_id"
            " WHERE s.status <> 'CANCELLED' AND l.deleted_at IS NULL"
        )
        == 10
    )


# ── 중복 바이어 PO 경합 ─────────────────────────────────────────────────────


def _direct_draft(buyer: int, sku: int, po: str) -> sos.SalesOrderDraft:
    return sos.SalesOrderDraft(
        buyer_partner_id=buyer,
        currency="USD",
        dest_market_code="US",
        buyer_po_no=po,
        lines=[
            sos.NewSoLine(
                LineSnapshot(
                    sku_id=sku,
                    sku_code="X",
                    sku_name_ko="테스트",
                    sku_name_en=None,
                    sku_kind="SINGLE",
                    buyer_item_code=None,
                    unit_price_amount=100,
                    list_price_amount=100,
                    price_basis="BUYER_PO",
                    is_free=False,
                    price_reason=None,
                ),
                3,
            )
        ],
    )


@pytest.mark.parametrize("round_no", range(3))
def test_concurrent_direct_landings_of_one_buyer_po_admit_exactly_one(round_no: int) -> None:
    """공통 부모 행이 없는 직접 착지 6건이 같은 (바이어, PO번호 — 대소문자·공백만 다름)를 동시에 → 정확히 1건 성공, 나머지는 **409 DUPLICATE_BUYER_PO**
    (선검사를 빠져나간 경합도 DB 부분 유니크가 잡고 같은 409로 번역 — IntegrityError·500 0건)"""
    actor = _actor()
    buyer = create_buyer()
    sku = create_priced_sku(amount=100)
    create_market("US")
    variants = ["PO-RACE", "po-race", " PO-RACE ", "P O-RACE", "Po-Race", "PO-RACE"]

    def worker(i: int) -> Any:
        with unit_of_work() as uow:
            return sos.create_received_sales_order(
                uow.session, actor=actor, draft=_direct_draft(buyer, sku, variants[i])
            ).doc_number

    outcomes = run_concurrently(worker, workers=6)
    _only_conflicts(outcomes)
    assert sum(1 for o in outcomes if o.ok) == 1
    assert {o.error.code for o in outcomes if not o.ok} == {  # type: ignore[union-attr]
        "TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO"
    }
    assert _scalar("SELECT count(*) FROM sales_orders") == 1
    assert (
        _scalar("SELECT count(*) FROM sales_order_status_log") == 1
    )  # 진 쪽은 이력·이벤트·번호 흔적 0
    assert _scalar("SELECT last_number FROM doc_number_seq WHERE prefix = 'SO'") == 1


@pytest.mark.parametrize("round_no", range(3))
def test_two_edits_to_the_same_po_number_admit_exactly_one(round_no: int) -> None:
    """서로 다른 두 SO의 PO번호를 같은 값으로 동시에 고치면 정확히 한쪽만 성공 — 진 쪽은 409 DUPLICATE_BUYER_PO(DB 유니크 경합 번역)·값 무변"""
    actor = _actor()
    buyer = create_buyer()
    qt = _issued_qt(actor, quantity=10, buyer=buyer)
    line_id = qt["lines"][0]["id"]
    a = _so_from_qt(actor, qt, f"a-{round_no}", lines=[{"source_line_id": line_id, "quantity": 2}])[
        1
    ]
    b = _so_from_qt(actor, qt, f"b-{round_no}", lines=[{"source_line_id": line_id, "quantity": 2}])[
        1
    ]

    def worker(i: int) -> Any:
        target = a if i == 0 else b
        return sos.update_sales_order(
            actor=actor,
            so_id=target["id"],
            payload={
                "version": target["version"],
                "buyer_po_no": "SAME-PO" if i == 0 else " same-po",
            },
        )

    outcomes = run_concurrently(worker, workers=2)
    _only_conflicts(outcomes)
    assert sum(1 for o in outcomes if o.ok) == 1
    loser = next(o for o in outcomes if not o.ok)
    assert isinstance(loser.error, AppError)
    assert loser.error.code == "TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO"
    assert _scalar("SELECT count(*) FROM sales_orders WHERE buyer_po_no_key = 'SAME-PO'") == 1
    assert _scalar("SELECT count(*) FROM sales_orders WHERE buyer_po_no IS NOT NULL") == 1


# ── 전이 직렬화·더블클릭 ────────────────────────────────────────────────────


def test_cancel_double_click_with_the_same_key_is_one_history_row() -> None:
    """취소 더블클릭(같은 키 동시 2요청) → 둘 다 같은 결과·이력 1행(CANCELLED)·이벤트 1건"""
    actor = _actor()
    so = _so_from_qt(actor, _issued_qt(actor), "seed")[1]

    def worker(_i: int) -> Any:
        return _cancel_so(actor, so, "dbl-cancel")[1]["status"]

    outcomes = run_concurrently(worker, workers=2)
    assert all(o.ok for o in outcomes), [o.error for o in outcomes]
    assert [o.value for o in outcomes] == ["CANCELLED", "CANCELLED"]
    assert (
        _scalar(
            "SELECT count(*) FROM sales_order_status_log WHERE sales_order_id = :i AND to_status = 'CANCELLED'",
            i=so["id"],
        )
        == 1
    )


@pytest.mark.parametrize("round_no", range(4))
def test_hold_versus_cancel_of_the_same_version_admits_exactly_one(round_no: int) -> None:
    """같은 version으로 보류 vs 취소 동시 — 정확히 1건 성공, 진 쪽은 409(낡은 version)이고 이력은 탄생+1행"""
    actor = _actor()
    so = _so_from_qt(actor, _issued_qt(actor), f"seed-{round_no}")[1]

    def worker(i: int) -> Any:
        return lifecycle.transition_sales_order(
            actor=actor,
            idempotency_key=f"t-{round_no}-{i}",
            so_id=so["id"],
            to="ON_HOLD" if i == 0 else "CANCELLED",
            version=so["version"],
            reason="동시 전이",
        )

    outcomes = run_concurrently(worker, workers=2)
    _only_conflicts(outcomes)
    assert sum(1 for o in outcomes if o.ok) == 1
    assert (
        _scalar("SELECT count(*) FROM sales_order_status_log WHERE sales_order_id = :i", i=so["id"])
        == 2
    )


@pytest.mark.parametrize("round_no", range(4))
def test_edit_versus_cancel_leaves_a_consistent_document(round_no: int) -> None:
    """접수 편집(라인 수량 변경) vs 취소 동시 — 취소가 이기면 편집은 409(낡은 version 또는 FROZEN), 편집이 이기면 취소가 새 version을 못 봐 409 —
    어느 쪽이든 헤더 합계 = Σ활성 라인(검산 0건)"""
    from app.modules.trade_docs.verify import verify_document_totals

    actor = _actor()
    so = _so_from_qt(actor, _issued_qt(actor), f"seed-{round_no}")[1]
    line = so["lines"][0]["id"]

    def worker(i: int) -> Any:
        if i == 0:
            return sos.update_line(
                actor=actor,
                so_id=so["id"],
                line_id=line,
                payload={"version": so["version"], "quantity": 3},
            )
        return _cancel_so(actor, so, f"ec-{round_no}")

    outcomes = run_concurrently(worker, workers=2)
    _only_conflicts(outcomes)
    assert sum(1 for o in outcomes if o.ok) == 1
    with unit_of_work() as uow:
        assert verify_document_totals(uow.session) == []


# ── 롤백 원자성 ─────────────────────────────────────────────────────────────


def test_a_failure_while_writing_lines_rolls_back_the_header_number_history_event_and_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """라인 기록 도중 실패 → SO·라인·이력·이벤트·채번·멱등 키가 전부 롤백(번호 카운터 미소비) — 같은 키로 재시도하면 새로 성공한다(이어받기 안전)"""
    actor = _actor()
    qt = _issued_qt(actor)
    real = sos._new_line
    calls = {"n": 0}

    def boom(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        raise RuntimeError("라인 기록 실패 주입")

    monkeypatch.setattr(sos, "_new_line", boom)
    with pytest.raises(RuntimeError):
        _so_from_qt(actor, qt, "retry-key")
    assert calls["n"] == 1
    for table in ("sales_orders", "sales_order_lines", "sales_order_status_log"):
        assert _scalar(f"SELECT count(*) FROM {table}") == 0, table
    assert _scalar("SELECT count(*) FROM events WHERE event_type LIKE 'sales_orders.%'") == 0
    assert _scalar("SELECT count(*) FROM doc_number_seq WHERE prefix = 'SO'") == 0 or (
        _scalar("SELECT last_number FROM doc_number_seq WHERE prefix = 'SO'") == 0
    )
    monkeypatch.setattr(sos, "_new_line", real)
    status, body = _so_from_qt(actor, qt, "retry-key")
    assert status == 201 and body["doc_number"].endswith("-0001")


# ── 잠금 대기 초과 ──────────────────────────────────────────────────────────


def test_a_held_so_lock_surfaces_as_lock_not_available_not_a_hang() -> None:
    """다른 트랜잭션이 SO 행을 잠근 상태에서 편집하면 무한 대기가 아니라 55P03(잠금 대기 초과)이 난다 — API는 409 LOCK_BUSY로 번역"""
    actor = _actor()
    so = _so_from_qt(actor, _issued_qt(actor), "seed")[1]
    holder = owner_engine.connect()
    tx = holder.begin()
    try:
        holder.execute(text("SELECT 1 FROM sales_orders WHERE id = :i FOR UPDATE"), {"i": so["id"]})
        with pytest.raises(DBAPIError) as caught, unit_of_work() as uow:
            uow.session.execute(text("SET LOCAL lock_timeout = '150ms'"))
            sos.update_sales_order(
                actor=actor,
                so_id=so["id"],
                payload={"version": so["version"], "internal_note": "x"},
            )
        assert getattr(caught.value.orig, "sqlstate", None) == "55P03"
    finally:
        tx.rollback()
        holder.close()
    assert _scalar("SELECT internal_note FROM sales_orders WHERE id = :i", i=so["id"]) is None


# ── 잠금 순서(LOCK_ORDER) 계측 ─────────────────────────────────────────────────

_LOCK_TABLES = {
    "idempotency_keys": "idempotency_keys",
    "partners": "partners",
    "quotations": "quotations",
    "proforma_invoices": "proforma_invoices",
    "sales_orders": "sales_orders",
    "quotation_lines": "lines",
    "proforma_invoice_lines": "lines",
    "sales_order_lines": "lines",
    "doc_number_seq": "doc_number_seq",
}
_LOCK_CLAUSE = re.compile(r"\bFOR (?:NO KEY )?(?:UPDATE|SHARE|KEY SHARE)\b", re.IGNORECASE)
_WRITE_LOCK = re.compile(r"^\s*(?:INSERT INTO|UPDATE)\s+(\w+)", re.IGNORECASE)
_FROM = re.compile(r"\bFROM\s+(\w+)", re.IGNORECASE)


def _first_lock_sequence(work: Callable[[], object]) -> list[str]:
    """work가 실행하는 SQL에서 LOCK_ORDER 대상의 **첫 접촉** 순서를 기록한다(행 잠금 절 또는 멱등·채번 쓰기)."""
    order: list[str] = []

    def listener(*args: object) -> None:
        statement = str(args[2])
        table: str | None = None
        if _LOCK_CLAUSE.search(statement):
            found = _FROM.search(statement)
            table = found.group(1) if found else None
        else:
            found = _WRITE_LOCK.match(statement)
            if found and found.group(1) in ("idempotency_keys", "doc_number_seq"):
                table = found.group(1)
        name = _LOCK_TABLES.get(table or "")
        if name and name not in order:
            order.append(name)

    event.listen(engine, "before_cursor_execute", listener)
    try:
        work()
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    return order


def _assert_follows_lock_order(sequence: list[str]) -> None:
    indexes = [LOCK_ORDER.index(name) for name in sequence]
    assert indexes == sorted(indexes), f"잠금 순서 위반: {sequence} (기준 {LOCK_ORDER})"


def test_every_so_operation_takes_locks_in_the_documented_order() -> None:
    """QT→SO 생성(멱등→바이어→QT→라인→채번)·PI→SO 생성(멱등→바이어→QT→PI→라인→채번)·복제 생성(QT→원본 SO→라인→채번)·SO 편집(SO→라인)·
    라인 수량 증가(SO→원천 라인)·취소(멱등→QT→PI→SO)가 LOCK_ORDER를 지킨다 — 채번은 항상 마지막"""
    actor = _actor()
    qt = _issued_qt(actor, quantity=20)
    holder: dict[str, Any] = {}

    def from_qt() -> None:
        _s, holder["qt_so"] = _so_from_qt(
            actor,
            qt,
            unique("lo-q"),
            lines=[{"source_line_id": qt["lines"][0]["id"], "quantity": 5}],
        )

    seen = _first_lock_sequence(from_qt)
    assert seen[:3] == ["idempotency_keys", "partners", "quotations"], seen
    assert seen[-1] == "doc_number_seq", seen
    _assert_follows_lock_order(seen)

    qt_b = _issued_qt(actor)
    pi = _pi(actor, qt_b)

    def from_pi() -> None:
        _s, holder["pi_so"] = _so_from_pi(actor, pi, unique("lo-p"))

    seen = _first_lock_sequence(from_pi)
    # QT를 PI보다 먼저 — SO INSERT의 FK 검사가 QT 행에 암묵 KEY SHARE를 잡으므로(PI 취소의 QT→PI와 교착하지 않게)
    assert seen[:4] == ["idempotency_keys", "partners", "quotations", "proforma_invoices"], seen
    assert seen[-1] == "doc_number_seq", seen
    _assert_follows_lock_order(seen)

    qt_c = _issued_qt(actor)  # 취소 SO를 복제 원본으로 — 원본 SO(5)는 원천 라인(8)보다 먼저 잠긴다
    src = _so_from_qt(actor, qt_c, unique("lo-src"))[1]
    _cancel_so(actor, src, unique("lo-srcx"))
    qt_c = quotations.get_quotation(qt_c["id"])

    def copy() -> None:
        _so_from_qt(actor, qt_c, unique("lo-cp"), copied_from_id=src["id"])

    seen = _first_lock_sequence(copy)
    assert seen.index("quotations") < seen.index("sales_orders") < seen.index("lines"), seen
    assert seen[-1] == "doc_number_seq", seen
    _assert_follows_lock_order(seen)

    so = holder["qt_so"]

    def edit() -> None:
        sos.update_sales_order(
            actor=actor, so_id=so["id"], payload={"version": so["version"], "internal_note": "n"}
        )

    seen = _first_lock_sequence(edit)
    assert seen[0] == "sales_orders", seen
    _assert_follows_lock_order(seen)
    so = sos.get_sales_order(so["id"])

    def bump_quantity() -> None:
        sos.update_line(
            actor=actor,
            so_id=so["id"],
            line_id=so["lines"][0]["id"],
            payload={"version": so["version"], "quantity": 6},
        )

    seen = _first_lock_sequence(bump_quantity)
    assert seen == ["sales_orders", "lines"], seen  # SO 행 잠금 뒤 원천 라인 잠금
    _assert_follows_lock_order(seen)

    pi_so = holder["pi_so"]

    def cancel() -> None:
        _cancel_so(actor, pi_so, unique("lo-x"))

    seen = _first_lock_sequence(cancel)
    assert seen[:4] == ["idempotency_keys", "quotations", "proforma_invoices", "sales_orders"], seen
    _assert_follows_lock_order(seen)


def test_the_lock_order_probe_flags_a_reversed_order() -> None:
    """공회전 방지 — 계측이 잠금을 실제로 기록하고(SO 생성이 partners·quotations·lines·채번을 만진다), 뒤집힌 순서는 위반으로 판정한다"""
    actor = _actor()
    qt = _issued_qt(actor)
    seen = _first_lock_sequence(lambda: _so_from_qt(actor, qt, unique("probe")))
    assert {"partners", "quotations", "lines", "doc_number_seq", "idempotency_keys"} <= set(seen), (
        seen
    )
    with pytest.raises(AssertionError):
        _assert_follows_lock_order(["sales_orders", "proforma_invoices"])
    with pytest.raises(AssertionError):
        _assert_follows_lock_order(["lines", "sales_orders"])
