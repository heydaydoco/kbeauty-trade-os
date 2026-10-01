"""A. 확정 통로 보조 판정 직접 시험 — 손으로 만든 `Clearance`·정산 입력 전수 (S3-1 PR-12a 적대 검토 M2·L1 / ADR-0070).

`credit_verdict_of`·`pi_gate_verdict_of`는 `clearance` 정산(`Settlement`)과 소비 결과를 SO 증적 값으로 **매핑만** 하고, 어긋나면(정산 불일치) 확정하지 않고 전용 409로 롤백하게 한다.
통합 시험이 못 만드는 조합(잠금 하에서는 드문 어긋남)을 입력으로 직접 만들어 전 분기를 고정한다. `evaluation_digest`는 증거용(승인 결속 아님) 결정적 해시다.
"""

from __future__ import annotations

import pytest

from app.core.errors.exceptions import AppError
from app.modules.gates.service import Clearance, Settlement
from app.modules.gates.types import GateCode, GateLevel, GateOutcome, GateResolution
from app.modules.trade_chain import confirm

pytestmark = pytest.mark.group_a

SO_ID = 77
NR, OV, AP, UN = (
    Settlement.NOT_REQUIRED,
    Settlement.OVERRIDDEN,
    Settlement.APPROVED,
    Settlement.UNRESOLVED,
)


def _item(
    gate: GateCode,
    reason: str,
    *,
    level: GateLevel = GateLevel.PASS,
    resolution: GateResolution = GateResolution.NONE,
    line_id: int | None = None,
    basis: dict[str, object] | None = None,
) -> GateOutcome:
    return GateOutcome(
        gate_code=gate.value,
        line_id=line_id,
        level=level,
        resolution=resolution,
        reason_code=reason,
        message_ko="시험",
        basis=basis or {},  # type: ignore[arg-type]
    )


def _clr(*pairs: tuple[GateOutcome, Settlement]) -> Clearance:
    return Clearance(
        cleared=all(state is not UN for _, state in pairs),
        unresolved=tuple(i for i, state in pairs if state is UN),
        used_overrides=(),
        needs_approval=False,
        settlements=tuple(pairs),
    )


def _credit(reason: str, state: Settlement) -> tuple[GateOutcome, Settlement]:
    return _item(GateCode.CREDIT, reason), state


def _pi(reason: str, state: Settlement) -> tuple[GateOutcome, Settlement]:
    return _item(GateCode.PI_DEPOSIT, reason), state


def _is_conflict(error: AppError) -> bool:
    return str(error.code) == "COMMON.CONCURRENCY.VERSION_CONFLICT" and error.status_code == 409


# ── credit_verdict_of ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("reason", "state", "consumed", "expected"),
    [
        ("LIMIT_EXCEEDED", AP, True, "APPROVED"),
        ("WITHIN_LIMIT", NR, False, "WITHIN_LIMIT"),
        ("NOT_MANAGED", NR, False, "NOT_MANAGED"),
    ],
    ids=["승인소비", "한도이내", "여신미관리"],
)
def test_the_credit_verdict_maps_the_three_storable_outcomes(
    reason: str, state: Settlement, consumed: bool, expected: str
) -> None:
    clr = _clr(_credit(reason, state), _pi("INACTIVE", NR))
    assert confirm.credit_verdict_of(clr, consumed=consumed, so_id=SO_ID) == expected


@pytest.mark.parametrize(
    ("reason", "state", "consumed"),
    [
        ("LIMIT_EXCEEDED", AP, False),  # 승인으로 해소됐다는 정산인데 소비가 없었다
        ("WITHIN_LIMIT", NR, True),  # 한도 이내인데 승인이 소비됐다
        ("NOT_MANAGED", NR, True),
        ("NO_INCREMENT", NR, False),  # 저장 가능한 통과 사유가 아니다(모르는 코드 fail-closed)
        ("WITHIN_LIMIT", UN, False),  # 미해소
        ("WITHIN_LIMIT", OV, False),  # 여신은 override 대상이 아니다
        ("LIMIT_EXCEEDED", UN, False),
        ("LIMIT_EXCEEDED", UN, True),
    ],
)
def test_a_settlement_that_disagrees_with_the_consumption_is_refused_with_the_dedicated_conflict(
    reason: str, state: Settlement, consumed: bool
) -> None:
    clr = _clr(_credit(reason, state), _pi("INACTIVE", NR))
    with pytest.raises(AppError) as caught:
        confirm.credit_verdict_of(clr, consumed=consumed, so_id=SO_ID)
    assert _is_conflict(caught.value)
    assert caught.value.message == confirm.INCONSISTENT_MESSAGE
    assert caught.value.log_context["sales_order_id"] == SO_ID


@pytest.mark.parametrize("count", [0, 2], ids=["여신결과없음", "여신결과둘"])
def test_exactly_one_credit_result_is_required(count: int) -> None:
    pairs = [_credit("WITHIN_LIMIT", NR) for _ in range(count)] + [_pi("INACTIVE", NR)]
    with pytest.raises(AppError) as caught:
        confirm.credit_verdict_of(_clr(*pairs), consumed=False, so_id=SO_ID)
    assert _is_conflict(caught.value)


# ── pi_gate_verdict_of ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("SUFFICIENT", "PASS"),
        ("ZERO_REQUIRED", "PASS"),
        ("INACTIVE", "NOT_APPLICABLE"),
        ("SKIPPED_OFF", "SKIPPED_OFF"),
        ("SHORT", "WARN"),
        ("PI_MISSING", "WARN"),
        ("PI_NOT_USABLE", "WARN"),
    ],
)
def test_the_pi_verdict_maps_every_passing_reason_code(reason: str, expected: str) -> None:
    clr = _clr(_credit("NOT_MANAGED", NR), _pi(reason, NR))
    assert confirm.pi_gate_verdict_of(clr, SO_ID) == expected


def test_the_reason_table_covers_exactly_the_documented_reasons() -> None:
    """매핑표의 키 집합이 `pi_gate`가 내는 통과 가능 사유와 같다(새 사유가 조용히 빠지거나 남지 않는다)"""
    assert set(confirm._PI_VERDICT_BY_REASON) == {
        "SUFFICIENT", "ZERO_REQUIRED", "INACTIVE", "SKIPPED_OFF", "SHORT", "PI_MISSING", "PI_NOT_USABLE",
    }  # fmt: skip


def test_an_overridden_pi_gate_is_recorded_as_overridden_whatever_the_reason() -> None:
    clr = _clr(_credit("NOT_MANAGED", NR), _pi("SHORT", OV))
    assert confirm.pi_gate_verdict_of(clr, SO_ID) == "OVERRIDDEN"


@pytest.mark.parametrize(
    ("reason", "state"),
    [
        ("SUFFICIENT", UN),  # 미해소
        ("SUFFICIENT", AP),  # PI는 승인 대상이 아니다
        ("SOMETHING_NEW", NR),  # 모르는 사유 — fail-closed
        ("PAYMENT_TYPE_UNSET", NR),  # 판정 불능 사유는 통과가 아니다
        ("TERMS_INCOMPLETE", NR),
    ],
)
def test_an_unexpected_pi_settlement_or_reason_is_refused(reason: str, state: Settlement) -> None:
    clr = _clr(_credit("NOT_MANAGED", NR), _pi(reason, state))
    with pytest.raises(AppError) as caught:
        confirm.pi_gate_verdict_of(clr, SO_ID)
    assert _is_conflict(caught.value)


@pytest.mark.parametrize("count", [0, 2], ids=["PI결과없음", "PI결과둘"])
def test_exactly_one_pi_result_is_required(count: int) -> None:
    pairs = [_credit("NOT_MANAGED", NR)] + [_pi("INACTIVE", NR) for _ in range(count)]
    with pytest.raises(AppError) as caught:
        confirm.pi_gate_verdict_of(_clr(*pairs), SO_ID)
    assert _is_conflict(caught.value)


# ── 승인 id 일치·재평가 판정 ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("consumed_id", "settled_id", "ok"),
    [(None, None, True), (5, 5, True), (5, None, False), (None, 5, False), (5, 6, False)],
)
def test_the_consumed_approval_must_be_the_one_the_settlement_saw(
    consumed_id: int | None, settled_id: int | None, ok: bool
) -> None:
    if ok:
        confirm._require_same_approval(consumed_id, settled_id, SO_ID)
        return
    with pytest.raises(AppError) as caught:
        confirm._require_same_approval(consumed_id, settled_id, SO_ID)
    assert _is_conflict(caught.value)


def test_only_an_approval_settled_credit_triggers_the_re_evaluation_check() -> None:
    assert confirm._credit_settled_by_approval(
        _clr(_credit("LIMIT_EXCEEDED", AP), _pi("INACTIVE", NR)), SO_ID
    )
    assert not confirm._credit_settled_by_approval(
        _clr(_credit("WITHIN_LIMIT", NR), _pi("INACTIVE", NR)), SO_ID
    )


# ── 증거용 digest ──────────────────────────────────────────────────────────────────


def test_the_evidence_digest_is_deterministic_order_independent_and_sensitive_to_inputs() -> None:
    """같은 결과 집합은 순서와 무관하게 같은 값 · 어떤 결과의 판정 입력(basis)·정책 출처가 바뀌면 달라진다(L1)"""
    a = _item(GateCode.MOQ, "BELOW_MOQ", level=GateLevel.BLOCK, line_id=2, basis={"moq": 100})
    b = _item(GateCode.PRICE_DEVIATION, "OK", line_id=1, basis={"unit": 10})
    c = _item(GateCode.CREDIT, "WITHIN_LIMIT")
    sources = {"pi_advance_gate_mode": "UNSET_DEFAULT", "price_deviation_tolerance_bp": "SET"}
    base = confirm.evaluation_digest(_clr((a, UN), (b, NR), (c, NR)), sources)
    assert len(base) == 64 and base == confirm.evaluation_digest(
        _clr((a, UN), (b, NR), (c, NR)), sources
    )
    assert base == confirm.evaluation_digest(
        _clr((c, NR), (a, UN), (b, NR)), dict(reversed(sources.items()))
    )
    changed_basis = _item(
        GateCode.MOQ, "BELOW_MOQ", level=GateLevel.BLOCK, line_id=2, basis={"moq": 200}
    )
    assert base != confirm.evaluation_digest(_clr((changed_basis, UN), (b, NR), (c, NR)), sources)
    assert base != confirm.evaluation_digest(
        _clr((a, UN), (b, NR), (c, NR)), {**sources, "pi_advance_gate_mode": "SET"}
    )  # 정책 출처만 바뀐 입력
    assert base != confirm.evaluation_digest(
        _clr((a, UN), (b, NR)), sources
    )  # 결과가 빠지면 달라진다


def test_the_inconsistency_error_names_the_cause_for_the_user() -> None:
    error = confirm._inconsistent(SO_ID, "x")
    assert "다른 수주" in error.message and "다시" in error.message
    assert _is_conflict(error)
