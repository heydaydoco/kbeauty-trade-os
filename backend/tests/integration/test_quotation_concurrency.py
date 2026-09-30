"""J. 견적 동시성 — 채번 동시 100·발행 더블클릭·발행 vs 편집·같은 원본 복제·잠금 대기 (S3-1 PR-5a / GC-F1).

★ 순차 실행은 증거가 아니다 — 실제 스레드를 Barrier로 동시에 출발시킨다(tests/support/concurrency.py). 각 스레드는
  자기 세션(unit_of_work)을 연다. 이 파일의 테스트는 커밋을 동반하므로 병렬(pytest-xdist)로 돌리지 않는다.
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
from app.modules.trade_chain import lifecycle
from app.modules.trade_docs.locking import LOCK_ORDER
from tests.factories.trade import (
    PAYMENT_TT_ADVANCE_30,
    create_buyer,
    create_priced_sku,
    unique,
)
from tests.support.concurrency import run_concurrently
from tests.support.factories import create_market, create_user

pytestmark = pytest.mark.group_j


def _actor(email: str | None = None) -> AuthenticatedUser:
    address = email or f"{unique('conc')}@example.com"
    user_id = create_user(address, roles=(RoleCode.TRADE,))
    return AuthenticatedUser(
        id=user_id,
        email=address,
        display_name="동시성",
        roles=frozenset({RoleCode.TRADE}),
        session_id=0,
    )


def _payload(buyer: int, sku: int | None = None, **extra: Any) -> dict[str, Any]:
    create_market("US")
    body: dict[str, Any] = {
        "buyer_partner_id": buyer,
        "dest_market_code": "US",
        "currency": "USD",
        "fx_rate": "1350",
        "valid_until": today_kst() + timedelta(days=30),
        "payment_terms": PAYMENT_TT_ADVANCE_30,
        "incoterm": {"code": "FOB", "place": "Busan", "year": 2020},
        "lines": [{"sku_id": sku, "quantity": 2}] if sku else [],
    }
    body.update(extra)
    return body


def _scalar(sql: str, **params: Any) -> Any:
    with owner_engine.connect() as connection:
        return connection.execute(text(sql), params).scalar_one()


def test_one_hundred_concurrent_creations_get_distinct_contiguous_numbers() -> None:
    """QT 동시 100건 작성 → 번호 100종·1..100 연속·UNIQUE 위반 0·실패 0(이력·이벤트도 100건씩)"""
    actor = _actor()
    buyer = create_buyer()
    sku = create_priced_sku(amount=100)
    body = _payload(buyer, sku)

    def worker(index: int) -> str:
        _status, out = quotations.create_quotation(
            actor=actor, idempotency_key=f"c100-{index}", payload=body
        )
        return str(out["doc_number"])

    outcomes = run_concurrently(worker, workers=100)
    failures = [o.error for o in outcomes if not o.ok]
    assert failures == [], failures
    numbers = sorted(o.value for o in outcomes)
    assert len(set(numbers)) == 100
    assert [int(n.rsplit("-", 1)[1]) for n in numbers] == list(range(1, 101))
    assert _scalar("SELECT count(*) FROM quotations") == 100
    assert _scalar("SELECT count(*) FROM quotation_status_log") == 100
    assert (
        _scalar("SELECT count(*) FROM events WHERE event_type = 'quotations.quotation.created'")
        == 100
    )
    assert _scalar("SELECT last_number FROM doc_number_seq WHERE prefix = 'QT'") == 100


def test_a_failed_creation_gives_the_number_back() -> None:
    """생성 도중 실패하면 번호가 소비되지 않는다 — 다음 번호는 결번 없이 이어진다"""
    actor = _actor()
    buyer = create_buyer()
    quotations.create_quotation(actor=actor, idempotency_key="ok-1", payload=_payload(buyer))
    with pytest.raises(AppError):  # 단가 없는 SKU — 채번 이전 검증 실패
        from tests.support.factories import create_sku

        bare = create_sku(unique("BARE"))
        quotations.create_quotation(
            actor=actor, idempotency_key="bad-1", payload=_payload(buyer, bare)
        )
    _status, third = quotations.create_quotation(
        actor=actor, idempotency_key="ok-2", payload=_payload(buyer)
    )
    assert third["doc_number"].endswith("-0002")


def _draft(actor: AuthenticatedUser, buyer: int, sku: int) -> dict[str, Any]:
    _status, body = quotations.create_quotation(
        actor=actor, idempotency_key=unique("draft"), payload=_payload(buyer, sku)
    )
    return body


def test_issue_double_click_with_the_same_key_creates_one_transition() -> None:
    """발행 더블클릭(같은 키 동시 2요청) → 둘 다 같은 결과·이력 1행·이벤트 1건(멱등 claim 직렬화)"""
    actor = _actor()
    qt = _draft(actor, create_buyer(), create_priced_sku())

    def worker(_i: int) -> dict[str, Any]:
        return lifecycle.issue_quotation(
            actor=actor, idempotency_key="dbl-issue", qt_id=qt["id"], version=qt["version"]
        )[1]

    outcomes = run_concurrently(worker, workers=2)
    assert all(o.ok for o in outcomes), [o.error for o in outcomes]
    assert outcomes[0].value == outcomes[1].value
    assert _scalar("SELECT count(*) FROM quotation_status_log WHERE to_status = 'ISSUED'") == 1
    assert (
        _scalar(
            "SELECT count(*) FROM events WHERE event_type = 'quotations.quotation.status_changed'"
        )
        == 1
    )


def test_issue_with_different_keys_lets_exactly_one_win() -> None:
    """발행을 서로 다른 키로 동시에 2번 → 정확히 1건 성공, 나머지는 409(이미 발행됨·version 충돌)"""
    actor = _actor()
    qt = _draft(actor, create_buyer(), create_priced_sku())

    def worker(i: int) -> Any:
        return lifecycle.issue_quotation(
            actor=actor, idempotency_key=f"diff-{i}", qt_id=qt["id"], version=qt["version"]
        )

    outcomes = run_concurrently(worker, workers=2)
    wins = [o for o in outcomes if o.ok]
    losses = [o for o in outcomes if not o.ok]
    assert len(wins) == 1 and len(losses) == 1
    assert isinstance(losses[0].error, AppError) and losses[0].error.status_code == 409
    assert _scalar("SELECT count(*) FROM quotation_status_log WHERE to_status = 'ISSUED'") == 1


@pytest.mark.parametrize("round_no", range(8))
def test_issue_versus_edit_never_leaves_a_half_edited_frozen_quotation(round_no: int) -> None:
    """발행 vs 편집 동시(같은 version) — 한쪽만 성공하고, 발행이 이기면 최종 값은 발행 시점 값이다(편집이 스며들지 않는다)"""
    actor = _actor()
    qt = _draft(actor, create_buyer(name_en="Original Name"), create_priced_sku())

    def worker(i: int) -> Any:
        if i == 0:
            return (
                "issue",
                lifecycle.issue_quotation(
                    actor=actor,
                    idempotency_key=f"vs-{round_no}",
                    qt_id=qt["id"],
                    version=qt["version"],
                )[1],
            )
        return (
            "edit",
            quotations.update_quotation(
                actor=actor,
                qt_id=qt["id"],
                payload={"version": qt["version"], "buyer_name": "Edited Name"},
            ),
        )

    outcomes = run_concurrently(worker, workers=2)
    ok = [o.value[0] for o in outcomes if o.ok]
    assert len(ok) == 1, [(o.ok, o.error) for o in outcomes]
    row = _scalar("SELECT status || '|' || buyer_name FROM quotations WHERE id = :i", i=qt["id"])
    if ok == ["issue"]:
        assert row == "ISSUED|Original Name"  # 발행이 이겼다 — 편집은 409(version)로 거부
    else:
        assert row == "DRAFT|Edited Name"  # 편집이 이겼다 — 발행은 옛 version이라 409


def test_concurrent_line_adds_on_the_same_version_admit_only_one() -> None:
    """같은 version으로 라인 추가 5건 동시 → 1건 성공·4건 409, 합계=라인 합 유지(잃어버린 갱신 없음)"""
    actor = _actor()
    buyer = create_buyer()
    qt = _draft(actor, buyer, create_priced_sku(amount=100))
    skus = [create_priced_sku(amount=100) for _ in range(5)]

    def worker(i: int) -> Any:
        return quotations.add_line(
            actor=actor,
            qt_id=qt["id"],
            payload={"version": qt["version"], "sku_id": skus[i], "quantity": 1},
        )

    outcomes = run_concurrently(worker, workers=5)
    assert sum(1 for o in outcomes if o.ok) == 1
    assert all(
        isinstance(o.error, AppError) and o.error.status_code == 409 for o in outcomes if not o.ok
    )
    header_total = _scalar("SELECT total_amount FROM quotations WHERE id = :i", i=qt["id"])
    lines_total = _scalar(
        "SELECT sum(line_amount) FROM quotation_lines WHERE qt_id = :i AND deleted_at IS NULL",
        i=qt["id"],
    )
    assert header_total == lines_total == 200 + 100


def test_two_creations_copying_the_same_source_admit_only_one() -> None:
    """같은 원본을 동시에 복제하는 2요청 → 1건 201·1건 409(부분 유니크 위반이 500으로 새지 않는다)"""
    actor = _actor()
    buyer = create_buyer()
    source = _draft(actor, buyer, create_priced_sku())
    lifecycle.transition_quotation(
        actor=actor,
        idempotency_key="cancel-src",
        qt_id=source["id"],
        to="CANCELLED",
        version=source["version"],
        reason="복제 원본",
    )

    def worker(i: int) -> Any:
        return quotations.create_quotation(
            actor=actor,
            idempotency_key=f"copy-{i}",
            payload=_payload(buyer, copied_from_id=source["id"]),
        )

    outcomes = run_concurrently(worker, workers=2)
    assert sum(1 for o in outcomes if o.ok) == 1
    errors = [o.error for o in outcomes if not o.ok]
    assert len(errors) == 1 and isinstance(errors[0], AppError)
    assert errors[0].code == "TRADE_DOCS.COPY.SOURCE_NOT_ELIGIBLE"
    assert _scalar("SELECT count(*) FROM quotations WHERE copied_from_id = :s", s=source["id"]) == 1


def test_two_revisions_of_the_same_original_admit_only_one() -> None:
    """같은 원본의 개정 초안을 동시에 2번 → 1건 201·1건 409"""
    actor = _actor()
    buyer = create_buyer()
    draft = _draft(actor, buyer, create_priced_sku())
    _s, issued = lifecycle.issue_quotation(
        actor=actor, idempotency_key="rev-issue", qt_id=draft["id"], version=draft["version"]
    )

    def worker(i: int) -> Any:
        return lifecycle.create_revision(
            actor=actor, idempotency_key=f"rev-{i}", qt_id=issued["id"], version=issued["version"]
        )

    outcomes = run_concurrently(worker, workers=2)
    assert sum(1 for o in outcomes if o.ok) == 1
    assert all(
        isinstance(o.error, AppError) and o.error.status_code == 409 for o in outcomes if not o.ok
    )
    assert _scalar("SELECT count(*) FROM quotations WHERE copied_from_id = :s", s=issued["id"]) == 1


def test_a_held_row_lock_surfaces_as_lock_not_available_not_a_hang() -> None:
    """다른 트랜잭션이 견적 행을 잠근 상태에서 편집하면 무한 대기가 아니라 55P03(잠금 대기 초과)이 난다 — API는 409 LOCK_BUSY로 번역"""
    actor = _actor()
    qt = _draft(actor, create_buyer(), create_priced_sku())
    holder = owner_engine.connect()
    tx = holder.begin()
    try:
        holder.execute(text("SELECT 1 FROM quotations WHERE id = :i FOR UPDATE"), {"i": qt["id"]})
        with pytest.raises(DBAPIError) as caught, unit_of_work() as uow:
            uow.session.execute(text("SET LOCAL lock_timeout = '150ms'"))
            quotations.update_quotation(
                actor=actor,
                qt_id=qt["id"],
                payload={"version": qt["version"], "internal_note": "x"},
            )
        assert getattr(caught.value.orig, "sqlstate", None) == "55P03"
    finally:
        tx.rollback()
        holder.close()


def _issued(actor: AuthenticatedUser, buyer: int | None = None) -> dict[str, Any]:
    draft = _draft(actor, buyer or create_buyer(), create_priced_sku())
    _s, issued = lifecycle.issue_quotation(
        actor=actor, idempotency_key=unique("iss"), qt_id=draft["id"], version=draft["version"]
    )
    return issued


def _ready_revision(actor: AuthenticatedUser, original: dict[str, Any], key: str) -> dict[str, Any]:
    """개정 초안을 만들고 발행 가능하도록 유효기간을 채운다(개정 초안은 유효기간이 비어 시작한다)"""
    _s, revision = lifecycle.create_revision(
        actor=actor, idempotency_key=key, qt_id=original["id"], version=original["version"]
    )
    edited: dict[str, Any] = quotations.update_quotation(
        actor=actor,
        qt_id=revision["id"],
        payload={"version": revision["version"], "valid_until": today_kst() + timedelta(days=30)},
    )
    return edited


def _only_conflicts(outcomes: list[Any]) -> None:
    """실패는 전부 정의된 409(AppError)여야 한다 — 데드락·IntegrityError·500이 새면 실패"""
    for outcome in outcomes:
        if not outcome.ok:
            assert isinstance(outcome.error, AppError), repr(outcome.error)
            assert outcome.error.status_code == 409, outcome.error


@pytest.mark.parametrize("round_no", range(6))
def test_cancel_versus_revision_publish_cancels_the_original_exactly_once(round_no: int) -> None:
    """원본 취소 vs 개정 발행(원본을 함께 취소) 동시 — 데드락 없이, 원본 CANCELLED 이력은 정확히 1행·개정본은 발행됨 또는 초안"""
    actor = _actor()
    original = _issued(actor)
    revision = _ready_revision(actor, original, f"mk-rev-{round_no}")

    def worker(i: int) -> Any:
        if i == 0:
            return lifecycle.transition_quotation(
                actor=actor,
                idempotency_key=f"cx-{round_no}",
                qt_id=original["id"],
                to="CANCELLED",
                version=original["version"],
                reason="동시 취소",
            )
        return lifecycle.issue_quotation(
            actor=actor,
            idempotency_key=f"rp-{round_no}",
            qt_id=revision["id"],
            version=revision["version"],
        )

    outcomes = run_concurrently(worker, workers=2)
    _only_conflicts(outcomes)
    assert any(o.ok for o in outcomes)
    assert _scalar("SELECT status FROM quotations WHERE id = :i", i=original["id"]) == "CANCELLED"
    assert (
        _scalar(
            "SELECT count(*) FROM quotation_status_log WHERE quotation_id = :i"
            " AND from_status = 'ISSUED' AND to_status = 'CANCELLED'",
            i=original["id"],
        )
        == 1
    )
    revision_status = _scalar("SELECT status FROM quotations WHERE id = :i", i=revision["id"])
    assert revision_status == ("ISSUED" if outcomes[1].ok else "DRAFT")


@pytest.mark.parametrize("round_no", range(6))
def test_cancel_versus_issue_of_the_same_draft_admits_only_one(round_no: int) -> None:
    """같은 초안의 취소 vs 발행 동시(같은 version) — 정확히 1건 성공, 최종 상태와 이력이 승자와 일치"""
    actor = _actor()
    qt = _draft(actor, create_buyer(), create_priced_sku())

    def worker(i: int) -> Any:
        if i == 0:
            return (
                "cancel",
                lifecycle.transition_quotation(
                    actor=actor,
                    idempotency_key=f"cv-c-{round_no}",
                    qt_id=qt["id"],
                    to="CANCELLED",
                    version=qt["version"],
                    reason="동시 취소",
                ),
            )
        return (
            "issue",
            lifecycle.issue_quotation(
                actor=actor,
                idempotency_key=f"cv-i-{round_no}",
                qt_id=qt["id"],
                version=qt["version"],
            ),
        )

    outcomes = run_concurrently(worker, workers=2)
    _only_conflicts(outcomes)
    winners = [o.value[0] for o in outcomes if o.ok]
    assert len(winners) == 1, [(o.ok, o.error) for o in outcomes]
    expected = "CANCELLED" if winners == ["cancel"] else "ISSUED"
    assert _scalar("SELECT status FROM quotations WHERE id = :i", i=qt["id"]) == expected
    assert (
        _scalar(
            "SELECT count(*) FROM quotation_status_log WHERE quotation_id = :i"
            " AND from_status = 'DRAFT' AND to_status IN ('CANCELLED', 'ISSUED')",
            i=qt["id"],
        )
        == 1
    )


def test_revision_double_click_with_the_same_key_creates_one_draft() -> None:
    """개정 더블클릭(같은 멱등 키 동시 2요청) → 둘 다 같은 응답·개정 초안 1건(멱등 claim 직렬화)"""
    actor = _actor()
    original = _issued(actor)

    def worker(_i: int) -> Any:
        return lifecycle.create_revision(
            actor=actor,
            idempotency_key="dbl-rev",
            qt_id=original["id"],
            version=original["version"],
        )

    outcomes = run_concurrently(worker, workers=2)
    assert all(o.ok for o in outcomes), [o.error for o in outcomes]
    assert outcomes[0].value == outcomes[1].value
    assert (
        _scalar("SELECT count(*) FROM quotations WHERE copied_from_id = :s", s=original["id"]) == 1
    )


# ── 잠금 순서 계측 — LOCK_ORDER(idempotency→partners→quotations→lines→채번)를 실제 SQL로 확인 ──────────

_LOCK_TABLES = {
    "idempotency_keys": "idempotency_keys",
    "partners": "partners",
    "quotations": "quotations",
    "quotation_lines": "lines",
    "doc_number_seq": "doc_number_seq",
}
_LOCK_CLAUSE = re.compile(r"\bFOR (?:NO KEY )?(?:UPDATE|SHARE|KEY SHARE)\b", re.IGNORECASE)
_WRITE_LOCK = re.compile(r"^\s*(?:INSERT INTO|UPDATE)\s+(\w+)", re.IGNORECASE)
_FROM = re.compile(r"\bFROM\s+(\w+)", re.IGNORECASE)


def _first_lock_sequence(work: Callable[[], object]) -> list[str]:
    """work가 실행하는 SQL에서 LOCK_ORDER 대상의 **첫 접촉** 순서(행 잠금 절 또는 멱등·채번 쓰기)를 기록한다."""
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


def test_the_lock_order_probe_sees_locks_and_flags_a_reversed_order() -> None:
    """공회전 방지 — 계측이 잠금을 실제로 기록하고, 뒤집힌 순서는 위반으로 판정한다"""
    actor = _actor()
    seen = _first_lock_sequence(
        lambda: quotations.create_quotation(
            actor=actor,
            idempotency_key=unique("probe"),
            payload=_payload(create_buyer(), create_priced_sku()),
        )
    )
    assert "partners" in seen and "doc_number_seq" in seen and "idempotency_keys" in seen, seen
    with pytest.raises(AssertionError):
        _assert_follows_lock_order(["quotations", "partners"])


def test_every_quotation_operation_takes_locks_in_the_documented_order() -> None:
    """작성·바이어 변경 편집·발행·개정 초안·개정 발행·취소·라인 추가가 partners→quotations→lines→채번 순으로만 잠근다"""
    actor = _actor()
    buyer, other_buyer = create_buyer(), create_buyer()
    sku, extra_sku = create_priced_sku(), create_priced_sku()
    holder: dict[str, Any] = {}

    def create() -> None:
        _s, holder["qt"] = quotations.create_quotation(
            actor=actor, idempotency_key=unique("lo-c"), payload=_payload(buyer, sku)
        )

    _assert_follows_lock_order(_first_lock_sequence(create))
    qt = holder["qt"]

    def edit_buyer() -> None:
        holder["qt"] = quotations.update_quotation(
            actor=actor,
            qt_id=qt["id"],
            payload={"version": qt["version"], "buyer_partner_id": other_buyer},
        )

    seen = _first_lock_sequence(edit_buyer)
    assert seen[:2] == ["partners", "quotations"], seen  # 바뀔 바이어를 QT보다 먼저 잠근다
    _assert_follows_lock_order(seen)
    qt = holder["qt"]

    def add_line() -> None:
        quotations.add_line(
            actor=actor,
            qt_id=qt["id"],
            payload={"version": qt["version"], "sku_id": extra_sku, "quantity": 1},
        )

    _assert_follows_lock_order(_first_lock_sequence(add_line))
    qt = quotations.get_quotation(qt["id"])

    def issue() -> None:
        _s, holder["issued"] = lifecycle.issue_quotation(
            actor=actor, idempotency_key=unique("lo-i"), qt_id=qt["id"], version=qt["version"]
        )

    _assert_follows_lock_order(_first_lock_sequence(issue))
    issued = holder["issued"]

    def revise() -> None:
        _s, holder["rev"] = lifecycle.create_revision(
            actor=actor,
            idempotency_key=unique("lo-r"),
            qt_id=issued["id"],
            version=issued["version"],
        )

    _assert_follows_lock_order(_first_lock_sequence(revise))
    revision = quotations.update_quotation(
        actor=actor,
        qt_id=holder["rev"]["id"],
        payload={
            "version": holder["rev"]["version"],
            "valid_until": today_kst() + timedelta(days=30),
        },
    )

    def publish() -> None:
        lifecycle.issue_quotation(
            actor=actor,
            idempotency_key=unique("lo-p"),
            qt_id=revision["id"],
            version=revision["version"],
        )

    seen = _first_lock_sequence(publish)  # 개정 발행: 멱등 → 바이어 → 원본 QT → 본 QT
    assert seen[:3] == ["idempotency_keys", "partners", "quotations"], seen
    _assert_follows_lock_order(seen)

    draft = _draft(actor, buyer, sku)

    def cancel() -> None:
        lifecycle.transition_quotation(
            actor=actor,
            idempotency_key=unique("lo-x"),
            qt_id=draft["id"],
            to="CANCELLED",
            version=draft["version"],
            reason="순서 점검",
        )

    _assert_follows_lock_order(_first_lock_sequence(cancel))
