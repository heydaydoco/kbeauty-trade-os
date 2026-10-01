"""A. 게이트 평가 틀 — 실 DB에서의 SAVEPOINT 격리·잠금 경합 전파·조회 무저장 (S3-1 PR-11a / design-D D3).

단위 테스트(`tests/unit/test_gate_core.py`)는 가짜 세션으로 미등록·예외·계약 위반을 본다. 여기서는 **진짜 트랜잭션**에서: ① 평가기의 SQL 오류가 호출 트랜잭션을 망가뜨리지 않고(UNKNOWN +
세션 계속 사용 가능) ② 실제 `lock_timeout`(55P03)은 삼켜지지 않고 전파되며 ③ 평가가 아무것도 쓰지 않는다는 것을 확인한다.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from app.core.db.session import owner_engine
from app.core.db.uow import unit_of_work
from app.modules.gates import service as gates_service
from app.modules.gates.registry import EvaluatorRegistry
from app.modules.gates.types import GateCode, GateLevel, GateOutcome, GatePhase, GateResolution
from app.modules.outbox.models import Event
from app.modules.sales_orders.models import SalesOrder
from tests.factories.gates import passing_so, scalar
from tests.integration.test_gate_evaluators import _subject
from tests.support.gate_coverage import register

pytestmark = pytest.mark.group_a

for _gate in GateCode:
    register(
        _gate, GateLevel.UNKNOWN, GateResolution.NONE
    )  # 7종 전수 — 아래 SQL 오류·미등록 테스트가 게이트별로 UNKNOWN/NONE을 확인


def _registry(**evaluators: Any) -> EvaluatorRegistry:
    registry = EvaluatorRegistry()
    for name, function in evaluators.items():
        registry.register(GateCode(name), function)
    return registry


def test_a_sql_error_inside_an_evaluator_becomes_unknown_and_the_transaction_survives() -> None:
    """평가기 안의 SQL 오류(0 나눗셈)는 SAVEPOINT에서 롤백돼 UNKNOWN(EVALUATION_ERROR)이 되고, 호출 트랜잭션은 계속 쓸 수 있다 — 평가 전에 쓴 행이 남고 이후 쿼리·커밋이 된다"""
    so = passing_so()
    subject = _subject(so["id"])

    def broken(session: Any, _subject: Any, _phase: Any) -> list[GateOutcome]:
        session.execute(text("SELECT 1/0"))
        return []

    with unit_of_work() as uow:
        uow.session.add(Event(event_type="gate.test.before", payload={}))
        uow.session.flush()
        results = gates_service.evaluate_all(
            uow.session,
            subject,
            GatePhase.CONFIRM,
            registry=_registry(MOQ=broken),
            only=[GateCode.MOQ],
        )
        assert [(o.level, o.reason_code) for o in results] == [
            (GateLevel.UNKNOWN, "EVALUATION_ERROR")
        ]
        assert results[0].basis == {"error_class": "DataError"}
        # 트랜잭션이 aborted 상태가 아니다 — 이어서 쿼리하고 쓸 수 있다
        assert uow.session.execute(select(SalesOrder.id)).first() is not None
        uow.session.add(Event(event_type="gate.test.after", payload={}))
    assert scalar("SELECT count(*) FROM events WHERE event_type LIKE 'gate.test.%'") == 2


def test_a_real_lock_timeout_propagates_and_is_not_swallowed_as_unknown() -> None:
    """다른 트랜잭션이 SO 행을 쥔 동안 평가기가 `FOR UPDATE`로 대기하다 lock_timeout(55P03)이 나면 예외가 그대로 전파된다 — UNKNOWN으로 둔갑해 평가 불능 표시로 흘러가지 않는다(409 LOCK_BUSY 번역 대상)"""
    so = passing_so()
    subject = _subject(so["id"])
    held = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with owner_engine.begin() as connection:
            connection.execute(
                text("SELECT id FROM sales_orders WHERE id = :i FOR UPDATE"), {"i": so["id"]}
            )
            held.set()
            release.wait(timeout=20)

    thread = threading.Thread(target=holder)
    thread.start()
    try:
        assert held.wait(timeout=10)

        def waits_for_the_row(session: Any, _subject: Any, _phase: Any) -> list[GateOutcome]:
            session.execute(text("SET LOCAL lock_timeout = '150ms'"))
            session.execute(
                text("SELECT id FROM sales_orders WHERE id = :i FOR UPDATE"), {"i": so["id"]}
            )
            return []

        with pytest.raises(OperationalError) as caught, unit_of_work() as uow:
            gates_service.evaluate_all(
                uow.session,
                subject,
                GatePhase.CONFIRM,
                registry=_registry(MOQ=waits_for_the_row),
                only=[GateCode.MOQ],
            )
        assert getattr(caught.value.orig, "sqlstate", None) == "55P03"
    finally:
        release.set()
        thread.join(timeout=20)


def test_evaluating_a_sales_order_writes_nothing() -> None:
    """SO 평가(7종 전건·참고값 포함)는 어떤 테이블에도 쓰지 않는다 — 증거 스냅샷·override·이벤트·감사 로그 행이 평가 전후로 같다(조회 부작용 금지)"""
    so = passing_so()
    tables = ("gate_evaluations", "gate_overrides", "events", "audit_log", "sales_orders")

    def counts() -> dict[str, int]:
        return {t: int(scalar(f"SELECT count(*) FROM {t}")) for t in tables}

    before = counts()
    from tests.factories.gates import evaluate

    evaluate(so["id"])
    evaluate(so["id"], authoritative=True)
    assert counts() == before


def test_a_subject_of_another_kind_is_unknown_for_every_registered_gate() -> None:
    """SO 평가기에 SO가 아닌 대상(예: 인테이크·채널 리스팅)을 넘기면 7종 모두 UNKNOWN — 모르는 대상 종류가 PASS가 되지 않는다(평가기가 대상 종류를 검사)"""
    import dataclasses

    from app.modules.trade_chain import gate_evaluators

    so = passing_so()
    subject = dataclasses.replace(_subject(so["id"]), kind="CHANNEL_LISTING")
    registry = EvaluatorRegistry()
    gate_evaluators.register_evaluators(registry)
    with unit_of_work() as uow:
        results = gates_service.evaluate_all(
            uow.session, subject, GatePhase.CONFIRM, registry=registry
        )
    assert len(results) == 7
    assert {(o.level, o.reason_code) for o in results} == {(GateLevel.UNKNOWN, "EVALUATION_ERROR")}


def test_the_global_registry_has_all_seven_evaluators_after_app_assembly() -> None:
    """앱 조립(라우터 임포트)이 끝나면 전역 등록부에 7종 평가기가 전부 있다 — 하나라도 빠지면 그 게이트는 UNKNOWN이라 확정이 막힌다(배포 결함이 드러난다)"""
    import app.main  # noqa: F401  (라우터 임포트가 평가기를 등록한다)
    from app.modules.gates.registry import DEFAULT_REGISTRY

    assert DEFAULT_REGISTRY.codes() == {g.value for g in GateCode}
