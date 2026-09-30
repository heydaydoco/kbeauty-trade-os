"""A. PI 상태 전이 — PI 20쌍 전수·입금 수렴 6방향(`converge_payment_status`)·통로 규칙·후속 보유 (S3-1 PR-6a / ADR-0051·0052 / design-B B1·B3).

PI 5상태의 순서쌍 20개: 허용 8(사람 1·자동 7)은 성공, 미허용 12는 409 전건(ADR-0038의 전수 선례). 입금 수렴은 호출자(payments)가
PR-10에서 배선되므로 **이 파일이 직접 호출**로 시험한다(죽은 문이 아니라 상태 기계 자동 6방향의 실제 진입점).
"""

from __future__ import annotations

from datetime import timedelta
from itertools import permutations
from typing import Any

import pytest
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.core.db.uow import unit_of_work
from app.core.errors.exceptions import AppError
from app.core.time import today_kst
from app.modules.identity.models import RoleCode
from app.modules.proforma_invoices.models import ProformaInvoice
from app.modules.trade_chain.payment_status import converge_payment_status, derive_pi_status
from app.modules.trade_docs.constants import DocKind
from app.modules.trade_docs.machine import AUTO_TRANSITIONS, HUMAN_TRANSITIONS, STATUSES
from app.modules.trade_docs.transition import record_transition
from tests.factories.trade import fake_successors, raw_pi, raw_quotation
from tests.support.factories import create_user

pytestmark = pytest.mark.group_a

KIND = DocKind.PROFORMA_INVOICE
STATES = STATUSES[KIND]
HUMAN = HUMAN_TRANSITIONS[KIND]
AUTO = AUTO_TRANSITIONS[KIND]


def _scalar(sql: str, **params: Any) -> Any:
    with owner_engine.connect() as connection:
        return connection.execute(text(sql), params).scalar_one()


def _log_rows(pi_id: int) -> list[tuple[Any, ...]]:
    with owner_engine.connect() as connection:
        return [
            tuple(r)
            for r in connection.execute(
                text(
                    "SELECT from_status, to_status, automatic, reason, actor_user_id FROM"
                    " proforma_invoice_status_log WHERE proforma_invoice_id = :p ORDER BY id"
                ),
                {"p": pi_id},
            )
        ]


def _events(pi_id: int) -> int:
    return int(
        _scalar(
            "SELECT count(*) FROM events WHERE aggregate_type = 'proforma_invoices'"
            " AND aggregate_id = :i AND event_type = 'proforma_invoices.proforma_invoice.status_changed'",
            i=pi_id,
        )
    )


def _transition(pi_id: int, to: str, **kwargs: Any) -> str:
    with unit_of_work() as uow:
        row = uow.session.get(ProformaInvoice, pi_id)
        assert row is not None
        return record_transition(uow.session, row, to, **kwargs)


_ALL_PAIRS = list(permutations(STATES, 2))


def _kwargs_for(pair: tuple[str, str], actor: int) -> dict[str, Any]:
    to = pair[1]
    auto = pair in AUTO
    return {
        "actor_user_id": None if auto and to == "EXPIRED" else actor,
        "reason": "테스트 사유" if to in ("CANCELLED", "EXPIRED") else None,
        "automatic": auto,
    }


@pytest.mark.parametrize("pair", _ALL_PAIRS, ids=[f"{a}->{b}" for a, b in _ALL_PAIRS])
def test_all_twenty_pi_pairs(pair: tuple[str, str]) -> None:
    """PI 20쌍 — 허용 8쌍은 성공(이력 1행·automatic 표식·이벤트 1건), 미허용 12쌍은 409 TRANSITION.NOT_ALLOWED이고 무변"""
    frm, to = pair
    actor = create_user(f"pi-actor-{frm}-{to}@example.com", roles=(RoleCode.TRADE,))
    pi_id = raw_pi(raw_quotation("ISSUED"), frm)
    before_log, before_events = len(_log_rows(pi_id)), _events(pi_id)
    if pair in HUMAN | AUTO:
        assert _transition(pi_id, to, **_kwargs_for(pair, actor)) == frm
        assert _scalar("SELECT status FROM proforma_invoices WHERE id = :i", i=pi_id) == to
        rows = _log_rows(pi_id)
        assert len(rows) == before_log + 1 and _events(pi_id) == before_events + 1
        assert rows[-1][:3] == (frm, to, pair in AUTO)
        if to in ("CANCELLED", "EXPIRED"):
            assert rows[-1][3] == "테스트 사유"
    else:
        with pytest.raises(AppError) as caught:
            _transition(pi_id, to, **_kwargs_for(pair, actor))
        assert caught.value.code == "TRADE_DOCS.TRANSITION.NOT_ALLOWED"
        assert caught.value.status_code == 409
        assert _scalar("SELECT status FROM proforma_invoices WHERE id = :i", i=pi_id) == frm
        assert len(_log_rows(pi_id)) == before_log and _events(pi_id) == before_events


def test_the_pair_table_itself_is_not_vacuous() -> None:
    """공회전 방지 — 20쌍 중 허용 8(사람 1·자동 7)·미허용 12가 실제로 갈린다"""
    assert len(_ALL_PAIRS) == 20
    assert len([p for p in _ALL_PAIRS if p in HUMAN | AUTO]) == 8
    assert (len(HUMAN), len(AUTO)) == (1, 7)


@pytest.mark.parametrize("pair", sorted(HUMAN | AUTO))
def test_wrong_channel_is_rejected_even_for_an_allowed_pair(pair: tuple[str, str]) -> None:
    """허용 쌍도 통로가 틀리면 거부 — 사람 쌍에 automatic=True·자동 쌍에 automatic=False(공개 API가 자동 엣지를 못 넘는 근거)"""
    frm, to = pair
    actor = create_user(f"pi-chan-{frm}-{to}@example.com", roles=(RoleCode.TRADE,))
    pi_id = raw_pi(raw_quotation("ISSUED"), frm)
    good = _kwargs_for(pair, actor)
    flipped = {**good, "automatic": not good["automatic"], "actor_user_id": actor}
    with pytest.raises(AppError) as caught:
        _transition(pi_id, to, **flipped)
    assert caught.value.code == "TRADE_DOCS.TRANSITION.NOT_ALLOWED"
    assert _scalar("SELECT status FROM proforma_invoices WHERE id = :i", i=pi_id) == frm


@pytest.mark.parametrize("to", ["CANCELLED", "EXPIRED"])
def test_cancel_and_expire_require_a_reason(to: str) -> None:
    """취소·만료 도달은 사유 필수 — 없으면 422 REASON_REQUIRED이고 상태 무변"""
    pi_id = raw_pi(raw_quotation("ISSUED"), "ISSUED")
    actor = create_user(f"pi-reason-{to}@example.com", roles=(RoleCode.TRADE,))
    auto = to == "EXPIRED"
    with pytest.raises(AppError) as caught:
        _transition(pi_id, to, actor_user_id=None if auto else actor, reason="  ", automatic=auto)
    assert caught.value.code == "TRADE_DOCS.TRANSITION.REASON_REQUIRED"
    assert _scalar("SELECT status FROM proforma_invoices WHERE id = :i", i=pi_id) == "ISSUED"


# ── derive_pi_status: 순수 함수 ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("received", "due", "expected"),
    [
        (0, 3000, "ISSUED"),
        (1, 3000, "PARTIALLY_PAID"),
        (2999, 3000, "PARTIALLY_PAID"),
        (3000, 3000, "PAID"),  # 등호 — 정확히 청구액이면 입금 완료
        (3001, 3000, "PAID"),
        (0, 0, "ISSUED"),
        (
            5,
            0,
            "PARTIALLY_PAID",
        ),  # due=0은 호출 계약상 no-op이라 수렴 함수가 먼저 걸러낸다(순수 함수 자체의 값)
    ],
)
def test_derive_pi_status_boundaries(received: int, due: int, expected: str) -> None:
    """0→ISSUED · 0<x<due→PARTIALLY_PAID · x≥due>0→PAID — 경계(1·due−1·정확히 due·초과)"""
    assert derive_pi_status(received, due) == expected


@pytest.mark.parametrize("args", [(-1, 100), (0, -1)])
def test_derive_pi_status_rejects_negative_inputs(args: tuple[int, int]) -> None:
    """음수 누적·음수 청구액은 호출 계약 위반(ValueError) — 조용히 상태를 정하지 않는다"""
    with pytest.raises(ValueError, match="0 이상"):
        derive_pi_status(*args)


# ── converge_payment_status: 6방향 왕복·경계·fail-closed ─────────────────────────


def _converge(pi_id: int, received: int, due: int = 3000, **kwargs: Any) -> str | None:
    with unit_of_work() as uow:
        return converge_payment_status(
            uow.session,
            pi_id,
            received_total_amount=received,
            due_amount=due,
            actor_user_id=kwargs.pop("actor_user_id", None),
            **kwargs,
        )


def test_payment_convergence_walks_all_six_directions() -> None:
    """ISSUED→PARTIALLY_PAID→PAID→PARTIALLY_PAID→ISSUED→PAID→ISSUED — 6방향 전부, 매번 이력 1행(automatic)·이벤트 1건, 같은 값 재수렴은 변화 0"""
    actor = create_user("pay-actor@example.com", roles=(RoleCode.TRADE,))
    pi_id = raw_pi(raw_quotation("ISSUED"), "ISSUED")
    steps = [
        (1000, "PARTIALLY_PAID"),  # ISSUED → PARTIALLY_PAID
        (3000, "PAID"),  # PARTIALLY_PAID → PAID
        (1500, "PARTIALLY_PAID"),  # PAID → PARTIALLY_PAID (역기록 후 부분)
        (0, "ISSUED"),  # PARTIALLY_PAID → ISSUED (전액 역기록)
        (3200, "PAID"),  # ISSUED → PAID (일시 완납)
        (0, "ISSUED"),  # PAID → ISSUED
    ]
    seen: set[tuple[str, str]] = set()
    for received, expected in steps:
        before = _scalar("SELECT status FROM proforma_invoices WHERE id = :i", i=pi_id)
        assert _converge(pi_id, received, actor_user_id=actor) == expected
        seen.add((before, expected))
        assert _converge(pi_id, received, actor_user_id=actor) is None  # 멱등 — 재수렴 변화 0
    assert len(seen) == 6  # 6방향 전부 실행됐다
    rows = _log_rows(pi_id)
    assert len(rows) == 1 + 6  # 탄생 + 6전이(재수렴은 이력을 쓰지 않는다)
    assert all(
        r[2] is True and r[4] == actor for r in rows[1:]
    )  # 자동이지만 행위자(입금 기록자)가 남는다
    assert _events(pi_id) == 6


def test_a_non_advance_pi_is_a_noop() -> None:
    """청구액(due) 0 = 선수금 T/T가 아닌 PI — 수렴 대상이 아니라 상태·이력 무변(None)"""
    pi_id = raw_pi(raw_quotation("ISSUED"), "ISSUED")
    assert _converge(pi_id, 500, due=0) is None
    assert _scalar("SELECT status FROM proforma_invoices WHERE id = :i", i=pi_id) == "ISSUED"
    assert len(_log_rows(pi_id)) == 1


@pytest.mark.parametrize("dead", ["CANCELLED", "EXPIRED"])
def test_convergence_on_a_dead_pi_is_refused_fail_closed(dead: str) -> None:
    """취소·만료된 PI에 입금 수렴 → 409 PAYMENT.PI_NOT_OPEN(엣지 없음 — 새 PI를 발행해야 한다) · due=0이어도 먼저 거부"""
    pi_id = raw_pi(raw_quotation("ISSUED"), dead)
    for due in (3000, 0):
        with pytest.raises(AppError) as caught:
            _converge(pi_id, 1000, due=due)
        assert (
            caught.value.code == "TRADE_DOCS.PAYMENT.PI_NOT_OPEN"
            and caught.value.status_code == 409
        )
    assert _scalar("SELECT status FROM proforma_invoices WHERE id = :i", i=pi_id) == dead
    assert len(_log_rows(pi_id)) == 1


def test_reversal_back_to_issued_after_the_validity_lapsed_expires_in_the_same_transaction() -> (
    None
):
    """유효기간이 지난 PI의 입금이 전액 역기록돼 ISSUED로 복귀하면 같은 트랜잭션에서 EXPIRED로 추가 수렴(이력 2행: 복귀+만료)"""
    day = today_kst() - timedelta(days=3)
    qt = raw_quotation("ISSUED", valid_until=day, doc_date=day)
    pi_id = raw_pi(qt, "PAID", valid_until=day, doc_date=day)
    assert _converge(pi_id, 0, today=today_kst()) == "EXPIRED"
    rows = _log_rows(pi_id)
    assert [(r[0], r[1], r[2]) for r in rows[1:]] == [
        ("PAID", "ISSUED", True),
        ("ISSUED", "EXPIRED", True),
    ]
    assert "유효기간 경과" in rows[-1][3]
    assert _scalar("SELECT status FROM proforma_invoices WHERE id = :i", i=pi_id) == "EXPIRED"
    # 부모 QT도 같은 트랜잭션에서 수렴된다(유효기간이 지났고 붙잡는 후속이 없어졌다)
    assert _scalar("SELECT status FROM quotations WHERE id = :i", i=qt) == "EXPIRED"


def test_a_lapsed_pi_with_payments_is_not_expired_only_when_the_payment_disappears() -> None:
    """일부입금·입금완료 PI는 유효기간이 지나도 만료되지 않고(선수금이 갇히지 않게), 입금이 사라져 ISSUED가 될 때만 만료 대상이 된다"""
    day = today_kst() - timedelta(days=3)
    qt = raw_quotation("ISSUED", valid_until=day, doc_date=day)
    pi_id = raw_pi(qt, "ISSUED", valid_until=day, doc_date=day)
    assert _converge(pi_id, 1000) == "PARTIALLY_PAID"  # 유효기간 경과에도 입금 수렴은 계속된다
    assert _converge(pi_id, 3000) == "PAID"
    assert _scalar("SELECT status FROM proforma_invoices WHERE id = :i", i=pi_id) == "PAID"
    assert _converge(pi_id, 1000) == "PARTIALLY_PAID"
    assert _converge(pi_id, 0) == "EXPIRED"


def test_a_live_successor_keeps_the_pi_open_after_the_payment_reversal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """살아 있는 후속(SO 대역)이 PI를 붙잡고 있으면 복귀 후에도 EXPIRED로 닫지 않는다 — 입금이 계속 들어와야 하므로"""
    day = today_kst() - timedelta(days=3)
    qt = raw_quotation("ISSUED", valid_until=day, doc_date=day)
    pi_id = raw_pi(qt, "PAID", valid_until=day, doc_date=day)
    with fake_successors(monkeypatch, fk_column="pi_id", parent=KIND) as successors:
        successors.add(pi_id, status="CONFIRMED", confirmed=True)
        assert _converge(pi_id, 0) == "ISSUED"
    assert _scalar("SELECT status FROM proforma_invoices WHERE id = :i", i=pi_id) == "ISSUED"
    assert [(r[0], r[1]) for r in _log_rows(pi_id)[1:]] == [("PAID", "ISSUED")]


def test_a_pi_with_a_live_successor_cannot_be_cancelled_until_it_dies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """역순 취소 — 살아 있는 후속(SO 대역)이 있는 PI 취소는 409 SUCCESSOR_ALIVE(detail=후속 번호) · 후속이 취소·만료되면 성공 · PR-7이 실 SO 테이블로 대체할 자리"""
    actor = create_user("pi-cancel-chain@example.com", roles=(RoleCode.TRADE,))
    pi_id = raw_pi(raw_quotation("ISSUED"), "ISSUED")
    with fake_successors(monkeypatch, fk_column="pi_id", parent=KIND) as successors:
        so = successors.add(pi_id, status="RECEIVED", number="SO-2026-9001")
        with pytest.raises(AppError) as caught:
            _transition(
                pi_id, "CANCELLED", actor_user_id=actor, reason="취소 시도", automatic=False
            )
        assert caught.value.code == "TRADE_DOCS.CANCEL.SUCCESSOR_ALIVE"
        assert caught.value.detail == {"successors": ["SO-2026-9001"]}
        assert _scalar("SELECT status FROM proforma_invoices WHERE id = :i", i=pi_id) == "ISSUED"
        successors.set_status(so, "EXPIRED")  # 죽은 후속은 취소를 막지 않는다
        _transition(
            pi_id, "CANCELLED", actor_user_id=actor, reason="후속 만료 뒤 취소", automatic=False
        )
    assert _scalar("SELECT status FROM proforma_invoices WHERE id = :i", i=pi_id) == "CANCELLED"
