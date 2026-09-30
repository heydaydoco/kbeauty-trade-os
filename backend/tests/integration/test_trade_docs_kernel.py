"""A·J. 전표 커널 — 잔량 산식·소비 직렬화·합계 검산 잡·스냅샷 통로 (S3-1 PR-5a / ADR-0052·0056 / design-B B5·design-A A14).

잔량은 파생(SUM)이다: 소비자 레지스트리(LINE_CONSUMERS)의 실등록(PI 라인)과 무관하게 임시 소비자 테이블을 레지스트리에 끼워 산식·경계·
직렬화 계약을 시험한다(운영 스키마에 소비 코드 없는 테이블을 만들지 않는다 — B5 (f)). 실등록 소비는 test_proforma_invoices가 시험한다.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import text

from app.core.db.session import owner_engine
from app.core.db.uow import unit_of_work
from app.core.errors.exceptions import AppError, NotFoundError
from app.modules.identity.models import RoleCode
from app.modules.platform import scheduler
from app.modules.trade_docs import quantities, verify
from app.modules.trade_docs.constants import DocKind
from app.modules.trade_docs.quantities import ConsumerSpec, OpenQuantity
from tests.factories.trade import (
    create_buyer,
    create_priced_sku,
    create_quotation_via_api,
    issue_via_api,
    logged_in,
    unique,
)
from tests.support.concurrency import run_concurrently
from tests.support.factories import create_user

pytestmark = pytest.mark.group_a


def _scalar(sql: str, **params: Any) -> Any:
    with owner_engine.connect() as connection:
        return connection.execute(text(sql), params).scalar_one()


@contextmanager
def fake_consumer(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """QT_LINE을 소비하는 임시 자식 라인·헤더 테이블을 만들고 레지스트리에 끼운다(끝나면 지운다)."""
    with owner_engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS public.scratch_child_lines"))
        connection.execute(text("DROP TABLE IF EXISTS public.scratch_child_headers"))
        connection.execute(
            text(
                "CREATE TABLE public.scratch_child_headers (id BIGSERIAL PRIMARY KEY,"
                " status TEXT NOT NULL DEFAULT 'ISSUED', deleted_at TIMESTAMPTZ)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE public.scratch_child_lines (id BIGSERIAL PRIMARY KEY,"
                " child_id BIGINT NOT NULL, qt_line_id BIGINT NOT NULL, quantity INT NOT NULL,"
                " deleted_at TIMESTAMPTZ)"
            )
        )
        for table in ("scratch_child_headers", "scratch_child_lines"):
            connection.execute(
                text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON public.{table} TO kbos_app")
            )
            connection.execute(text(f"GRANT USAGE ON SEQUENCE public.{table}_id_seq TO kbos_app"))
    spec = ConsumerSpec(
        name="scratch",
        child_line_table="scratch_child_lines",
        line_fk_col="qt_line_id",
        qty_col="quantity",
        child_header_table="scratch_child_headers",
        child_header_fk="child_id",
    )
    monkeypatch.setitem(quantities.LINE_CONSUMERS, "QT_LINE", (spec,))
    try:
        yield
    finally:
        with owner_engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS public.scratch_child_lines"))
            connection.execute(text("DROP TABLE IF EXISTS public.scratch_child_headers"))


def _consume(line_id: int, qty: int, *, status: str = "ISSUED") -> int:
    with owner_engine.begin() as connection:
        header = connection.execute(
            text("INSERT INTO scratch_child_headers (status) VALUES (:s) RETURNING id"),
            {"s": status},
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO scratch_child_lines (child_id, qt_line_id, quantity) VALUES (:h, :l, :q)"
            ),
            {"h": header, "l": line_id, "q": qty},
        )
    return int(header)


def _open(line_id: int) -> OpenQuantity:
    with unit_of_work() as uow:
        return quantities.open_quantity(uow.session, "QT_LINE", [line_id])[line_id]


def _make_qt_line() -> tuple[int, int]:
    with logged_in(RoleCode.TRADE) as trade:
        sku = create_priced_sku(amount=100)
        qt = create_quotation_via_api(trade, create_buyer(), [sku])
        return qt["id"], qt["lines"][0]["id"]


def test_open_quantity_without_consumers_equals_the_ordered_quantity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """소비자가 없으면 잔량 = 주문량 · 없는 라인 id는 결과에서 빠진다 · 빈 입력은 빈 결과"""
    _qt, line = _make_qt_line()
    monkeypatch.setitem(quantities.LINE_CONSUMERS, "QT_LINE", ())
    assert _open(line) == OpenQuantity(ordered=10, consumed=0) and _open(line).open == 10
    with unit_of_work() as uow:
        assert quantities.open_quantity(uow.session, "QT_LINE", []) == {}
        assert quantities.open_quantity(uow.session, "QT_LINE", [999_999]) == {}


def test_open_quantity_is_a_live_sum_over_consumers(monkeypatch: pytest.MonkeyPatch) -> None:
    """잔량 = 주문량 − 살아 있는 소비량 SUM — 경계(정확히 0·+1 초과)·취소/만료 환원·삭제 라인 제외"""
    _qt, line = _make_qt_line()
    with fake_consumer(monkeypatch):
        first = _consume(line, 4)
        _consume(line, 3)
        assert _open(line) == OpenQuantity(10, 7)
        with unit_of_work() as uow:
            quantities.require_within_open(
                quantities.open_quantity(uow.session, "QT_LINE", [line]), {line: 3}
            )  # 정확히 0
            with pytest.raises(AppError) as caught:
                quantities.require_within_open(
                    quantities.open_quantity(uow.session, "QT_LINE", [line]), {line: 4}
                )
            assert (
                caught.value.code == "TRADE_DOCS.QUANTITY.EXCEEDS_OPEN"
                and caught.value.status_code == 409
            )
            assert caught.value.detail == {"open_quantity": {str(line): 3}}
            with pytest.raises(AppError):
                quantities.require_within_open({}, {line: 1})  # 모르는 라인은 거부(fail-closed)
        with owner_engine.begin() as connection:  # 소비 문서가 취소되면 수량이 환원된다
            connection.execute(
                text("UPDATE scratch_child_headers SET status = 'CANCELLED' WHERE id = :i"),
                {"i": first},
            )
        assert _open(line).consumed == 3
        with owner_engine.begin() as connection:  # 만료도 환원
            connection.execute(text("UPDATE scratch_child_headers SET status = 'EXPIRED'"))
        assert _open(line).consumed == 0
        with owner_engine.begin() as connection:  # ON_HOLD·삭제
            connection.execute(text("UPDATE scratch_child_headers SET status = 'ON_HOLD'"))
        assert _open(line).consumed == 7  # ON_HOLD는 살아 있다
        with owner_engine.begin() as connection:
            connection.execute(
                text("UPDATE scratch_child_lines SET deleted_at = now() WHERE quantity = 3")
            )
            connection.execute(
                text("UPDATE scratch_child_headers SET deleted_at = now() WHERE id = :i"),
                {"i": first},
            )
        assert _open(line).consumed == 0  # 삭제된 라인·삭제된 헤더는 세지 않는다


def test_multiple_consumers_are_summed(monkeypatch: pytest.MonkeyPatch) -> None:
    """소비자가 둘 이상이면 합산한다(PI 라인 경유 + SO 라인 직접 경로처럼)"""
    _qt, line = _make_qt_line()
    with fake_consumer(monkeypatch):
        _consume(line, 2)
        first = quantities.LINE_CONSUMERS["QT_LINE"][0]
        second = ConsumerSpec(
            name="scratch2",
            child_line_table=first.child_line_table,
            line_fk_col=first.line_fk_col,
            qty_col=first.qty_col,
            child_header_table=first.child_header_table,
            child_header_fk=first.child_header_fk,
        )
        monkeypatch.setitem(quantities.LINE_CONSUMERS, "QT_LINE", (first, second))
        assert _open(line).consumed == 4  # 같은 표를 두 소비자가 각각 센다 — 합산 경로 확인


@pytest.fixture
def consumable_qt(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[int, list[int]]]:
    """QT를 임시로 '소비 가능 문서'로 취급해 소비 잠금 계약(헤더 SHARE→라인 FOR UPDATE id순)을 실제 표로 시험한다."""
    with logged_in(RoleCode.TRADE) as trade:
        skus = [create_priced_sku(amount=100) for _ in range(2)]
        qt = issue_via_api(trade, create_quotation_via_api(trade, create_buyer(), skus))
    monkeypatch.setitem(quantities.CONSUMABLE_STATUSES, DocKind.QUOTATION, frozenset({"ISSUED"}))
    with fake_consumer(monkeypatch):
        yield qt["id"], [line["id"] for line in qt["lines"]]


def _consume_with_lock(qt_id: int, requests: dict[int, int]) -> None:
    """소비 절차(소비 세션 의무): ① 잠금 ② 잔량 재계산 ③ 요청 ≤ 잔량 ④ INSERT — 한 트랜잭션."""
    with unit_of_work() as uow:
        session = uow.session
        quantities.lock_lines_for_consumption(session, DocKind.QUOTATION, qt_id, list(requests))
        quantities.require_within_open(
            quantities.open_quantity(session, "QT_LINE", list(requests)), requests
        )
        header = session.execute(
            text("INSERT INTO scratch_child_headers DEFAULT VALUES RETURNING id")
        ).scalar_one()
        for line_id, qty in requests.items():
            session.execute(
                text(
                    "INSERT INTO scratch_child_lines (child_id, qt_line_id, quantity) VALUES (:h, :l, :q)"
                ),
                {"h": header, "l": line_id, "q": qty},
            )


def test_consumption_lock_serializes_competing_consumers(
    consumable_qt: tuple[int, list[int]],
) -> None:
    """잔량 10에 동시 7+7 소비 → 한쪽만 성공·합계 ≤ 주문량 · 1씩 12스레드 → 정확히 10건만 성공"""
    qt_id, (line, _other) = consumable_qt

    outcomes = run_concurrently(lambda _i: _consume_with_lock(qt_id, {line: 7}), workers=2)
    assert sum(1 for o in outcomes if o.ok) == 1
    assert all(
        isinstance(o.error, AppError) and o.error.status_code == 409 for o in outcomes if not o.ok
    )
    assert _open(line).consumed == 7
    outcomes = run_concurrently(lambda _i: _consume_with_lock(qt_id, {line: 1}), workers=12)
    assert sum(1 for o in outcomes if o.ok) == 3  # 남은 3
    assert _open(line).open == 0


def test_consumption_lock_orders_lines_by_id_and_avoids_deadlock(
    consumable_qt: tuple[int, list[int]],
) -> None:
    """서로 반대 순서로 두 라인을 요청하는 두 트랜잭션도 교착하지 않는다(라인 id 오름차순 잠금)"""
    qt_id, (a, b) = consumable_qt
    order = [{a: 1, b: 1}, {b: 1, a: 1}]
    outcomes = run_concurrently(lambda i: _consume_with_lock(qt_id, order[i]), workers=2)
    assert all(o.ok for o in outcomes), [o.error for o in outcomes]
    assert _open(a).consumed == 2 and _open(b).consumed == 2


def test_consumption_requires_a_consumable_document(monkeypatch: pytest.MonkeyPatch) -> None:
    """소비 가능 상태가 아닌 문서(또는 소비 대상이 아닌 종류)는 409 DOCUMENT_NOT_CONSUMABLE · 없는 문서는 404"""
    qt_id, line = _make_qt_line()  # DRAFT
    with unit_of_work() as uow, pytest.raises(AppError) as caught:
        quantities.lock_lines_for_consumption(uow.session, DocKind.QUOTATION, qt_id, [line])
    assert caught.value.code == "TRADE_DOCS.QUANTITY.DOCUMENT_NOT_CONSUMABLE"
    monkeypatch.setitem(quantities.CONSUMABLE_STATUSES, DocKind.QUOTATION, frozenset({"ISSUED"}))
    with unit_of_work() as uow, pytest.raises(AppError):  # 소비 가능 집합에 없는 DRAFT
        quantities.lock_lines_for_consumption(uow.session, DocKind.QUOTATION, qt_id, [line])
    with unit_of_work() as uow, pytest.raises(NotFoundError):
        quantities.lock_lines_for_consumption(uow.session, DocKind.QUOTATION, 999_999, [1])


# ── 합계 검산 ───────────────────────────────────────────────────────────────


def _admin_and_qt(lines: int = 2) -> tuple[int, int]:
    admin = create_user(f"{unique('adm')}@example.com", roles=(RoleCode.ADMIN,))
    with logged_in(RoleCode.TRADE) as trade:
        qt = create_quotation_via_api(
            trade, create_buyer(), [create_priced_sku(amount=100 * (i + 1)) for i in range(lines)]
        )
    return admin, qt["id"]


def test_verify_finds_a_header_that_disagrees_with_its_lines_and_ignores_the_rest() -> None:
    """검산: 정상 0건 · 헤더 합계를 SQL로 틀어 놓으면 검출 · 삭제된 라인은 제외 · 삭제된 헤더는 대상 밖 · 취소 전표는 대상"""
    _admin, qt = _admin_and_qt()
    with unit_of_work() as uow:
        assert verify.verify_document_totals(uow.session) == []
    with owner_engine.begin() as connection:
        connection.execute(
            text("UPDATE quotations SET total_amount = total_amount + 1 WHERE id = :i"), {"i": qt}
        )
    with unit_of_work() as uow:
        found = verify.verify_document_totals(uow.session)
    assert [(m.doc_kind, m.doc_id, m.lines_total, m.header_total) for m in found] == [
        (DocKind.QUOTATION, qt, 10 * 100 + 10 * 200, 10 * 100 + 10 * 200 + 1)
    ]
    with (
        owner_engine.begin() as connection
    ):  # 라인 하나를 지우면(soft delete) 합이 바뀌어 다시 어긋난다
        connection.execute(
            text("UPDATE quotations SET total_amount = 3000 WHERE id = :i"), {"i": qt}
        )
        connection.execute(
            text("UPDATE quotation_lines SET deleted_at = now() WHERE qt_id = :i AND line_no = 2"),
            {"i": qt},
        )
    with unit_of_work() as uow:
        assert [m.lines_total for m in verify.verify_document_totals(uow.session)] == [1000]
    with owner_engine.begin() as connection:  # 취소 전표도 대상
        connection.execute(
            text("UPDATE quotations SET status = 'CANCELLED', frozen_at = NULL WHERE id = :i"),
            {"i": qt},
        )
    with unit_of_work() as uow:
        assert len(verify.verify_document_totals(uow.session)) == 1
    with owner_engine.begin() as connection:  # 삭제된 헤더는 제외
        connection.execute(
            text("UPDATE quotations SET deleted_at = now() WHERE id = :i"), {"i": qt}
        )
    with unit_of_work() as uow:
        assert verify.verify_document_totals(uow.session) == []


def test_a_header_without_lines_and_zero_total_is_consistent() -> None:
    """라인 0개·합계 0은 정상 — 외부조인이 빈 합을 0으로 본다"""
    with logged_in(RoleCode.TRADE) as trade:
        create_quotation_via_api(trade, create_buyer())
    with unit_of_work() as uow:
        assert verify.verify_document_totals(uow.session) == []


def test_totals_job_alerts_admins_once_per_day_without_amounts_and_never_corrects() -> None:
    """잡 본체: 불일치마다 ADMIN 인앱 알림(문서번호만·금액 미기재) · 같은 KST 일자 재실행은 0건 · 자동 보정 없음"""
    admin, qt = _admin_and_qt()
    with owner_engine.begin() as connection:
        connection.execute(
            text("UPDATE quotations SET total_amount = 999999 WHERE id = :i"), {"i": qt}
        )
    counts = verify.run_totals_verify()
    assert counts == {"mismatches": 1, "notified": 1}
    row = _scalar(
        "SELECT title || ' ' || coalesce(body, '') FROM alerts WHERE recipient_user_id = :u",
        u=admin,
    )
    number = _scalar("SELECT doc_number FROM quotations WHERE id = :i", i=qt)
    assert number in row and "999999" not in row and "3000" not in row
    assert (
        _scalar(
            "SELECT entity_type || ':' || entity_id FROM alerts WHERE recipient_user_id = :u",
            u=admin,
        )
        == f"quotations:{qt}"
    )
    assert verify.run_totals_verify() == {"mismatches": 1, "notified": 0}  # 같은 날 dedup
    assert (
        _scalar("SELECT total_amount FROM quotations WHERE id = :i", i=qt) == 999999
    )  # 조용한 보정 금지
    assert _scalar("SELECT count(*) FROM alerts WHERE recipient_user_id = :u", u=admin) == 1


def test_totals_job_is_registered_daily_and_fails_loudly_when_the_check_breaks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """레지스트리 등록(daily@05:30) · 검사 예외는 삼키지 않고 잡을 FAILED로 올린다(§15 실패 알림 소비)"""
    spec = scheduler.JOBS_BY_CODE["trade-docs-totals-verify"]
    assert spec.schedule == "daily@05:30"
    scheduler.register_jobs()

    def boom(_session: Any) -> Any:
        raise RuntimeError("검산 실패 주입")

    monkeypatch.setattr(verify, "verify_document_totals", boom)
    with pytest.raises(RuntimeError):
        spec.run()
    counts = scheduler.run_due_jobs()
    assert counts["failed"] >= 1
    assert (
        _scalar("SELECT last_status FROM scheduled_jobs WHERE code = 'trade-docs-totals-verify'")
        == "FAILED"
    )
