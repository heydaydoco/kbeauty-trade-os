"""A. 입금 원장 서비스 전수 — 왕복·통화·초과 due ±1·KST 경계·자릿수·PI 상태·결제유형·역기록 규칙·확정 SO 경고·partner 일치 (S3-1 PR-10a / ADR-0068 / design-E E7).

API(`POST /proforma-invoices/{id}/payments`·`POST /payments/{id}/reversal`·`GET …/payments`)를 실제로 호출한다 — 라우터·멱등·오케스트레이터(`trade_chain/payment_flow`)·
원장 함수(`payments/service`)·PI 상태 수렴이 한 경로로 시험된다. 기본 PI는 USD·선수금 T/T 30%·총액 1000.00 → due 300.00.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.modules.identity.models import RoleCode
from tests.factories.payments import (
    DEFAULT_DUE,
    advance_pi,
    ledger,
    pay,
    pi_status,
    receipt_body,
    reverse,
    scalar,
    set_payment_terms,
    status_log,
)
from tests.factories.trade import idem, logged_in, raw_so

pytestmark = pytest.mark.group_a


def _code(response: Any) -> str:
    return str(response.json()["error"]["code"])


def test_full_round_trip_walks_the_pi_status_forward_and_back_with_automatic_history() -> None:
    """ISSUED→PARTIALLY_PAID→PAID→PARTIALLY_PAID→ISSUED 왕복 — 상태 이력은 automatic·사유, 순입금·요약이 매 단계 일치, 원 입금 행은 끝까지 불변"""
    pi = advance_pi()
    with logged_in(RoleCode.TRADE) as client:
        first = pay(client, pi, "100.00")
        assert first.status_code == 201, first.text
        assert pi_status(pi) == "PARTIALLY_PAID"
        assert first.json()["summary"]["net_received_amount"] == 10_000
        assert first.json()["summary"]["due_amount"] == DEFAULT_DUE
        assert first.json()["summary"]["remaining_text"] == "200.00"
        assert first.json()["warnings"] == []
        second = pay(client, pi, "200.00")
        assert second.status_code == 201, second.text
        assert pi_status(pi) == "PAID"
        originals = ledger(pi)
        assert [r["kind"] for r in originals] == ["RECEIPT", "RECEIPT"]

        back = reverse(client, second.json()["payment"]["id"])
        assert back.status_code == 201, back.text
        assert back.json()["summary"]["net_received_amount"] == 10_000
        assert pi_status(pi) == "PARTIALLY_PAID"
        back2 = reverse(client, first.json()["payment"]["id"])
        assert back2.status_code == 201, back2.text
        assert pi_status(pi) == "ISSUED"
        assert back2.json()["summary"]["net_received_amount"] == 0

    rows = ledger(pi)
    assert [r["kind"] for r in rows] == ["RECEIPT", "RECEIPT", "REVERSAL", "REVERSAL"]
    assert [r["received_amount"] for r in rows] == [10_000, 20_000, -20_000, -10_000]
    assert (
        scalar("SELECT COALESCE(SUM(received_amount), 0) FROM payments WHERE pi_id = :p", p=pi) == 0
    )
    for before, after in zip(originals, rows[:2], strict=True):  # 원본은 한 글자도 바뀌지 않았다
        assert before == after
    log = [row for row in status_log(pi) if row[0] is not None]
    assert [(a, b) for a, b, _auto, _r in log] == [
        ("ISSUED", "PARTIALLY_PAID"),
        ("PARTIALLY_PAID", "PAID"),
        ("PAID", "PARTIALLY_PAID"),
        ("PARTIALLY_PAID", "ISSUED"),
    ]
    assert all(auto is True for _a, _b, auto, _r in log)
    # 상태 이력 사유 — 입금은 "입금 #id 기록", 역기록은 "입금 #원입금id 역기록"(어느 입금이 상태를 바꿨는지 이력만으로 추적)
    p1, p2 = originals[0]["id"], originals[1]["id"]
    assert [r for _a, _b, _auto, r in log] == [
        f"입금 #{p1} 기록",
        f"입금 #{p2} 기록",
        f"입금 #{p2} 역기록",
        f"입금 #{p1} 역기록",
    ]


@pytest.mark.golden
def test_a_reversal_row_is_the_exact_negative_of_its_original_on_the_same_pi_currency_and_partner() -> (
    None
):
    """GC-A12 — 역기록 = −전액·같은 PI·같은 통화·같은 partner(PI의 바이어)·reverses_payment_id=원 입금·사유 보존, 기록자 = 행위자"""
    pi = advance_pi()
    buyer = scalar("SELECT buyer_partner_id FROM proforma_invoices WHERE id = :i", i=pi)
    with logged_in(RoleCode.TRADE) as client:
        paid = pay(client, pi, "123.45")
        back = reverse(client, paid.json()["payment"]["id"], "금액 오기입 정정")
    original, reversal = ledger(pi)
    assert original["partner_id"] == buyer == reversal["partner_id"]
    assert original["recorded_by_id"] > 0 and reversal["recorded_by_id"] > 0
    assert reversal["received_amount"] == -original["received_amount"] == -12_345
    assert reversal["received_currency"] == original["received_currency"] == "USD"
    assert reversal["reverses_payment_id"] == original["id"]
    assert reversal["reason"] == "금액 오기입 정정"
    assert back.json()["payment"]["received_amount_text"] == "-123.45"
    listed = None
    with logged_in(RoleCode.VIEWER) as viewer:
        listed = viewer.get(f"/api/v1/proforma-invoices/{pi}/payments").json()
    assert listed["items"][0]["reversed_by_payment_id"] == reversal["id"]
    assert listed["items"][1]["reversed_by_payment_id"] is None


def test_currency_mismatch_is_422_and_writes_nothing() -> None:
    """PI 통화(USD)와 다른 통화 입금(KRW·EUR) → 422 CURRENCY_MISMATCH — 환산 없음·행 0·상태 불변"""
    pi = advance_pi()
    with logged_in(RoleCode.TRADE) as client:
        for currency in ("KRW", "EUR"):
            response = pay(client, pi, "100.00", currency=currency)
            assert response.status_code == 422
            assert _code(response) == "PAYMENTS.PAYMENT.CURRENCY_MISMATCH"
        assert pay(client, pi, "100.00").status_code == 201  # 양성 대조
    assert len(ledger(pi)) == 1 and pi_status(pi) == "PARTIALLY_PAID"


@pytest.mark.parametrize(
    ("amounts", "ok"),
    [
        (["300.00"], True),  # due와 정확히 같다 — 통과(등호)
        (["300.01"], False),  # due + 0.01 — 초과
        (["299.99"], True),  # due − 0.01 — 통과(일부입금)
        (["100.00", "200.00"], True),  # 누적이 due와 같다
        (["100.00", "200.01"], False),  # 누적이 due를 0.01 넘는다
    ],
)
def test_exceeds_due_boundary_plus_minus_one_minor_unit(amounts: list[str], ok: bool) -> None:
    """순입금+금액 ≤ due — 경계(±1센트)·누적 합산. 초과는 422 EXCEEDS_DUE이고 마지막 입금만 거부된다"""
    pi = advance_pi()
    with logged_in(RoleCode.TRADE) as client:
        responses = [pay(client, pi, amount) for amount in amounts]
    assert all(r.status_code == 201 for r in responses[:-1])
    last = responses[-1]
    if ok:
        assert last.status_code == 201, last.text
    else:
        assert last.status_code == 422 and _code(last) == "PAYMENTS.PAYMENT.EXCEEDS_DUE"
        assert last.json()["error"]["detail"]["remaining_amount"] is not None
        assert len(ledger(pi)) == len(amounts) - 1
    expected_net = sum(int(a.replace(".", "")) for a in amounts[: len(amounts) if ok else -1])
    assert (
        scalar("SELECT COALESCE(SUM(received_amount), 0) FROM payments WHERE pi_id = :p", p=pi)
        == expected_net
    )


def test_after_a_reversal_the_freed_room_can_be_paid_again() -> None:
    """정정 = 역기록 후 재입금 — 역기록으로 비워진 만큼 다시 받을 수 있고 due 초과는 여전히 거부된다"""
    pi = advance_pi()
    with logged_in(RoleCode.TRADE) as client:
        first = pay(client, pi, "300.00")
        assert pay(client, pi, "0.01").status_code == 422  # 이미 due
        assert reverse(client, first.json()["payment"]["id"]).status_code == 201
        assert pay(client, pi, "250.00").status_code == 201
        assert pay(client, pi, "50.01").status_code == 422
    assert pi_status(pi) == "PARTIALLY_PAID"


def test_received_on_future_is_422_and_backdating_is_allowed_with_the_kst_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """입금일(증빙일): 미래 불가·소급 허용 — 기준은 KST 업무일이다(UTC 15:30 = KST 다음 날 00:30 → 그날 KST 날짜까지 허용)"""
    import app.core.time as core_time

    monkeypatch.setattr(
        core_time, "utcnow", lambda: datetime(2026, 9, 30, 15, 30, tzinfo=UTC)
    )  # KST 2026-10-01 00:30 (UTC 날짜는 아직 09-30)
    pi = advance_pi()
    with logged_in(RoleCode.TRADE) as client:
        tomorrow = pay(client, pi, "10.00", received_on=date(2026, 10, 2))
        assert tomorrow.status_code == 422
        assert tomorrow.json()["error"]["detail"]["received_on"]
        kst_today = pay(
            client, pi, "10.00", received_on=date(2026, 10, 1)
        )  # UTC 기준이면 미래 — KST 기준이라 통과
        assert kst_today.status_code == 201, kst_today.text
        assert pay(client, pi, "10.00", received_on=date(2025, 1, 1)).status_code == 201  # 소급
    assert len(ledger(pi)) == 2


@pytest.mark.parametrize("amount", ["10.123", "abc", "0", "0.00", "-5.00", "1e3", "12,34", ""])
def test_invalid_amounts_are_422_without_rounding(amount: str) -> None:
    """자릿수 초과(USD 소수 3자리)·비숫자·0·음수·지수·유럽식 콤마·빈 값 → 422 — 조용한 반올림 없음, 행 0"""
    pi = advance_pi()
    with logged_in(RoleCode.TRADE) as client:
        response = pay(client, pi, amount)
    assert response.status_code == 422, response.text
    assert ledger(pi) == []


def test_extra_fields_and_lowercase_currency_are_rejected_by_the_schema() -> None:
    """extra=forbid(status·pi_id 같은 필드를 본문으로 밀어 넣을 수 없다)·통화는 대문자 3자·참조 공백만 불가"""
    pi = advance_pi()
    with logged_in(RoleCode.TRADE) as client:
        url = f"/api/v1/proforma-invoices/{pi}/payments"
        base = receipt_body("10.00")
        for bad in (
            {**base, "status": "PAID"},
            {**base, "received_currency": "usd"},
            {**base, "reference": "   "},
            {**base, "reference": ""},
            {**base, "reference": "x" * 101},
        ):
            assert client.post(url, json=bad, headers=idem()).status_code == 422, bad
        assert client.post(url, json=base, headers=idem()).status_code == 201
        no_key = client.post(url, json=base)
        assert no_key.status_code == 400  # Idempotency-Key 필수
    assert len(ledger(pi)) == 1


@pytest.mark.parametrize("status", ["EXPIRED", "CANCELLED"])
def test_closed_pi_rejects_receipts_and_reversals_with_409(status: str) -> None:
    """취소·만료 PI는 입금을 받지 않는다 — 409 TRADE_DOCS.PAYMENT.PI_NOT_OPEN(원장 행 0)"""
    pi = advance_pi(status)
    with logged_in(RoleCode.TRADE) as client:
        response = pay(client, pi, "10.00")
    assert response.status_code == 409 and _code(response) == "TRADE_DOCS.PAYMENT.PI_NOT_OPEN"
    assert ledger(pi) == []


@pytest.mark.parametrize("payment_type", ["TT_DEFERRED", "LC"])
def test_only_advance_tt_pi_accepts_receipts(payment_type: str) -> None:
    """선수금 T/T가 아닌 PI(후불 T/T·L/C) 입금 → 422 PI_NOT_ADVANCE — 잔금·L/C 입금은 S3-3 채권 몫, 행 0·상태 불변"""
    pi = advance_pi()
    set_payment_terms(pi, payment_type)
    with logged_in(RoleCode.TRADE) as client:
        response = pay(client, pi, "10.00")
    assert response.status_code == 422 and _code(response) == "PAYMENTS.PAYMENT.PI_NOT_ADVANCE"
    assert ledger(pi) == [] and pi_status(pi) == "ISSUED"


def test_reversal_rules_reversal_of_a_reversal_double_reversal_unknown_and_short_reason() -> None:
    """역기록 규칙 — REVERSAL 행 역기록 409 NOT_REVERSIBLE·재역기록 409 ALREADY_REVERSED·없는 입금 404·사유 1자 422 — 거부는 행·상태를 바꾸지 않는다"""
    pi = advance_pi()
    with logged_in(RoleCode.TRADE) as client:
        paid = pay(client, pi, "100.00")
        pid = paid.json()["payment"]["id"]
        assert reverse(client, pid, "가").status_code == 422
        assert reverse(client, 9_999_999).status_code == 404
        done = reverse(client, pid)
        assert done.status_code == 201
        again = reverse(client, pid)
        assert again.status_code == 409 and _code(again) == "PAYMENTS.PAYMENT.ALREADY_REVERSED"
        of_reversal = reverse(client, done.json()["payment"]["id"])
        assert of_reversal.status_code == 409
        assert _code(of_reversal) == "PAYMENTS.PAYMENT.NOT_REVERSIBLE"
    assert len(ledger(pi)) == 2 and pi_status(pi) == "ISSUED"


@pytest.mark.parametrize("closed", ["CANCELLED", "EXPIRED"])
def test_reversal_after_the_pi_is_closed_is_rejected(closed: str) -> None:
    """입금 뒤 PI가 닫힌(취소·만료) 상태가 되면 역기록도 409 PI_NOT_OPEN — 닫힌 PI의 원장은 움직이지 않는다"""
    pi = advance_pi()
    with logged_in(RoleCode.TRADE) as client:
        paid = pay(client, pi, "100.00")
        with owner_engine.begin() as connection:
            connection.execute(
                text("UPDATE proforma_invoices SET status = :s WHERE id = :i"),
                {"s": closed, "i": pi},
            )
        response = reverse(client, paid.json()["payment"]["id"])
    assert response.status_code == 409 and _code(response) == "TRADE_DOCS.PAYMENT.PI_NOT_OPEN"
    assert len(ledger(pi)) == 1


def test_reversal_back_to_issued_after_validity_expires_the_pi_in_the_same_transaction() -> None:
    """유효기간이 지난 PI에서 입금을 전부 되돌리면 같은 트랜잭션에서 EXPIRED로 수렴(이력 2행) — 응답 summary의 PI 상태도 EXPIRED"""
    pi = advance_pi()
    with logged_in(RoleCode.TRADE) as client:
        paid = pay(client, pi, "100.00")
        assert paid.status_code == 201, paid.text
        with (
            owner_engine.begin() as connection
        ):  # 입금 뒤 유효기간이 지났다(일부입금 PI는 유효기간이 막지 않는다)
            connection.execute(
                text(
                    "UPDATE proforma_invoices SET valid_until = :d, doc_date = :d, fx_rate_date = :d"
                    " WHERE id = :i"
                ),
                {"d": date(2026, 1, 1), "i": pi},
            )
        back = reverse(client, paid.json()["payment"]["id"])
    assert back.status_code == 201 and back.json()["summary"]["pi_status"] == "EXPIRED"
    assert pi_status(pi) == "EXPIRED"
    tail = [(a, b) for a, b, _auto, _r in status_log(pi)][-2:]
    assert tail == [("PARTIALLY_PAID", "ISSUED"), ("ISSUED", "EXPIRED")]


def test_reversal_with_a_confirmed_sales_order_warns_publishes_and_leaves_the_so_untouched() -> (
    None
):
    """확정 SO가 이 PI를 근거로 있고 역기록 뒤 순입금이 required 미만 → 201 + warnings[CONFIRMED_SO_ADVANCE_UNMET, SO id]·outbox `reversed_after_confirm`(id만) — SO 상태·행 불변(자동 취소 없음)"""
    pi = advance_pi()
    so = raw_so("CONFIRMED", pi_id=pi)
    with logged_in(RoleCode.TRADE) as client:
        paid = pay(client, pi, "300.00")
        assert paid.json()["warnings"] == []  # 입금에는 경고가 없다
        back = reverse(client, paid.json()["payment"]["id"], "은행 회수 통보로 정정")
    assert back.status_code == 201, back.text
    assert back.json()["warnings"] == [
        {"code": "CONFIRMED_SO_ADVANCE_UNMET", "sales_order_ids": [so]}
    ]
    row = scalar("SELECT status FROM sales_orders WHERE id = :i", i=so)
    assert row == "CONFIRMED"  # 자동 취소·전이 없음
    assert (
        scalar("SELECT count(*) FROM sales_order_status_log WHERE sales_order_id = :i", i=so) == 1
    )
    with owner_engine.connect() as connection:
        events = (
            connection.execute(
                text(
                    "SELECT payload FROM events WHERE event_type = 'payments.payment.reversed_after_confirm'"
                    " AND (payload->>'pi_id')::bigint = :p"
                ),
                {"p": pi},
            )
            .scalars()
            .all()
        )
    assert len(events) == 1
    assert set(events[0]) == {"payment_id", "pi_id", "sales_order_ids"}
    assert events[0]["sales_order_ids"] == [so]


def test_no_warning_when_no_confirmed_live_so_exists_for_this_pi() -> None:
    """경고 대상이 아닌 SO(접수·취소·보류 직전 미확정·다른 PI의 SO)만 있으면 warnings=[]·이벤트 없음"""
    pi = advance_pi()
    other = advance_pi()
    raw_so("RECEIVED", pi_id=pi)
    raw_so("CANCELLED", pi_id=pi, confirmed=True)
    raw_so("CONFIRMED", pi_id=other)
    with logged_in(RoleCode.TRADE) as client:
        paid = pay(client, pi, "300.00")
        back = reverse(client, paid.json()["payment"]["id"])
    assert back.status_code == 201 and back.json()["warnings"] == []
    assert (
        scalar(
            "SELECT count(*) FROM events WHERE event_type = 'payments.payment.reversed_after_confirm'"
        )
        == 0
    )


def test_a_held_so_that_was_confirmed_still_counts_for_the_warning() -> None:
    """확정 뒤 보류(ON_HOLD, confirmed_at 있음)된 SO도 확정 이력이 있는 살아 있는 SO라 경고 대상이다"""
    pi = advance_pi()
    so = raw_so("ON_HOLD", pi_id=pi, confirmed=True)
    with logged_in(RoleCode.TRADE) as client:
        paid = pay(client, pi, "100.00")
        back = reverse(client, paid.json()["payment"]["id"])
    assert back.json()["warnings"] == [
        {"code": "CONFIRMED_SO_ADVANCE_UNMET", "sales_order_ids": [so]}
    ]


def test_audit_and_outbox_carry_ids_and_amounts_but_never_the_reference_or_reason_text() -> None:
    """audit(payments.receipt.recorded/reversed)·outbox 페이로드에는 참조 텍스트·사유가 없다(원장 행에만) — 금액·통화·증빙일·net_after·id만"""
    pi = advance_pi()
    secret_ref = "SECRET-BANK-REF-77"
    secret_reason = "비밀사유-정정-88"
    with logged_in(RoleCode.TRADE) as client:
        paid = pay(client, pi, "100.00", reference=secret_ref)
        reverse(client, paid.json()["payment"]["id"], secret_reason)
    with owner_engine.connect() as connection:
        audits = connection.execute(
            text("SELECT action, detail FROM audit_log WHERE action LIKE 'payments.%' ORDER BY id")
        ).all()
        events = connection.execute(
            text(
                "SELECT event_type, payload FROM events WHERE event_type LIKE 'payments.%' ORDER BY id"
            )
        ).all()
    assert [a for a, _d in audits] == ["payments.receipt.recorded", "payments.receipt.reversed"]
    blob = json.dumps([list(map(str, r)) for r in audits + events], ensure_ascii=False)
    assert secret_ref not in blob and secret_reason not in blob
    assert audits[0][1] == {
        "pi_id": pi,
        "amount": 10_000,
        "currency": "USD",
        "received_on": "2026-09-20",
        "net_after": 10_000,
    }
    assert audits[1][1]["net_after"] == 0
    assert [e for e, _p in events] == ["payments.payment.recorded", "payments.payment.reversed"]


def test_list_is_paginated_in_ledger_order_with_summary_and_404_for_unknown_pi() -> None:
    """GET 원장 — 페이지 크기 2(전체 3건)·id 오름차순·모든 페이지가 같은 요약(순입금·due·PI 상태)·없는 PI 404·size 상한 초과 422"""
    pi = advance_pi()
    with logged_in(RoleCode.TRADE) as client:
        for amount in ("10.00", "20.00", "30.00"):
            assert pay(client, pi, amount).status_code == 201
        page1 = client.get(f"/api/v1/proforma-invoices/{pi}/payments?page=1&size=2").json()
        page2 = client.get(f"/api/v1/proforma-invoices/{pi}/payments?page=2&size=2").json()
        assert client.get("/api/v1/proforma-invoices/99999999/payments").status_code == 404
        assert client.get(f"/api/v1/proforma-invoices/{pi}/payments?size=201").status_code == 422
        default = client.get(f"/api/v1/proforma-invoices/{pi}/payments").json()
    assert page1["total"] == page2["total"] == 3 and (page1["page"], page1["size"]) == (1, 2)
    assert [i["received_amount"] for i in page1["items"]] == [1000, 2000]
    assert [i["received_amount"] for i in page2["items"]] == [3000]
    assert page1["summary"] == page2["summary"]
    assert (
        page1["summary"]["net_received_amount"] == 6000
        and page1["summary"]["pi_status"] == "PARTIALLY_PAID"
    )
    assert (
        page1["summary"]["due_text"] == "300.00" and page1["summary"]["remaining_text"] == "240.00"
    )
    assert default["size"] == 50


def test_same_key_replay_returns_the_first_result_and_a_different_body_conflicts() -> None:
    """같은 Idempotency-Key 재전송 → 최초 201 그대로(행 1건)·같은 키 다른 본문 → 409 IDEMPOTENCY_KEY_CONFLICT"""
    pi = advance_pi()
    headers = idem()
    with logged_in(RoleCode.TRADE) as client:
        url = f"/api/v1/proforma-invoices/{pi}/payments"
        body = receipt_body("100.00")
        first = client.post(url, json=body, headers=headers)
        second = client.post(url, json=body, headers=headers)
        other = client.post(url, json=receipt_body("101.00"), headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert other.status_code == 409
    assert len(ledger(pi)) == 1


def test_a_rejection_does_not_consume_the_idempotency_key() -> None:
    """거부(4xx)는 키를 소비하지 않는다 — 같은 키로 고쳐 다시 보내면 새 판정을 받는다(성공만 complete)"""
    pi = advance_pi()
    headers = idem()
    with logged_in(RoleCode.TRADE) as client:
        url = f"/api/v1/proforma-invoices/{pi}/payments"
        refused = client.post(url, json=receipt_body("999.00"), headers=headers)
        assert refused.status_code == 422
        retry = client.post(url, json=receipt_body("999.00"), headers=headers)
        assert retry.status_code == 422  # 같은 본문은 같은 거부 — 키가 '처리됨'으로 굳지 않았다
        assert (
            scalar(
                "SELECT count(*) FROM idempotency_keys WHERE response_body IS NOT NULL AND endpoint LIKE '%payments%'"
            )
            == 0
        )
    assert ledger(pi) == []


@pytest.mark.parametrize("days_ago", [0, 1, 30])
def test_retroactive_receipts_keep_the_ledger_order_by_entry_not_by_received_on(
    days_ago: int,
) -> None:
    """증빙일(received_on)은 소급되어도 원장 순서는 입력 순(id)이다 — 입력일은 created_at(UTC), 증빙일과 별개"""
    pi = advance_pi()
    with logged_in(RoleCode.TRADE) as client:
        pay(client, pi, "10.00", received_on=date(2026, 9, 20))
        pay(client, pi, "10.00", received_on=date(2026, 9, 20) - timedelta(days=days_ago))
    rows = ledger(pi)
    assert rows[0]["id"] < rows[1]["id"] and rows[1]["created_at"] >= rows[0]["created_at"]


def _set_terms(pi: int, **columns: Any) -> None:
    sets = ", ".join(f"{c} = :{c}" for c in columns)
    with owner_engine.begin() as connection:
        connection.execute(
            text(f"UPDATE proforma_invoices SET {sets} WHERE id = :i"), {**columns, "i": pi}
        )


@pytest.mark.parametrize(
    ("total", "bp"),
    [(100_005, 3000), (100_001, 3333), (99_999, 3333), (1_001, 5000)],
)
def test_due_boundary_follows_split_advance_half_up(total: int, bp: int) -> None:
    """선수금 청구액은 `split_advance`(HALF_UP)와 정확히 같다 — 홀수 총액·bp 3333에서 due와 due+1센트를 경계로 통과/초과가 갈리고 floor·ceil 구현은 깨진다"""
    from app.modules.trade_docs.payment_terms import split_advance

    due = split_advance(total, bp)[0]
    exact_floor = total * bp // 10_000
    pi = advance_pi(total_amount=total)
    _set_terms(pi, advance_pct_bp=bp)
    text_of = lambda minor: f"{minor // 100}.{minor % 100:02d}"  # noqa: E731
    with logged_in(RoleCode.TRADE) as client:
        over = pay(client, pi, text_of(due + 1))
        assert over.status_code == 422 and _code(over) == "PAYMENTS.PAYMENT.EXCEEDS_DUE"
        assert pay(client, pi, text_of(due - 1)).status_code == 201
        last = pay(client, pi, "0.01")
        assert last.status_code == 201 and last.json()["summary"]["due_amount"] == due
        assert pi_status(pi) == "PAID"
        assert pay(client, pi, "0.01").status_code == 422
    assert due in (exact_floor, exact_floor + 1)


def test_zero_decimal_currency_accepts_whole_units_and_rejects_fractions() -> None:
    """JPY(소수 0자리) PI — "10"은 통과(10엔=10), "10.5"는 거부(반올림 없음), 요약 표기에 소수점이 없다"""
    pi = advance_pi()
    _set_terms(pi, currency="JPY", total_amount=100_000)
    with logged_in(RoleCode.TRADE) as client:
        half = pay(client, pi, "10.5", currency="JPY")
        assert half.status_code == 422 and half.json()["error"]["detail"]["received_amount"]
        ok = pay(client, pi, "10", currency="JPY")
        assert ok.status_code == 201, ok.text
        assert pay(client, pi, "10", currency="USD").status_code == 422  # 통화 불일치도 여전히 거부
    assert ok.json()["payment"]["received_amount"] == 10
    assert (
        ok.json()["summary"]["net_received_text"] == "10"
        and ok.json()["summary"]["due_text"] == "30000"
    )


def test_absurdly_large_amounts_are_422_and_the_largest_in_range_hits_exceeds_due() -> None:
    """16자리 이상 금액은 422(너무 큼 — MAX_MINOR_AMOUNT 이하 15자리 상한), 15자리 최대치는 형식은 통과하되 due 초과 422 — 어느 쪽도 행 0"""
    pi = advance_pi()
    with logged_in(RoleCode.TRADE) as client:
        huge = pay(client, pi, "99999999999999999.99")
        assert huge.status_code == 422 and _code(huge) == "COMMON.VALIDATION.INVALID_FIELD"
        edge = pay(client, pi, "9999999999999.99")
        assert edge.status_code == 422 and _code(edge) == "PAYMENTS.PAYMENT.EXCEEDS_DUE"
        assert pay(client, pi, "9007199254740993").status_code == 422
    assert ledger(pi) == []


@pytest.mark.parametrize("payment_type", ["LC", "TT_DEFERRED"])
def test_summary_of_a_non_advance_pi_shows_zero_due(payment_type: str) -> None:
    """선수금 T/T가 아닌 PI의 GET 원장 — 200·빈 목록·due 0·잔여 0·순입금 0·결제유형 표시(입금은 불가하지만 열람은 된다)"""
    pi = advance_pi()
    set_payment_terms(pi, payment_type)
    with logged_in(RoleCode.VIEWER) as client:
        body = client.get(f"/api/v1/proforma-invoices/{pi}/payments").json()
    assert body["items"] == [] and body["total"] == 0
    summary = body["summary"]
    assert (summary["payment_type"], summary["due_amount"], summary["remaining_amount"]) == (
        payment_type,
        0,
        0,
    )
    assert summary["net_received_amount"] == 0 and summary["pi_status"] == "ISSUED"


@pytest.mark.parametrize(
    "bad_reference",
    ["line1\nline2", "tab\there", "nul\x00byte", "bell\x07", "del\x7f", "\r\n", "\t"],
    ids=["newline", "tab", "nul", "bell", "del", "crlf-only", "tab-only"],
)
def test_control_characters_in_reference_and_reason_are_422(bad_reference: str) -> None:
    """참조·사유의 제어문자(개행·탭·NUL·BEL·DEL 포함 \\x00-\\x1f·\\x7f)는 422 — 행 0. 앞뒤 일반 공백은 잘려 저장된다(양성 대조)"""
    pi = advance_pi()
    with logged_in(RoleCode.TRADE) as client:
        refused = pay(client, pi, "10.00", reference=bad_reference)
        assert refused.status_code == 422, refused.text
        ok = pay(client, pi, "10.00", reference="  BANK-REF-1  ")
        assert ok.status_code == 201 and ok.json()["payment"]["reference"] == "BANK-REF-1"
        bad_reason = reverse(client, ok.json()["payment"]["id"], f"정정{bad_reference}사유")
        assert bad_reason.status_code == 422, bad_reason.text
        spaced = reverse(client, ok.json()["payment"]["id"], "  정정 사유  ")
        assert spaced.status_code == 201 and spaced.json()["payment"]["reason"] == "정정 사유"
    assert len(ledger(pi)) == 2


def test_length_is_checked_after_stripping() -> None:
    """길이는 strip 뒤 값으로 본다 — 공백 포함 101자라도 실질 100자면 통과, 실질 101자는 422, 사유는 공백 포함 1자+공백은 422(2자 미만)"""
    pi = advance_pi()
    with logged_in(RoleCode.TRADE) as client:
        assert pay(client, pi, "1.00", reference=" " + "r" * 100 + " ").status_code == 201
        assert pay(client, pi, "1.00", reference="r" * 101).status_code == 422
        paid = pay(client, pi, "1.00")
        assert reverse(client, paid.json()["payment"]["id"], " 가 ").status_code == 422
        assert reverse(client, paid.json()["payment"]["id"], "가나").status_code == 201


@pytest.mark.parametrize("amount", ["１２.３４", "１００", "١٠٠"])
def test_non_ascii_digits_in_amount_are_422(amount: str) -> None:
    """전각·아랍-인도 숫자 금액은 422 — 값이 조용히 바뀌어 기록되지 않는다"""
    pi = advance_pi()
    with logged_in(RoleCode.TRADE) as client:
        assert pay(client, pi, amount).status_code == 422
    assert ledger(pi) == []
