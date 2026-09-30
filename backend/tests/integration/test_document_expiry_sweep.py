"""H·A. 만료 스윕 `document-expiry-sweep` — 경계·정합·건별 독립 TX·후속 보유 제외·멱등·행 잠금 후 재확인 (S3-1 PR-6a / ADR-0056 / design-B B7).

잡이 만드는 전이는 (QT,ISSUED→EXPIRED)·(PI,ISSUED→EXPIRED) 두 엣지뿐이다(4금 논증). 스윕 로그는 행위자 NULL·automatic=true다.
유효기간은 당일 KST 24:00까지 유효 — `valid_until < 기준일`일 때만 만료(포함 경계). 후속(살아 있는 PI·SO)이 있으면 부모를 닫지 않는다.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from app import cli
from app.core.db.session import owner_engine
from app.core.time import today_kst
from app.modules.platform import scheduler
from app.modules.trade_chain import expiry_sweep
from app.modules.trade_chain.expiry_sweep import sweep_expired_documents
from app.modules.trade_docs.constants import DocKind
from tests.factories.trade import fake_successors, raw_pi, raw_quotation

pytestmark = pytest.mark.group_h


def _scalar(sql: str, **params: Any) -> Any:
    with owner_engine.connect() as connection:
        return connection.execute(text(sql), params).scalar_one()


def _status(table: str, row_id: int) -> str:
    return str(_scalar(f"SELECT status FROM {table} WHERE id = :i", i=row_id))


def _qt(valid_until: date, status: str = "ISSUED") -> int:
    return raw_quotation(status, valid_until=valid_until, doc_date=date(2026, 9, 1))


def _log(table: str, fk: str, row_id: int) -> list[tuple[Any, ...]]:
    with owner_engine.connect() as connection:
        return [
            tuple(r)
            for r in connection.execute(
                text(
                    f"SELECT from_status, to_status, automatic, actor_user_id, reason FROM {table}"
                    f" WHERE {fk} = :i ORDER BY id"
                ),
                {"i": row_id},
            )
        ]


TODAY = today_kst()
YESTERDAY = TODAY - timedelta(days=1)


def test_the_boundary_is_inclusive_of_the_validity_day_itself() -> None:
    """valid_until = 기준일이면 유지(당일 KST 24:00까지 유효) · 기준일−1이면 만료 — QT·PI 양쪽"""
    keep_qt, expire_qt = _qt(TODAY), _qt(YESTERDAY)
    keep_pi = raw_pi(_qt(TODAY + timedelta(days=30)), "ISSUED", valid_until=TODAY)
    expire_pi = raw_pi(_qt(TODAY + timedelta(days=30)), "ISSUED", valid_until=YESTERDAY)
    counts = sweep_expired_documents()
    assert counts == {"expired_qt": 1, "expired_pi": 1, "skipped": 0, "failed": 0}
    assert (
        _status("quotations", keep_qt) == "ISSUED" and _status("quotations", expire_qt) == "EXPIRED"
    )
    assert _status("proforma_invoices", keep_pi) == "ISSUED"
    assert _status("proforma_invoices", expire_pi) == "EXPIRED"


def test_the_base_date_decides_the_boundary_not_the_utc_date() -> None:
    """기준일은 KST 업무일 — --base-date로 하루 밀면 같은 문서가 만료 대상이 된다(UTC 날짜가 아닌 지정 기준일로 판정)"""
    qt = _qt(TODAY)
    assert sweep_expired_documents(base_date=TODAY)["expired_qt"] == 0
    assert sweep_expired_documents(base_date=TODAY + timedelta(days=1))["expired_qt"] == 1
    assert _status("quotations", qt) == "EXPIRED"
    reason = _log("quotation_status_log", "quotation_id", qt)[-1][4]
    assert f"기준일={TODAY + timedelta(days=1)}" in reason and f"valid_until={TODAY}" in reason


def test_sweep_logs_are_system_actions_and_rerun_changes_nothing() -> None:
    """스윕 이력 = 행위자 NULL·automatic=true·사유(기준일 포함) · 재실행은 변화 0(이력·이벤트 중복 0)"""
    qt = _qt(YESTERDAY)
    pi = raw_pi(_qt(TODAY + timedelta(days=30)), "ISSUED", valid_until=YESTERDAY)
    first = sweep_expired_documents()
    assert first["expired_qt"] == 1 and first["expired_pi"] == 1
    for table, fk, doc in (
        ("quotation_status_log", "quotation_id", qt),
        ("proforma_invoice_status_log", "proforma_invoice_id", pi),
    ):
        last = _log(table, fk, doc)[-1]
        assert last[:4] == ("ISSUED", "EXPIRED", True, None) and "자동: 유효기간 경과" in last[4]
    events_before = _scalar("SELECT count(*) FROM events WHERE event_type LIKE '%status_changed'")
    assert sweep_expired_documents() == {
        "expired_qt": 0,
        "expired_pi": 0,
        "skipped": 0,
        "failed": 0,
    }
    assert (
        _scalar("SELECT count(*) FROM events WHERE event_type LIKE '%status_changed'")
        == events_before
    )
    with owner_engine.connect() as connection:
        payloads = [
            r[0]
            for r in connection.execute(
                text(
                    "SELECT payload FROM events WHERE event_type LIKE '%status_changed'"
                    " AND aggregate_id = :i"
                ),
                {"i": qt},
            )
        ]
    assert payloads and payloads[0]["automatic"] is True and payloads[0]["to_status"] == "EXPIRED"


def test_only_unpaid_issued_documents_are_candidates() -> None:
    """후보 = 미입금 발행(ISSUED) — 일부입금·입금완료 PI·CONVERTED QT·이미 종결된 문서는 유효기간이 지나도 무변"""
    base_qt = _qt(TODAY + timedelta(days=30))
    partial = raw_pi(base_qt, "PARTIALLY_PAID", valid_until=YESTERDAY)
    paid = raw_pi(base_qt, "PAID", valid_until=YESTERDAY)
    cancelled = raw_pi(base_qt, "CANCELLED", valid_until=YESTERDAY)
    converted = _qt(YESTERDAY, "CONVERTED")
    cancelled_qt = _qt(YESTERDAY, "CANCELLED")
    counts = sweep_expired_documents()
    assert counts == {"expired_qt": 0, "expired_pi": 0, "skipped": 0, "failed": 0}
    assert _status("proforma_invoices", partial) == "PARTIALLY_PAID"
    assert _status("proforma_invoices", paid) == "PAID"
    assert _status("proforma_invoices", cancelled) == "CANCELLED"
    assert _status("quotations", converted) == "CONVERTED"
    assert _status("quotations", cancelled_qt) == "CANCELLED"


def test_a_quotation_held_by_a_live_pi_is_not_expired_until_the_pi_dies() -> None:
    """살아 있는 PI가 QT를 붙잡는다(X-18) — QT 만료는 건너뜀(skipped) · PI가 같은 실행에서 만료되면 QT도 그 실행에서 만료된다(PI 먼저)"""
    qt = _qt(YESTERDAY)
    pi_open = raw_pi(qt, "ISSUED", valid_until=TODAY + timedelta(days=10))
    counts = sweep_expired_documents()
    assert counts == {"expired_qt": 0, "expired_pi": 0, "skipped": 1, "failed": 0}
    assert _status("quotations", qt) == "ISSUED"
    with (
        owner_engine.begin() as connection
    ):  # PI의 유효기간도 지났다 → 같은 실행에서 PI→QT 순으로 닫힌다
        connection.execute(
            text(
                "UPDATE proforma_invoices SET valid_until = :d, doc_date = :d, fx_rate_date = :d WHERE id = :i"
            ),
            {"d": YESTERDAY, "i": pi_open},
        )
    counts = sweep_expired_documents()
    assert counts == {"expired_qt": 1, "expired_pi": 1, "skipped": 0, "failed": 0}
    assert (
        _status("proforma_invoices", pi_open) == "EXPIRED"
        and _status("quotations", qt) == "EXPIRED"
    )


def test_a_pi_held_by_a_live_successor_is_never_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    """살아 있는 후속(SO 대역)이 있는 PI는 미입금이어도 닫지 않는다 — 입금이 계속 들어와야 하므로(skipped)"""
    qt = _qt(TODAY + timedelta(days=30))
    pi = raw_pi(qt, "ISSUED", valid_until=YESTERDAY)
    with fake_successors(
        monkeypatch, fk_column="pi_id", parent=DocKind.PROFORMA_INVOICE
    ) as successors:
        so = successors.add(pi, status="RECEIVED")
        assert sweep_expired_documents()["skipped"] == 1
        assert _status("proforma_invoices", pi) == "ISSUED"
        successors.set_status(so, "CANCELLED")  # 후속이 죽으면 다음 실행에서 닫힌다
        assert sweep_expired_documents()["expired_pi"] == 1
    assert _status("proforma_invoices", pi) == "EXPIRED"


def test_each_document_is_its_own_transaction_and_a_failure_fails_the_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """건별 독립 TX — 3건 중 1건에 실패를 주입하면 나머지 2건은 EXPIRED로 커밋되고 failed=1이며 잡은 FAILED(RuntimeError)로 올라간다"""
    ids = [_qt(YESTERDAY) for _ in range(3)]
    poisoned = ids[1]
    real = expiry_sweep.record_transition

    def flaky(session: Any, doc: Any, to: str, **kwargs: Any) -> str:
        if doc.id == poisoned:
            raise RuntimeError("주입된 실패 SECRET-DETAIL-9")
        return real(session, doc, to, **kwargs)

    monkeypatch.setattr(expiry_sweep, "record_transition", flaky)
    counts = sweep_expired_documents()
    assert counts == {"expired_qt": 2, "expired_pi": 0, "skipped": 0, "failed": 1}
    assert [_status("quotations", i) for i in ids] == ["EXPIRED", "ISSUED", "EXPIRED"]
    # 실패 건은 이력·이벤트를 남기지 않는다(그 건의 트랜잭션은 통째로 롤백)
    assert len(_log("quotation_status_log", "quotation_id", poisoned)) == 1
    with pytest.raises(RuntimeError, match="1건 실패"):
        scheduler.JOBS_BY_CODE[
            "document-expiry-sweep"
        ].run()  # 재실행: 성공분은 멱등, 실패 건이 남아 잡이 FAILED
    monkeypatch.setattr(expiry_sweep, "record_transition", real)
    assert sweep_expired_documents()["expired_qt"] == 1  # 고쳐지면 그 건도 닫힌다
    assert _status("quotations", poisoned) == "EXPIRED"


def test_the_error_log_scrubs_the_exception_text(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """실패 로그는 entity_id·예외 종류·scrub된 문구뿐 — DB 예외의 Failing row(값 전체)는 지워진다"""
    qt = _qt(YESTERDAY)

    def boom(*_a: Any, **_k: Any) -> str:
        raise RuntimeError(
            "check violation\nDETAIL:  Failing row contains (110-222-333, secret-note)."
        )

    monkeypatch.setattr(expiry_sweep, "record_transition", boom)
    with caplog.at_level("ERROR"):
        assert sweep_expired_documents()["failed"] == 1
    logged = " ".join(str(record.msg) for record in caplog.records)
    assert "document_expiry_sweep_failed" in logged and f"'entity_id': {qt}" in logged
    assert "110-222-333" not in logged and "secret-note" not in logged


def test_the_row_is_rechecked_after_the_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    """후보 수집 뒤 사람이 취소한 문서는 잠근 뒤 재확인에서 skipped — 이중 전이·이력 오염 0"""
    qt = _qt(YESTERDAY)
    pi = raw_pi(_qt(TODAY + timedelta(days=30)), "ISSUED", valid_until=YESTERDAY)
    real = expiry_sweep._candidate_ids

    def stale_then_cancel(model: Any, today: date) -> list[int]:
        ids = real(model, today)  # 후보 수집 시점의 목록
        with (
            owner_engine.begin() as connection
        ):  # 그 사이 사람이 취소·입금이 들어왔다(모델별로 자기 후보만)
            if model.__tablename__ == "quotations":
                connection.execute(
                    text("UPDATE quotations SET status = 'CANCELLED' WHERE id = :i"), {"i": qt}
                )
            else:
                connection.execute(
                    text("UPDATE proforma_invoices SET status = 'PAID' WHERE id = :i"), {"i": pi}
                )
        return ids

    monkeypatch.setattr(expiry_sweep, "_candidate_ids", stale_then_cancel)
    counts = sweep_expired_documents()
    assert counts == {"expired_qt": 0, "expired_pi": 0, "skipped": 2, "failed": 0}
    assert _status("quotations", qt) == "CANCELLED" and _status("proforma_invoices", pi) == "PAID"
    assert len(_log("quotation_status_log", "quotation_id", qt)) == 1  # 스윕이 이력을 더하지 않았다


def test_sweep_creates_only_the_two_edges_and_touches_no_other_table() -> None:
    """만든 전이는 정확히 {(QT,ISSUED,EXPIRED),(PI,ISSUED,EXPIRED)} — SO·PO·알림·번호 카운터·감사·멱등 무접촉"""
    _qt(YESTERDAY)
    raw_pi(_qt(TODAY + timedelta(days=30)), "ISSUED", valid_until=YESTERDAY)
    tables = ["alerts", "audit_log", "idempotency_keys", "doc_number_seq", "tasks"]
    before = {t: _scalar(f"SELECT count(*) FROM {t}") for t in tables}
    sweep_expired_documents()
    assert {t: _scalar(f"SELECT count(*) FROM {t}") for t in tables} == before
    seen: set[tuple[str, str, str]] = set()
    with owner_engine.connect() as connection:
        for table, kind in (
            ("quotation_status_log", DocKind.QUOTATION),
            ("proforma_invoice_status_log", DocKind.PROFORMA_INVOICE),
        ):
            for frm, to in connection.execute(
                text(f"SELECT from_status, to_status FROM {table} WHERE from_status IS NOT NULL")
            ):
                seen.add((kind.value, frm, to))
    assert seen == {(k.value, a, b) for k, a, b in expiry_sweep.SWEEP_EDGES}


def test_the_job_is_registered_and_runs_from_cli_and_scheduler() -> None:
    """레지스트리 daily@06:10 · 마이그레이션 시드 아님(register_jobs로 등록) · CLI --base-date가 같은 함수를 실행 · 실패 0이면 종료코드 0"""
    spec = scheduler.JOBS_BY_CODE["document-expiry-sweep"]
    assert spec.schedule == "daily@06:10"
    assert scheduler.register_jobs().count("document-expiry-sweep") == 1
    assert scheduler.register_jobs() == []  # 멱등
    qt = _qt(TODAY)
    assert cli.main(["document-expiry-sweep"]) == 0
    assert _status("quotations", qt) == "ISSUED"
    assert cli.main(["document-expiry-sweep", "--base-date", str(TODAY + timedelta(days=1))]) == 0
    assert _status("quotations", qt) == "EXPIRED"
    assert spec.run()["expired_qt"] == 0  # 잡 본체도 같은 함수 — 이미 닫혔으니 변화 0
