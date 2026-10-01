"""H·J·K. 승인 적대 검토 반영 — 무권한 호출의 잠금·audit 비소모·요청 유지·provider SAVEPOINT·무결성 대사·마이그레이션 결합 (S3-1 PR-9a / ADR-0060 부기).

승인 통제 PR의 fail-closed 보강: ① 권한은 잠금·노출 재계산 **전에** 무잠금으로 판정(무권한 호출이 잠금 경합을 만들지 못함·그 승인과 무관한 사용자에게는 존재 여부도 비노출)
② 거부 audit는 합산(소모 불가) ③ 같은 digest에서 노출이 줄었다고 멀쩡한 승인을 VOID하지 못함 ④ provider DB 오류가 트랜잭션을 망가뜨리지 않음 ⑤ 위조 탐지 대사.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.db.session import build_engine, owner_engine
from app.core.db.uow import unit_of_work
from app.core.errors.codes import ErrorCode
from app.core.errors.exceptions import AppError
from app.modules.approvals import integrity, service
from app.modules.approvals.machine import ConsumeOutcome
from app.modules.credit import providers
from app.modules.credit.evaluation import CreditVerdict
from app.modules.credit.locking import lock_buyer_for_credit
from app.modules.credit.providers import ReceivableTerm
from app.modules.identity.models import RoleCode
from tests.factories.approvals import (
    TYPE,
    add_line,
    approval_row,
    approved_for,
    audit_actions,
    count,
    credit_so,
    decide,
    evaluate,
    make_user,
    raw_open_so,
    request,
    set_credit_limit,
)
from tests.factories.trade import create_buyer, unique
from tests.support.concurrency import run_concurrently

pytestmark = pytest.mark.group_h


def _pending() -> tuple[dict[str, Any], int, Any]:
    so = credit_so()
    if count("approval_lines") == 0:
        add_line(0, role="TRADE")
    requester = make_user(RoleCode.TRADE)
    make_user(RoleCode.TRADE)
    return so, int(request(so["id"], requester).approval.id), requester


# ── ① 권한 먼저·무잠금 ───────────────────────────────────────────────────────


def test_an_unauthorised_call_does_not_wait_for_the_locks_it_must_not_take() -> None:
    """다른 세션이 거래처·SO·승인 행을 잠근 상태에서도 무권한(CERT)·기안자 본인(SELF) 호출은 **대기 없이 즉시** 거부된다(잠금 경합·DoS 표면 제거 — lock_timeout 5초를 기다리지 않는다)"""
    so, approval_id, requester = _pending()
    outsider = make_user(RoleCode.CERT)
    holding, release = threading.Event(), threading.Event()

    def holder() -> None:
        with unit_of_work() as uow:
            lock_buyer_for_credit(uow.session, so["buyer_partner_id"])
            uow.session.execute(
                text("SELECT id FROM sales_orders WHERE id = :i FOR UPDATE"), {"i": so["id"]}
            )
            uow.session.execute(
                text("SELECT id FROM approvals WHERE id = :i FOR UPDATE"), {"i": approval_id}
            )
            holding.set()
            release.wait(timeout=30)

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert holding.wait(timeout=10)
        for actor, code in (
            (outsider, ErrorCode.RESOURCE_NOT_FOUND),
            (requester, ErrorCode.APPROVALS_DECISION_SELF_APPROVAL),
        ):
            started = time.monotonic()
            with pytest.raises(AppError) as caught:
                decide(approval_id, actor, "APPROVE", version=1)
            assert time.monotonic() - started < 2.0, "무권한 호출이 잠금을 기다렸다"
            assert caught.value.code == code
    finally:
        release.set()
        thread.join(timeout=30)


def test_an_unrelated_user_learns_nothing_about_the_approval() -> None:
    """무관한 사용자의 호출은 존재하는 승인과 없는 id가 **같은 404 응답**이다 — 상태(current)·version 충돌 정보가 새지 않는다(낡은 version·잘못된 동사로 찔러도 동일)"""
    _, approval_id, _ = _pending()
    outsider = make_user(RoleCode.LOGISTICS)
    responses = []
    for target in (approval_id, 987654):
        for verb, version in (("APPROVE", 1), ("WITHDRAW", 99), ("REJECT", 1)):
            with pytest.raises(AppError) as caught:
                decide(target, outsider, verb, reason="x", version=version)
            responses.append(
                (
                    caught.value.code,
                    caught.value.status_code,
                    caught.value.detail,
                    caught.value.message,
                )
            )
    assert len(set(map(str, responses))) == 1 and responses[0][1] == 404 and responses[0][2] == {}


def test_a_stale_state_conflict_is_only_visible_to_an_authorised_party() -> None:
    """이미 승인된 건에 대한 409(현재 상태 detail)는 그 승인의 당사자(결재 자격자)에게만 간다"""
    _, approval_id, _ = _pending()
    approver = make_user(RoleCode.TRADE)
    decide(approval_id, approver, "APPROVE")
    with pytest.raises(AppError) as caught:
        decide(approval_id, make_user(RoleCode.TRADE), "APPROVE", version=2)
    assert caught.value.code == ErrorCode.APPROVALS_TRANSITION_NOT_ALLOWED
    assert caught.value.detail["current"] == "APPROVED"


# ── ② audit 합산 ─────────────────────────────────────────────────────────────


def test_repeated_denied_attempts_add_one_audit_row_and_outsiders_add_none() -> None:
    """같은 (행위자·승인·동사·사유)의 거부는 audit 1건으로 합산한다(키를 바꿔 반복해도 소모 불가) — 무관한 사용자는 audit 0건(404)"""
    _, approval_id, requester = _pending()
    outsider = make_user(RoleCode.CERT)
    for _ in range(5):
        for actor in (requester, outsider):
            with pytest.raises(AppError):
                decide(approval_id, actor, "APPROVE", version=1, key=unique("k"))
    rows = audit_actions("approvals.decision.denied")
    assert len(rows) == 1 and rows[0]["actor_user_id"] == requester.id
    with pytest.raises(AppError):
        decide(approval_id, requester, "REJECT", reason="r", version=1)  # 다른 동사는 별도 1건
    assert len(audit_actions("approvals.decision.denied")) == 2


# ── ⑤ 같은 스냅샷 — 상한 이하면 유지 ───────────────────────────────────────────


def test_a_request_with_a_smaller_excess_keeps_the_existing_approval() -> None:
    """같은 digest에서 현재 초과분이 승인 상한 이하이면(승인 후 노출 감소) 재요청은 기존 승인(APPROVED)을 그대로 돌려준다 — 타인이 VOID하지 못한다. 상한 초과면 새 요청"""
    so = credit_so()
    approval_id, _, _ = approved_for(so)
    set_credit_limit(
        so["buyer_partner_id"], 150_000
    )  # 초과분 100,000 → 50,000(상한 이하, digest 동일)
    again = request(so["id"], make_user(RoleCode.TRADE))
    assert again.created is False and again.approval.id == approval_id
    assert approval_row(approval_id)["status"] == "APPROVED"
    set_credit_limit(
        so["buyer_partner_id"], 50_000
    )  # 초과분 150,000 > 상한 → 새 요청(이전 승인 VOID)
    third = request(so["id"], make_user(RoleCode.TRADE))
    assert third.created is True and approval_row(approval_id)["status"] == "VOIDED"


# ── ⑥⑦ provider ──────────────────────────────────────────────────────────────


@pytest.fixture
def _provider_reset() -> Any:
    providers.reset_receivable_provider_for_tests()
    yield
    providers.reset_receivable_provider_for_tests()


def test_a_provider_database_error_does_not_poison_the_transaction(_provider_reset: None) -> None:
    """provider가 DB 오류(SQLAlchemyError)를 내도 SAVEPOINT가 격리한다 — 평가는 UNEVALUABLE이고 같은 트랜잭션의 이후 쿼리가 성공한다(aborted 상태가 아니다)"""

    class BadSql:
        def outstanding(self, session: Any, partner_id: int, limit_currency: str) -> ReceivableTerm:
            session.execute(text("SELECT * FROM table_that_does_not_exist"))
            raise AssertionError("도달 불가")

    providers.register_receivable_provider(BadSql())
    buyer = create_buyer()
    set_credit_limit(buyer, 100_000)
    so_id = raw_open_so(buyer, status="RECEIVED", total=10)
    from app.modules.credit import evaluation
    from app.modules.sales_orders.models import SalesOrder

    with unit_of_work() as uow:
        locked = lock_buyer_for_credit(uow.session, buyer)
        order = uow.session.get(SalesOrder, so_id)
        assert order is not None
        result = evaluation.evaluate_credit(uow.session, locked, order)
        assert result.verdict is CreditVerdict.UNEVALUABLE
        assert "RECEIVABLE_PROVIDER_ERROR" in result.reason_codes
        assert uow.session.execute(text("SELECT 1")).scalar_one() == 1  # 트랜잭션이 살아 있다


def test_programming_errors_in_a_provider_are_not_swallowed(_provider_reset: None) -> None:
    """좁은 예외만 평가 불능이다 — provider의 프로그래밍 오류(TypeError)는 삼키지 않고 올라온다(500으로 드러남)"""

    class Buggy:
        def outstanding(self, session: Any, partner_id: int, limit_currency: str) -> ReceivableTerm:
            raise TypeError("버그")

    providers.register_receivable_provider(Buggy())
    buyer = create_buyer()
    set_credit_limit(buyer, 100_000)
    with pytest.raises(TypeError):
        evaluate(raw_open_so(buyer, status="RECEIVED", total=10))
    assert issubclass(SQLAlchemyError, Exception)


def test_a_negative_receivable_is_rejected_and_makes_the_evaluation_unevaluable(
    _provider_reset: None,
) -> None:
    """미수 금액은 0 이상이다 — 음수는 값 객체가 거부하고(ValueError) provider가 그걸 내면 노출을 줄이는 방향으로 오염되지 않고 UNEVALUABLE이다"""
    with pytest.raises(ValueError):
        ReceivableTerm(reflected=True, amount=-1)

    class Negative:
        def outstanding(self, session: Any, partner_id: int, limit_currency: str) -> ReceivableTerm:
            return ReceivableTerm(reflected=True, amount=-50_000)

    providers.register_receivable_provider(Negative())
    buyer = create_buyer()
    set_credit_limit(buyer, 100_000)
    result = evaluate(raw_open_so(buyer, status="RECEIVED", total=100_001))
    assert result.verdict is CreditVerdict.UNEVALUABLE


# ── ⑧ peek 최신 상태 ─────────────────────────────────────────────────────────


def test_peek_reads_the_fresh_row_not_a_stale_cached_one() -> None:
    """같은 세션이 APPROVED로 캐시한 승인을 다른 트랜잭션이 회수한 뒤 peek하면 최신 상태(None)를 본다 — populate_existing"""
    so = credit_so()
    approval_id, requester, _ = approved_for(so)
    with unit_of_work() as uow:
        assert (
            service.peek_active_approved(uow.session, approval_type=TYPE, target_id=so["id"])
            is not None
        )
        decide(
            approval_id, requester, "WITHDRAW", reason="철회", version=2
        )  # 다른 UoW(자기 트랜잭션)가 커밋
        # 같은 세션의 identity map에는 옛 APPROVED 객체가 남아 있다
        assert (
            service.peek_active_approved(uow.session, approval_type=TYPE, target_id=so["id"])
            is None
        )
    assert approval_row(approval_id)["status"] == "WITHDRAWN"


# ── ⑨ 승인×회수 동시 ─────────────────────────────────────────────────────────


@pytest.mark.group_j
def test_approving_and_withdrawing_at_once_yield_exactly_one_success() -> None:
    """승인과 회수의 동시 경합 — 같은 version을 든 두 요청 중 정확히 1건만 성공(나머지 409), 이력은 결정 1행·최종 상태 일관(교착·500 없음)"""
    for _ in range(5):
        _, approval_id, requester = _pending()
        approver = make_user(RoleCode.TRADE)

        def worker(i: int, _id: int = approval_id, _a: Any = approver, _r: Any = requester) -> Any:
            if i == 0:
                return decide(_id, _a, "APPROVE", version=1)
            return decide(_id, _r, "WITHDRAW", reason="회수", version=1)

        outcomes = run_concurrently(worker, workers=2)
        assert all(o.error is None or isinstance(o.error, AppError) for o in outcomes), [
            o.error for o in outcomes
        ]
        assert sum(o.error is None for o in outcomes) == 1
        row = approval_row(approval_id)
        events = count("approval_events", "approval_id = :i", i=approval_id)
        assert events == 2 and row["status"] in ("APPROVED", "WITHDRAWN")


# ── ④(c) 무결성 대사 ─────────────────────────────────────────────────────────


def test_integrity_is_clean_for_every_legitimate_flow() -> None:
    """정상 흐름(요청·승인·소비·반려·회수·무효·대결)만 있는 DB는 대사 결과가 빈 목록이다 — 승인 행이 이력으로 재구성된다"""
    from app.modules.approvals.machine import VoidReasonCode

    so1 = credit_so()
    approved_for(so1)
    with unit_of_work() as uow:
        service.consume_approval(
            uow.session, approval_type=TYPE, target_id=so1["id"], actor_user_id=1
        )
    so2, a2, _ = _pending()
    decide(a2, make_user(RoleCode.TRADE), "REJECT", reason="no")
    so3, a3, r3 = _pending()
    decide(a3, r3, "WITHDRAW", reason="w")
    so4, _a4, _ = _pending()
    with unit_of_work() as uow:
        service.void_for_target(
            uow.session,
            approval_type=TYPE,
            target_id=so4["id"],
            actor_user_id=1,
            reason_code=VoidReasonCode.TARGET_CANCELLED,
        )
    assert count("approvals") == 4 and so2 and so3
    with unit_of_work() as uow:
        assert integrity.check_integrity(uow.session) == []


@pytest.mark.parametrize(
    ("tamper", "expected"),
    [
        (
            "UPDATE approvals SET status = 'APPROVED', decided_by_id = {u}, decided_at = now() WHERE id = {i}",
            "STATUS_MISMATCH",
        ),
        ("DELETE FROM approval_events WHERE approval_id = {i}", "NO_EVENTS"),
        ("UPDATE approvals SET decided_by_id = {u} WHERE id = {i}", "DECISION_MISMATCH"),
    ],
    ids=["이벤트 없는 상태 위조", "이력 소거", "결정자 바꿔치기"],
)
def test_integrity_detects_forged_rows(tamper: str, expected: str) -> None:
    """이력 없이 상태·결정자를 바꾸거나 이력을 지운 행(소유자 권한 위조 시뮬레이션)을 대사가 짚어낸다 — DB 권한만으로는 막지 못하는 구멍의 탐지 층"""
    _, approval_id, requester = _pending()
    other = make_user(RoleCode.TRADE)
    if "decided_by_id = {u}" in tamper and "status" not in tamper:
        decide(approval_id, make_user(RoleCode.TRADE), "APPROVE")
    with owner_engine.begin() as connection:
        connection.execute(text(tamper.format(u=other.id, i=approval_id)))
    with unit_of_work() as uow:
        found = integrity.check_integrity(uow.session)
    assert {"approval_id": approval_id, "problem": expected} in found, found
    assert requester.id


# ── ③ 마이그레이션 결합 제거 ──────────────────────────────────────────────────


@pytest.mark.group_k
def test_changing_the_app_constant_does_not_break_migration_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """앱 상수 `COLUMN_UPDATE_ALLOWLIST`에 컬럼을 임의로 더해도 신규 DB `alembic upgrade head`가 깨지지 않고, DB 권한은 **마이그레이션이 고정한 리터럴**대로다"""
    from alembic import command
    from alembic.config import Config

    from app.core.config import settings
    from app.core.db import table_policy
    from tests.conftest import ALEMBIC_INI

    assert settings.migration_check_database_url is not None
    url = settings.migration_check_database_url.get_secret_value()
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", url)
    changed = dict(table_policy.COLUMN_UPDATE_ALLOWLIST)
    changed["approvals"] = changed["approvals"] | {"basis_amount"}
    monkeypatch.setattr(table_policy, "COLUMN_UPDATE_ALLOWLIST", changed)
    config = Config(str(ALEMBIC_INI))
    command.downgrade(config, "base")
    command.upgrade(config, "head")  # 상수가 달라도 예외 없이 재생된다
    engine = build_engine(url)
    try:
        with engine.connect() as connection:
            granted = {
                column: connection.execute(
                    text("SELECT has_column_privilege('kbos_app', 'approvals', :c, 'UPDATE')"),
                    {"c": column},
                ).scalar_one()
                for column in ("basis_amount", "status")
            }
    finally:
        engine.dispose()
    assert granted == {"basis_amount": False, "status": True}


@pytest.mark.group_k
def test_the_migration_and_the_helper_do_not_depend_on_the_app_constant() -> None:
    """마이그레이션 파일이 앱 상수를 임포트·참조하지 않고(리터럴 고정), 헬퍼도 상수와의 일치를 검사하지 않는다 — 일치 검증은 has_column_privilege 전수 테스트의 몫"""
    import inspect
    from pathlib import Path

    migration = next(
        Path(__file__).resolve().parents[2].glob("migrations/versions/*approvals_core.py")
    )
    source = migration.read_text(encoding="utf-8")
    assert (
        "COLUMN_UPDATE_ALLOWLIST[" not in source and "import COLUMN_UPDATE_ALLOWLIST" not in source
    )
    assert "frozenset(" in source
    from app.core.db.table_policy import restrict_update_columns

    assert "COLUMN_UPDATE_ALLOWLIST.get" not in inspect.getsource(restrict_update_columns)
    assert ConsumeOutcome.BLOCKED  # 가드
