"""J. 게이트 override 동시성 — 더블클릭=1건·동시 부여 직렬화·부여×철회 최종 일관성·잠금 순서 (S3-1 PR-11a / design-D D3 (f) J).

★ 순차 실행은 증거가 아니다 — 실제 스레드를 Barrier로 동시에 출발시킨다(tests/support/concurrency.py). 각 스레드는 자기 세션을 연다. 이 파일은 커밋을 동반한다.
override 오케스트레이터는 **멱등 선점 → SO 행 `FOR UPDATE` → 평가 → 불변 INSERT** 순서이고, SO 잠금이 같은 SO의 부여·철회·(PR-12) 확정을 직렬화한다.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import event, text

from app.core.db.session import engine, owner_engine
from app.modules.gates import service as gates_service
from app.modules.gates.models import SUBJECT_SALES_ORDER
from app.modules.gates.types import GateCode
from app.modules.identity.models import RoleCode
from app.modules.trade_chain import gate_flow
from tests.factories.gates import evaluate, line_ids, one, passing_so, scalar, set_line, set_policy
from tests.factories.payments import actor
from tests.factories.trade import unique
from tests.support.concurrency import run_concurrently

pytestmark = [pytest.mark.group_j, pytest.mark.concurrency]


def _price_block() -> tuple[dict[str, Any], int, str]:
    """단가 편차 BLOCK SO → (SO, 라인 id, 판정 해시)."""
    so = passing_so(price=1000)
    set_policy("price_deviation_tolerance_bp", 500)
    set_line(so["id"], 1, unit_price=1100, list_price=1000)
    (line,) = line_ids(so["id"])
    return so, line, one(evaluate(so["id"]), GateCode.PRICE_DEVIATION, line).basis_hash


def _body(line: int, basis_hash: str, reason: str = "동시성 시험 사유") -> dict[str, Any]:
    return {
        "gate_code": "PRICE_DEVIATION",
        "line_id": line,
        "basis_hash": basis_hash,
        "reason": reason,
    }


def _grant(who: Any, so_id: int, body: dict[str, Any], *, key: str | None = None) -> Any:
    return gate_flow.grant_gate_override(
        actor=who, idempotency_key=key or unique("conc"), so_id=so_id, payload=dict(body)
    )


def _revoke(who: Any, so_id: int, body: dict[str, Any]) -> Any:
    return gate_flow.revoke_gate_override(
        actor=who, idempotency_key=unique("conc"), so_id=so_id, payload=dict(body)
    )


def test_double_click_with_the_same_key_creates_exactly_one_override() -> None:
    """같은 Idempotency-Key·같은 본문을 동시에 2번 → 부여 행 1건·이벤트 1건·감사 1건, 두 응답은 같은 최초 결과(뒤늦은 요청은 재생)"""
    so, line, digest = _price_block()
    who = actor(RoleCode.TRADE)
    key = unique("dbl")
    body = _body(line, digest)
    outcomes = run_concurrently(lambda _i: _grant(who, so["id"], body, key=key), workers=2)
    assert all(o.ok for o in outcomes), [o.error for o in outcomes]
    assert outcomes[0].value == outcomes[1].value
    assert scalar("SELECT count(*) FROM gate_overrides") == 1
    assert scalar("SELECT count(*) FROM events WHERE event_type = 'gates.override.granted'") == 1
    assert scalar("SELECT count(*) FROM audit_log WHERE action = 'gates.override.granted'") == 1


@pytest.mark.parametrize("round_no", range(3))
def test_many_concurrent_grants_yield_exactly_one_and_the_rest_are_already_granted(
    round_no: int,
) -> None:
    """같은 SO·같은 판정에 서로 다른 키로 8건 동시 부여 → SO 잠금이 직렬화해 정확히 1건만 성공하고 나머지 7건은 409 ALREADY_GRANTED(500·교착 0) — 부여 행·이벤트·감사 모두 1건, 유효 override 한 키"""
    from app.core.errors.exceptions import AppError

    so, line, digest = _price_block()
    people = [actor(RoleCode.TRADE) for _ in range(8)]
    outcomes = run_concurrently(
        lambda i: _grant(people[i], so["id"], _body(line, digest)), workers=8
    )
    ok = [o for o in outcomes if o.ok]
    failed = [o.error for o in outcomes if not o.ok]
    assert len(ok) == 1, [repr(e) for e in failed]
    assert all(
        isinstance(e, AppError) and e.code.value == "GATES.OVERRIDE.ALREADY_GRANTED" for e in failed
    )
    assert scalar("SELECT count(*) FROM gate_overrides") == 1
    assert scalar("SELECT count(*) FROM events WHERE event_type = 'gates.override.granted'") == 1
    assert scalar("SELECT count(*) FROM audit_log WHERE action = 'gates.override.granted'") == 1
    from app.core.db.uow import unit_of_work

    with unit_of_work() as uow:
        effective = gates_service.effective_overrides(uow.session, SUBJECT_SALES_ORDER, so["id"])
    assert list(effective) == [("PRICE_DEVIATION", line, digest)]


@pytest.mark.parametrize("round_no", range(4))
def test_concurrent_revoke_and_regrant_end_in_a_state_equal_to_the_last_row(round_no: int) -> None:
    """선부여된 판정에 철회와 재부여(둘 다 ADMIN)를 동시에: 재부여가 먼저 잠금을 잡으면 이미 유효해 409, 철회가 먼저면 재부여 성공 — 어느 쪽이든 500·교착 0이고 최종 유효 상태 = 마지막 행의 action이다"""
    from app.core.errors.exceptions import AppError

    so, line, digest = _price_block()
    admin = actor(RoleCode.ADMIN)
    assert _grant(admin, so["id"], _body(line, digest))[0] == 201

    def worker(index: int) -> Any:
        if index == 0:
            return _revoke(admin, so["id"], _body(line, digest, "철회 사유입니다"))
        return _grant(admin, so["id"], _body(line, digest, "재부여 사유입니다"))

    outcomes = run_concurrently(worker, workers=2)
    for o in outcomes:
        assert o.ok or (
            isinstance(o.error, AppError) and o.error.code.value == "GATES.OVERRIDE.ALREADY_GRANTED"
        ), repr(o.error)
    rows = scalar("SELECT json_agg(action ORDER BY id) FROM gate_overrides")
    from app.core.db.uow import unit_of_work

    with unit_of_work() as uow:
        effective = gates_service.effective_overrides(uow.session, SUBJECT_SALES_ORDER, so["id"])
    assert (("PRICE_DEVIATION", line, digest) in effective) == (rows[-1] == "GRANT")
    assert rows in (["GRANT", "REVOKE", "GRANT"], ["GRANT", "REVOKE"])


def test_the_override_flow_takes_the_idempotency_claim_then_the_order_lock_then_writes() -> None:
    """잠금 순서(LOCK_ORDER): 멱등 키 선점 → SO 행 `FOR UPDATE` → 평가 → 불변 INSERT. SO 잠금 이전에 override 행을 쓰지 않고, 거래처 행은 잠그지 않는다(override는 여신 직렬화 축이 아니다)"""
    so, line, digest = _price_block()
    who = actor(RoleCode.TRADE)
    seen: list[str] = []

    def record(_c: Any, _cur: Any, statement: str, *_a: Any) -> None:
        seen.append(" ".join(statement.split()).upper())

    event.listen(engine, "before_cursor_execute", record)
    try:
        assert _grant(who, so["id"], _body(line, digest))[0] == 201
    finally:
        event.remove(engine, "before_cursor_execute", record)

    def first(predicate: Any) -> int:
        return next(i for i, s in enumerate(seen) if predicate(s))

    claim = first(lambda s: s.startswith("INSERT INTO IDEMPOTENCY_KEYS"))
    lock = first(lambda s: "FROM SALES_ORDERS" in s and "FOR UPDATE" in s)
    write = first(lambda s: s.startswith("INSERT INTO GATE_OVERRIDES"))
    assert claim < lock < write
    assert not [s for s in seen if "FROM PARTNERS" in s and "FOR " in s and "UPDATE" in s]
    assert scalar("SELECT count(*) FROM gate_evaluations") == 0


def test_a_failed_grant_rolls_back_everything_including_the_idempotency_claim() -> None:
    """부여가 거부(낡은 해시 409)되면 행·이벤트·감사·멱등 키가 하나도 남지 않는다 — 같은 키로 고쳐 다시 보내면 처리된다"""
    so, line, digest = _price_block()
    who = actor(RoleCode.TRADE)
    key = unique("rb")
    with pytest.raises(Exception) as caught:
        _grant(who, so["id"], _body(line, "d" * 64), key=key)
    assert getattr(caught.value, "code", None) is not None
    assert scalar("SELECT count(*) FROM gate_overrides") == 0
    assert scalar("SELECT count(*) FROM events WHERE event_type LIKE 'gates.%'") == 0
    assert scalar("SELECT count(*) FROM idempotency_keys WHERE idempotency_key = :k", k=key) == 0
    assert _grant(who, so["id"], _body(line, digest), key=key)[0] == 201
    with owner_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM gate_overrides")).scalar_one() == 1
