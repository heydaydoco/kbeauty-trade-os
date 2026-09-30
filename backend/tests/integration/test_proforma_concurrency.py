"""J. PI 동시성 — 잔량 초과 동시 생성=1건·더블클릭·취소 vs 후속 생성·스윕 vs 입금·잠금 순서 (S3-1 PR-6a / ADR-0052·0059 / design-A A4·A13 / design-B B3·B7).

★ 순차 실행은 증거가 아니다 — 실제 스레드를 Barrier로 동시에 출발시킨다(tests/support/concurrency.py). 각 스레드는 자기 세션을 연다.
  이 파일의 테스트는 커밋을 동반하므로 병렬(pytest-xdist)로 돌리지 않는다. 결과 불변식(초과 소비 0·고아 후속 0·이중 전이 0)을 본다.
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
from app.modules.proforma_invoices import service as pi_service
from app.modules.quotations import service as quotations
from app.modules.trade_chain import lifecycle, reference
from app.modules.trade_chain.expiry_sweep import sweep_expired_documents
from app.modules.trade_chain.payment_status import converge_payment_status
from app.modules.trade_docs.locking import LOCK_ORDER
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
    address = f"{unique('pi-conc')}@example.com"
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
    actor: AuthenticatedUser, *, quantity: int = 10, valid_days: int = 30
) -> dict[str, Any]:
    """발행된 견적 1건(라인 1개·수량 quantity) — 서비스 계층으로 만든다."""
    create_market("US")
    _s, draft = quotations.create_quotation(
        actor=actor,
        idempotency_key=unique("qt"),
        payload={
            "buyer_partner_id": create_buyer(),
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


def _payload(qt: dict[str, Any], bank: int, **extra: Any) -> dict[str, Any]:
    return {
        "version": qt["version"],
        "valid_until": today_kst() + timedelta(days=30),
        "bank_account_id": bank,
        **extra,
    }


def _create(actor: AuthenticatedUser, qt: dict[str, Any], bank: int, key: str, **extra: Any) -> Any:
    return reference.create_proforma_invoice(
        actor=actor, idempotency_key=key, qt_id=qt["id"], payload=_payload(qt, bank, **extra)
    )


def _only_conflicts(outcomes: list[Any]) -> None:
    """실패는 전부 정의된 409(AppError)여야 한다 — 데드락·IntegrityError·500이 새면 실패"""
    for outcome in outcomes:
        if not outcome.ok:
            assert isinstance(outcome.error, AppError), repr(outcome.error)
            assert outcome.error.status_code == 409, outcome.error


def test_two_concurrent_full_quantity_creations_admit_exactly_one() -> None:
    """같은 QT에서 잔량 전부를 요구하는 PI 생성 2건 동시(다른 키) → 정확히 1건 성공, 나머지는 409 EXCEEDS_OPEN — 초과 소비 0"""
    actor = _actor()
    qt = _issued_qt(actor, quantity=10)
    bank = create_bank_account("USD")

    def worker(i: int) -> Any:
        return _create(actor, qt, bank, f"full-{i}")

    outcomes = run_concurrently(worker, workers=2)
    wins = [o for o in outcomes if o.ok]
    losses = [o for o in outcomes if not o.ok]
    assert len(wins) == 1 and len(losses) == 1
    assert isinstance(losses[0].error, AppError)
    assert losses[0].error.code == "TRADE_DOCS.QUANTITY.EXCEEDS_OPEN"
    assert _scalar("SELECT count(*) FROM proforma_invoices") == 1
    assert (
        _scalar("SELECT coalesce(sum(quantity), 0) FROM proforma_invoice_lines") == 10
    )  # 주문량 이하
    assert (
        _scalar("SELECT count(*) FROM proforma_invoice_status_log") == 1
    )  # 실패한 쪽은 흔적이 없다
    assert (
        _scalar(
            "SELECT count(*) FROM events WHERE event_type = 'proforma_invoices.proforma_invoice.created'"
        )
        == 1
    )


@pytest.mark.parametrize("round_no", range(3))
def test_many_small_creations_never_oversubscribe_the_source_line(round_no: int) -> None:
    """수량 10에 2씩 6스레드 동시 → 정확히 5건 성공·1건 EXPIRED 아닌 EXCEEDS_OPEN·소비 합 = 10(잔량 정확히 0), 번호 결번 0"""
    actor = _actor()
    qt = _issued_qt(actor, quantity=10)
    bank = create_bank_account("USD")
    line_id = qt["lines"][0]["id"]

    def worker(i: int) -> Any:
        return _create(
            actor,
            qt,
            bank,
            f"small-{round_no}-{i}",
            lines=[{"source_line_id": line_id, "quantity": 2}],
        )

    outcomes = run_concurrently(worker, workers=6)
    _only_conflicts(outcomes)
    assert sum(1 for o in outcomes if o.ok) == 5
    assert [o.error.code for o in outcomes if not o.ok] == ["TRADE_DOCS.QUANTITY.EXCEEDS_OPEN"]  # type: ignore[union-attr]
    assert _scalar("SELECT sum(quantity) FROM proforma_invoice_lines") == 10
    with owner_engine.connect() as connection:
        numbers = sorted(
            int(n.rsplit("-", 1)[1])
            for (n,) in connection.execute(text("SELECT doc_number FROM proforma_invoices"))
        )
    assert numbers == [1, 2, 3, 4, 5]  # 실패한 시도가 번호를 소비하지 않았다(결번 0)


def test_double_click_with_the_same_key_creates_one_pi() -> None:
    """생성 더블클릭(같은 키 동시 2요청) → 둘 다 같은 결과·PI 1건·이력 1행·이벤트 1건(멱등 claim 직렬화)"""
    actor = _actor()
    qt = _issued_qt(actor)
    bank = create_bank_account("USD")

    def worker(_i: int) -> Any:
        return _create(actor, qt, bank, "dbl-create")[1]

    outcomes = run_concurrently(worker, workers=2)
    assert all(o.ok for o in outcomes), [o.error for o in outcomes]
    assert outcomes[0].value == outcomes[1].value
    assert _scalar("SELECT count(*) FROM proforma_invoices") == 1
    assert _scalar("SELECT count(*) FROM proforma_invoice_status_log") == 1


def test_creations_from_different_quotations_do_not_block_each_other() -> None:
    """서로 다른 QT의 PI 생성 8건 동시 → 전부 성공(직렬화는 같은 원천 QT 안에서만) — 번호 8개 연속·중복 0"""
    actor = _actor()
    bank = create_bank_account("USD")
    quotes = [_issued_qt(actor) for _ in range(8)]

    def worker(i: int) -> Any:
        return _create(actor, quotes[i], bank, f"diff-qt-{i}")[1]["doc_number"]

    outcomes = run_concurrently(worker, workers=8)
    assert all(o.ok for o in outcomes), [o.error for o in outcomes]
    numbers = sorted(int(o.value.rsplit("-", 1)[1]) for o in outcomes)
    assert numbers == list(range(1, 9))


@pytest.mark.parametrize("round_no", range(8))
def test_quotation_cancel_versus_pi_creation_never_orphans_a_live_pi(round_no: int) -> None:
    """QT 취소 vs 그 QT의 PI 생성 동시(20회 계열) — 정확히 한쪽 결과로 수렴: 취소된 QT 아래 살아 있는 PI 0건, 실패는 정의된 409뿐(데드락 0)"""
    actor = _actor()
    qt = _issued_qt(actor)
    bank = create_bank_account("USD")

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
        return _create(actor, qt, bank, f"pc-{round_no}")

    outcomes = run_concurrently(worker, workers=2)
    _only_conflicts(outcomes)
    assert any(o.ok for o in outcomes)
    status = _scalar("SELECT status FROM quotations WHERE id = :i", i=qt["id"])
    live = _scalar(
        "SELECT count(*) FROM proforma_invoices WHERE qt_id = :i AND status NOT IN ('CANCELLED', 'EXPIRED')",
        i=qt["id"],
    )
    if status == "CANCELLED":
        assert live == 0, "취소된 QT 아래 살아 있는 PI(고아 후속)"
        assert not outcomes[1].ok  # 취소가 이겼으면 생성은 거부됐다
    else:
        assert live == 1 and not outcomes[0].ok  # 생성이 이겼으면 QT 취소는 후속 생존으로 409


@pytest.mark.parametrize("round_no", range(6))
def test_pi_cancel_versus_quotation_cancel_is_deadlock_free_and_ordered(round_no: int) -> None:
    """PI 취소 vs 그 QT 취소 동시 — 데드락 없이 정의된 409만, 결과는 (PI 취소 후 QT 취소 성공) 또는 (QT 취소 409·PI만 취소) 둘 중 하나"""
    actor = _actor()
    qt = _issued_qt(actor)
    bank = create_bank_account("USD")
    _s, pi = _create(actor, qt, bank, f"mk-{round_no}")

    def worker(i: int) -> Any:
        if i == 0:
            return lifecycle.transition_proforma_invoice(
                actor=actor,
                idempotency_key=f"pcx-{round_no}",
                pi_id=pi["id"],
                to="CANCELLED",
                version=pi["version"],
                reason="동시 PI 취소",
            )
        return lifecycle.transition_quotation(
            actor=actor,
            idempotency_key=f"qcx-{round_no}",
            qt_id=qt["id"],
            to="CANCELLED",
            version=qt["version"],
            reason="동시 QT 취소",
        )

    outcomes = run_concurrently(worker, workers=2)
    _only_conflicts(outcomes)
    assert outcomes[0].ok  # PI 취소는 항상 성공(QT 취소는 PI가 살아 있는 동안 못 이긴다)
    assert _scalar("SELECT status FROM proforma_invoices WHERE id = :i", i=pi["id"]) == "CANCELLED"
    qt_status = _scalar("SELECT status FROM quotations WHERE id = :i", i=qt["id"])
    assert qt_status == ("CANCELLED" if outcomes[1].ok else "ISSUED")
    assert (
        _scalar(
            "SELECT count(*) FROM proforma_invoice_status_log WHERE proforma_invoice_id = :i"
            " AND to_status = 'CANCELLED'",
            i=pi["id"],
        )
        == 1
    )


@pytest.mark.parametrize("round_no", range(6))
def test_expiry_sweep_versus_pi_creation_never_leaves_an_expired_parent_with_a_live_pi(
    round_no: int,
) -> None:
    """만료 스윕(기준일=내일, 오늘까지 유효한 QT) vs PI 생성 동시 — 만료된 QT 아래 살아 있는 PI 0건(후속이 부모를 붙잡거나 QT가 먼저 닫혀 생성이 409)"""
    actor = _actor()
    qt = _issued_qt(actor, valid_days=0)  # valid_until = 오늘
    bank = create_bank_account("USD")

    def worker(i: int) -> Any:
        if i == 0:
            return sweep_expired_documents(base_date=today_kst() + timedelta(days=1))
        return _create(actor, qt, bank, f"sw-{round_no}")

    outcomes = run_concurrently(worker, workers=2)
    assert outcomes[0].ok, outcomes[0].error  # 스윕은 잡을 깨뜨리지 않는다
    if not outcomes[1].ok:
        assert isinstance(outcomes[1].error, AppError) and outcomes[1].error.status_code == 409
    status = _scalar("SELECT status FROM quotations WHERE id = :i", i=qt["id"])
    live = _scalar(
        "SELECT count(*) FROM proforma_invoices WHERE qt_id = :i AND status NOT IN ('CANCELLED', 'EXPIRED')",
        i=qt["id"],
    )
    assert not (status == "EXPIRED" and live > 0), "만료된 QT 아래 살아 있는 PI"
    assert status in ("EXPIRED", "ISSUED") and (status == "EXPIRED") == (not outcomes[1].ok)
    assert outcomes[0].value["failed"] == 0


@pytest.mark.parametrize("round_no", range(6))
def test_expiry_sweep_versus_payment_convergence_yields_one_consistent_outcome(
    round_no: int,
) -> None:
    """만료 스윕 vs 입금 수렴 동시(유효기간이 지난 미입금 PI) — 결과는 (EXPIRED + 입금 거부 409) 또는 (PARTIALLY_PAID + 스윕 skipped) 중 하나, 이중 전이·이력 오염 0"""
    actor = _actor()
    qt = _issued_qt(actor)
    bank = create_bank_account("USD")
    _s, pi = _create(actor, qt, bank, f"vs-{round_no}")
    yesterday = today_kst() - timedelta(days=1)
    with owner_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE proforma_invoices SET valid_until = :d, doc_date = :d, fx_rate_date = :d WHERE id = :i"
            ),
            {"d": yesterday, "i": pi["id"]},
        )

    def worker(i: int) -> Any:
        if i == 0:
            return sweep_expired_documents()
        with unit_of_work() as uow:
            return converge_payment_status(
                uow.session,
                pi["id"],
                received_total_amount=1000,
                due_amount=3000,
                actor_user_id=actor.id,
            )

    outcomes = run_concurrently(worker, workers=2)
    assert outcomes[0].ok, outcomes[0].error
    final = _scalar("SELECT status FROM proforma_invoices WHERE id = :i", i=pi["id"])
    rows = _scalar(
        "SELECT count(*) FROM proforma_invoice_status_log WHERE proforma_invoice_id = :i AND from_status IS NOT NULL",
        i=pi["id"],
    )
    if final == "EXPIRED":
        assert not outcomes[1].ok and isinstance(outcomes[1].error, AppError)
        assert outcomes[1].error.code == "TRADE_DOCS.PAYMENT.PI_NOT_OPEN"
        assert outcomes[0].value["expired_pi"] == 1
    else:
        assert (
            final == "PARTIALLY_PAID" and outcomes[1].ok and outcomes[1].value == "PARTIALLY_PAID"
        )
        assert outcomes[0].value["expired_pi"] == 0
    assert rows == 1  # 전이는 정확히 한 번


def test_a_held_quotation_lock_surfaces_as_lock_not_available_not_a_hang() -> None:
    """다른 트랜잭션이 QT 행을 잠근 상태에서 PI를 만들면 무한 대기가 아니라 55P03(잠금 대기 초과)이 난다 — API는 409 LOCK_BUSY로 번역"""
    actor = _actor()
    qt = _issued_qt(actor)
    bank = create_bank_account("USD")
    holder = owner_engine.connect()
    tx = holder.begin()
    try:
        holder.execute(text("SELECT 1 FROM quotations WHERE id = :i FOR UPDATE"), {"i": qt["id"]})
        with pytest.raises(DBAPIError) as caught, unit_of_work() as uow:
            uow.session.execute(text("SET LOCAL lock_timeout = '150ms'"))
            from app.modules.trade_chain.reference import _plan

            _plan(uow.session, actor, qt["id"], _payload(qt, bank), lock=True)
        assert getattr(caught.value.orig, "sqlstate", None) == "55P03"
    finally:
        tx.rollback()
        holder.close()
    assert _scalar("SELECT count(*) FROM proforma_invoices") == 0


def test_preview_does_not_wait_for_the_quotation_lock() -> None:
    """미리보기는 원천 QT를 잠그지 않는다 — 다른 트랜잭션이 QT 행을 잡고 있어도 즉시 응답한다(읽기 전용)"""
    actor = _actor()
    qt = _issued_qt(actor)
    bank = create_bank_account("USD")
    holder = owner_engine.connect()
    tx = holder.begin()
    try:
        holder.execute(text("SELECT 1 FROM quotations WHERE id = :i FOR UPDATE"), {"i": qt["id"]})
        body = reference.preview_proforma_invoice(
            actor=actor, qt_id=qt["id"], payload=_payload(qt, bank)
        )
        assert body["total_amount"] == 1000
    finally:
        tx.rollback()
        holder.close()


# ── 잠금 순서(LOCK_ORDER) 계측 ─────────────────────────────────────────────────

_LOCK_TABLES = {
    "idempotency_keys": "idempotency_keys",
    "partners": "partners",
    "quotations": "quotations",
    "proforma_invoices": "proforma_invoices",
    "quotation_lines": "lines",
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


def test_the_lock_order_probe_flags_a_reversed_order() -> None:
    """공회전 방지 — 계측이 잠금을 실제로 기록하고(PI 생성이 partners·quotations·lines·채번을 만진다), 뒤집힌 순서는 위반으로 판정한다"""
    actor = _actor()
    qt = _issued_qt(actor)
    bank = create_bank_account("USD")
    seen = _first_lock_sequence(lambda: _create(actor, qt, bank, unique("probe")))
    assert {"partners", "quotations", "lines", "doc_number_seq", "idempotency_keys"} <= set(seen), (
        seen
    )
    with pytest.raises(AssertionError):
        _assert_follows_lock_order(["quotations", "partners"])
    with pytest.raises(AssertionError):
        _assert_follows_lock_order(["proforma_invoices", "quotations"])


def test_every_pi_operation_takes_locks_in_the_documented_order() -> None:
    """생성(멱등→바이어→QT→라인→채번)·복제 생성(PI 원본은 QT 뒤)·취소(QT→PI)·입금 수렴(QT→PI)이 LOCK_ORDER를 지킨다"""
    actor = _actor()
    qt = _issued_qt(actor)
    bank = create_bank_account("USD")
    holder: dict[str, Any] = {}

    def create() -> None:
        _s, holder["pi"] = _create(actor, qt, bank, unique("lo-c"))

    seen = _first_lock_sequence(create)
    assert seen[:3] == ["idempotency_keys", "partners", "quotations"], seen
    assert seen[-1] == "doc_number_seq", seen  # 채번은 항상 마지막
    _assert_follows_lock_order(seen)
    pi = holder["pi"]

    def pay() -> None:
        with unit_of_work() as uow:
            converge_payment_status(
                uow.session,
                pi["id"],
                received_total_amount=500,
                due_amount=3000,
                actor_user_id=actor.id,
            )

    seen = _first_lock_sequence(pay)
    assert seen[:2] == ["quotations", "proforma_invoices"], seen  # 사슬 상위(QT)에서 하위(PI)로
    _assert_follows_lock_order(seen)
    with unit_of_work() as uow:  # 입금을 되돌려 다시 ISSUED로(취소 가능 상태)
        converge_payment_status(
            uow.session, pi["id"], received_total_amount=0, due_amount=3000, actor_user_id=actor.id
        )
    pi = pi_service.get_proforma_invoice(pi["id"])

    def cancel() -> None:
        lifecycle.transition_proforma_invoice(
            actor=actor,
            idempotency_key=unique("lo-x"),
            pi_id=pi["id"],
            to="CANCELLED",
            version=pi["version"],
            reason="잠금 순서 확인",
        )

    seen = _first_lock_sequence(cancel)
    assert seen[:3] == ["idempotency_keys", "quotations", "proforma_invoices"], seen
    _assert_follows_lock_order(seen)
    qt_now = quotations.get_quotation(qt["id"])

    def copy() -> None:
        reference.create_proforma_invoice(
            actor=actor,
            idempotency_key=unique("lo-cp"),
            qt_id=qt["id"],
            payload=_payload(qt_now, bank, copied_from_id=pi["id"]),
        )

    seen = _first_lock_sequence(copy)  # 복제 원본 PI는 QT 뒤에 잠근다
    assert seen.index("quotations") < seen.index("proforma_invoices") < seen.index("lines"), seen
    _assert_follows_lock_order(seen)


@pytest.mark.parametrize("round_no", range(4))
def test_bank_correction_versus_pi_creation_copies_one_committed_snapshot(round_no: int) -> None:
    """은행 계좌 정정(은행명+계좌번호 동시 변경) vs PI 생성 동시 — PI의 은행 스냅샷은 정정 전 값 전체 또는 정정 후 값 전체(뒤섞임 0),
    비활성 vs 생성은 (생성 성공 후 비활성) 또는 (비활성 후 생성 422) 중 하나 — 계좌 행 FOR SHARE가 직렬화한다"""
    from app.modules.bank_accounts import service as bank_service

    actor = _actor()
    qt = _issued_qt(actor)
    bank = create_bank_account("USD", account_no="OLD-ACCT-1")
    with unit_of_work() as uow:  # 원본 은행명 확인용 버전
        version = int(
            uow.session.execute(
                text("SELECT version FROM bank_accounts WHERE id = :i"), {"i": bank}
            ).scalar_one()
        )

    def worker(i: int) -> Any:
        if i == 0:
            return bank_service.update_bank_account(
                actor=actor,
                account_id=bank,
                payload={"version": version, "bank_name": "New Bank", "account_no": "NEW-ACCT-2"},
            )
        return _create(actor, qt, bank, f"bk-{round_no}")

    outcomes = run_concurrently(worker, workers=2)
    assert all(o.ok for o in outcomes), [o.error for o in outcomes]
    snap = owner_engine.connect()
    try:
        name, number = snap.execute(
            text("SELECT bank_name, bank_account_no FROM proforma_invoices")
        ).one()
    finally:
        snap.close()
    assert (name, number) in {("Synthetic Bank", "OLD-ACCT-1"), ("New Bank", "NEW-ACCT-2")}, (
        name,
        number,
    )


@pytest.mark.parametrize("round_no", range(4))
def test_bank_deactivation_versus_pi_creation_is_serialized(round_no: int) -> None:
    """비활성 vs PI 생성 동시 — 생성이 이기면 PI가 남고 계좌는 비활성, 비활성이 이기면 생성은 422(사용할 수 없는 계좌) — 반쪽 상태 0"""
    from app.modules.bank_accounts import service as bank_service

    actor = _actor()
    qt = _issued_qt(actor)
    bank = create_bank_account("USD")

    def worker(i: int) -> Any:
        if i == 0:
            return bank_service.deactivate_bank_account(actor=actor, account_id=bank, version=1)
        return _create(actor, qt, bank, f"bd-{round_no}")

    outcomes = run_concurrently(worker, workers=2)
    assert outcomes[0].ok, outcomes[0].error
    pis = _scalar("SELECT count(*) FROM proforma_invoices")
    if outcomes[1].ok:
        assert pis == 1
    else:
        assert isinstance(outcomes[1].error, AppError) and outcomes[1].error.status_code == 422
        assert pis == 0
    assert _scalar("SELECT deleted_at IS NOT NULL FROM bank_accounts WHERE id = :i", i=bank) is True
