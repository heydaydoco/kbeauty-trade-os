"""게이트 코어 순수 단위 — 확정 가능 판정(`clearance`)·결과 팩토리·명세·평가 틀 (S3-1 PR-11a / design-D D3).

DB 없이 판정 로직만 고정한다: 통과 판정은 `gates.service.clearance` 하나이고, BLOCK·UNKNOWN은 해소 수단(override의 유효 부여·승인)이 있을 때만 풀린다.
평가 틀은 가짜 세션(`begin_nested`만 흉내)으로 돌려 미등록·예외·계약 위반이 UNKNOWN이 되는지 본다.
"""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any

import pytest
from sqlalchemy.exc import OperationalError

from app.modules.gates import service
from app.modules.gates.policy import (
    GATE_ORDER,
    GATE_SPECS,
    OVERRIDABLE_GATES,
    OVERRIDE_ROLES,
    declared_results,
    outcome,
)
from app.modules.gates.registry import EvaluatorRegistry
from app.modules.gates.service import Settlement, clearance, evaluate_all, settle
from app.modules.gates.types import (
    GateCode,
    GateLevel,
    GateOutcome,
    GatePhase,
    GateResolution,
    GateSubject,
)
from app.modules.identity.models import RoleCode
from tests.support.gate_coverage import register

L = GateLevel
R = GateResolution
G = GateCode


def _raw(
    gate: str = "PRICE_DEVIATION",
    level: GateLevel = L.BLOCK,
    resolution: GateResolution = R.OVERRIDE,
    *,
    line_id: int | None = 7,
    basis: dict[str, Any] | None = None,
    reason: str = "X",
) -> GateOutcome:
    """명세 검증 없이 만든 결과 — clearance가 **명세를 신뢰하지 않고도** 안전한지 보려는 방어 시험용."""
    return GateOutcome(gate, line_id, level, resolution, reason, "m", basis or {"p": 1})


class _FakeSession:
    """`begin_nested`만 있는 가짜 세션 — 평가 틀의 SAVEPOINT 사용을 흉내."""

    def begin_nested(self) -> Any:
        return nullcontext()


def _subject() -> GateSubject:
    return GateSubject(
        kind="SALES_ORDER",
        id=1,
        buyer_partner_id=1,
        currency="USD",
        dest_market_code="US",
        lines=(),
        total_amount=0,
    )


# ══ clearance — 통과 판정의 유일한 정의 ══════════════════════════════════════════


@pytest.mark.group_h
@pytest.mark.parametrize("resolution", list(R))
@pytest.mark.parametrize("level", [L.PASS, L.WARN])
def test_pass_and_warn_never_need_resolution(level: GateLevel, resolution: GateResolution) -> None:
    """PASS·WARN은 어떤 해소 값이든 통과 — 해소가 필요 없다(WARN은 확정 증거에 남는다)"""
    state, override_id = settle(_raw(level=level, resolution=resolution), {}, False)
    assert (state, override_id) == (Settlement.NOT_REQUIRED, None)


@pytest.mark.group_h
@pytest.mark.parametrize("level", [L.BLOCK, L.UNKNOWN])
def test_block_and_unknown_with_no_resolution_path_are_unresolved_whatever_is_supplied(
    level: GateLevel,
) -> None:
    """BLOCK·UNKNOWN + NONE은 override가 있어도 승인이 있어도 미해소 — 데이터를 고쳐야 한다"""
    item = _raw("ITEM_MAPPING", level, R.NONE)
    overrides = {("ITEM_MAPPING", 7, item.basis_hash): 1}
    assert settle(item, overrides, True)[0] is Settlement.UNRESOLVED


@pytest.mark.group_h
@pytest.mark.parametrize("level", [L.BLOCK, L.UNKNOWN])
def test_override_resolves_only_a_matching_grant_bound_to_the_judgement_hash(
    level: GateLevel,
) -> None:
    """OVERRIDE 해소는 (게이트, 라인, 판정 해시)가 모두 같은 유효 부여일 때만 — 해시·라인·게이트 중 하나라도 다르면 미해소(입력이 바뀌면 자동 무효)"""
    item = _raw("MOQ", level, R.OVERRIDE, line_id=3, basis={"q": 9})
    good = {("MOQ", 3, item.basis_hash): 42}
    assert settle(item, good, False) == (Settlement.OVERRIDDEN, 42)
    stale = {("MOQ", 3, _raw("MOQ", level, R.OVERRIDE, line_id=3, basis={"q": 10}).basis_hash): 42}
    assert settle(item, stale, False)[0] is Settlement.UNRESOLVED
    assert settle(item, {("MOQ", 4, item.basis_hash): 42}, False)[0] is Settlement.UNRESOLVED
    assert (
        settle(item, {("PRICE_DEVIATION", 3, item.basis_hash): 42}, False)[0]
        is Settlement.UNRESOLVED
    )
    assert settle(item, {}, False)[0] is Settlement.UNRESOLVED


@pytest.mark.group_h
@pytest.mark.parametrize("gate", ["ITEM_MAPPING", "DUPLICATE_PO", "CREDIT"])
def test_a_non_overridable_gate_is_never_cleared_by_an_override_even_if_one_exists(
    gate: str,
) -> None:
    """방어: 명세 밖 값(품번·중복 PO·여신이 OVERRIDE 해소를 달고 오고 부여 키까지 있어도)은 미해소 — 우회 불가 게이트는 clearance가 한 번 더 막는다"""
    item = _raw(gate, L.BLOCK, R.OVERRIDE, line_id=None)
    assert gate not in OVERRIDABLE_GATES
    assert settle(item, {(gate, None, item.basis_hash): 1}, True)[0] is Settlement.UNRESOLVED


@pytest.mark.group_h
def test_approval_resolves_only_the_approval_resolution_and_only_when_available() -> None:
    """APPROVAL 해소는 소비 가능한 승인이 있을 때만 — override는 승인을 대신하지 못하고, 승인은 override 대상을 풀지 못한다. 승인이 필요한 미해소는 needs_approval로 드러난다"""
    credit = _raw("CREDIT", L.BLOCK, R.APPROVAL, line_id=None)
    assert settle(credit, {}, False)[0] is Settlement.UNRESOLVED
    assert settle(credit, {}, True)[0] is Settlement.APPROVED
    assert (
        settle(credit, {("CREDIT", None, credit.basis_hash): 1}, False)[0] is Settlement.UNRESOLVED
    )
    price = _raw("PRICE_DEVIATION", L.BLOCK, R.OVERRIDE)
    assert settle(price, {}, True)[0] is Settlement.UNRESOLVED
    result = clearance([credit], {}, False)
    assert (result.cleared, result.needs_approval) == (False, True)
    assert clearance([credit], {}, True).cleared is True


@pytest.mark.group_h
def test_an_empty_evaluation_is_not_a_clearance() -> None:
    """아무것도 평가하지 않았으면(결과 0건) 확정 가능이 아니다 — 평가 누락이 조용한 통과가 되지 않는다"""
    assert clearance([], {}, True).cleared is False


@pytest.mark.group_h
def test_clearance_collects_used_override_ids_and_every_unresolved_item() -> None:
    """한 평가에 해소·미해소가 섞이면 cleared=False, 쓰인 override id(중복 제거·정렬)와 미해소 목록이 정확히 나온다"""
    a = _raw("PRICE_DEVIATION", L.BLOCK, R.OVERRIDE, line_id=1, basis={"a": 1})
    b = _raw("MOQ", L.UNKNOWN, R.OVERRIDE, line_id=2, basis={"b": 1})
    c = _raw("ITEM_MAPPING", L.BLOCK, R.NONE, line_id=3)
    ok = _raw("DUPLICATE_PO", L.WARN, R.NONE, line_id=None)
    overrides = {("PRICE_DEVIATION", 1, a.basis_hash): 9, ("MOQ", 2, b.basis_hash): 5}
    result = clearance([a, b, c, ok], overrides, False)
    assert result.cleared is False
    assert result.used_overrides == (5, 9)
    assert result.unresolved == (c,)
    assert [s for _, s in result.settlements] == [
        Settlement.OVERRIDDEN,
        Settlement.OVERRIDDEN,
        Settlement.UNRESOLVED,
        Settlement.NOT_REQUIRED,
    ]
    assert clearance([a, b, ok], overrides, False).cleared is True


@pytest.mark.group_h
def test_unknown_is_never_treated_as_pass() -> None:
    """UNKNOWN(평가 불능)은 PASS와 다르게 표기되고(값이 다르다) 확정 가능 판정에서 BLOCK과 같은 효과 — UNKNOWN→PASS 매핑이 생기면 이 테스트가 실패한다"""
    assert L.UNKNOWN is not L.PASS and L.UNKNOWN.value != L.PASS.value
    unknown = _raw("MARKET_READINESS", L.UNKNOWN, R.NONE, line_id=None)
    assert clearance([unknown], {}, True).cleared is False
    assert service.is_overridable(_raw("MARKET_READINESS", L.UNKNOWN, R.OVERRIDE)) is True
    assert service.is_overridable(_raw("MARKET_READINESS", L.WARN, R.OVERRIDE)) is False
    assert service.is_overridable(_raw("CREDIT", L.BLOCK, R.APPROVAL)) is False


# ══ 명세·결과 팩토리 ═══════════════════════════════════════════════════════════


@pytest.mark.group_a
def test_every_gate_has_a_spec_and_the_order_is_the_display_order() -> None:
    """7개 게이트가 모두 명세를 갖고 표시 순서가 통합 X-35의 명단 순서다"""
    assert [g.value for g in GATE_ORDER] == [
        "ITEM_MAPPING",
        "DUPLICATE_PO",
        "PRICE_DEVIATION",
        "CREDIT",
        "MARKET_READINESS",
        "MOQ",
        "PI_DEPOSIT",
    ]
    assert set(GATE_SPECS) == set(GateCode)


@pytest.mark.group_a
def test_override_roles_are_exactly_the_four_overridable_gates() -> None:
    """override 허용 역할 표 = 가격·MOQ(무역·관리자)·준비도·PI(관리자) — 품번·중복 PO·여신은 표에 없다(우회 불가). LOGISTICS·CERT·VIEWER는 어느 게이트에도 없다"""
    assert OVERRIDE_ROLES == {
        G.PRICE_DEVIATION: (RoleCode.TRADE, RoleCode.ADMIN),
        G.MOQ: (RoleCode.TRADE, RoleCode.ADMIN),
        G.MARKET_READINESS: (RoleCode.ADMIN,),
        G.PI_DEPOSIT: (RoleCode.ADMIN,),
    }
    everyone = {role for roles in OVERRIDE_ROLES.values() for role in roles}
    assert everyone.isdisjoint({RoleCode.LOGISTICS, RoleCode.CERT, RoleCode.VIEWER})


@pytest.mark.group_a
def test_a_declared_override_resolution_always_has_roles_and_the_others_never_do() -> None:
    """명세가 OVERRIDE 해소를 선언한 게이트는 override 역할이 있고(공회전 방지), 그 밖의 해소는 역할이 없다"""
    for code, spec in GATE_SPECS.items():
        has_override = any(r.resolution is R.OVERRIDE for r in spec.results)
        assert has_override == (code in OVERRIDE_ROLES), code
        for result in spec.results:
            built = outcome(code, result.level, result.resolution, "T", "m")
            assert bool(built.override_roles) == (result.resolution is R.OVERRIDE)


@pytest.mark.group_a
def test_the_outcome_factory_rejects_combinations_the_spec_does_not_declare() -> None:
    """명세에 없는 (결과, 해소) 조합은 ValueError — 품번 매핑을 OVERRIDE로, 여신을 OVERRIDE로, MOQ를 APPROVAL로 만들 수 없다"""
    for code, level, resolution in [
        (G.ITEM_MAPPING, L.BLOCK, R.OVERRIDE),
        (G.DUPLICATE_PO, L.BLOCK, R.OVERRIDE),
        (G.CREDIT, L.BLOCK, R.OVERRIDE),
        (G.CREDIT, L.UNKNOWN, R.APPROVAL),
        (G.MOQ, L.BLOCK, R.APPROVAL),
        (G.PI_DEPOSIT, L.WARN, R.OVERRIDE),
    ]:
        assert (level, resolution) not in declared_results(code)
        with pytest.raises(ValueError):
            outcome(code, level, resolution, "T", "m")


@pytest.mark.group_a
def test_every_gate_may_report_unknown_none_as_the_framework_result() -> None:
    """UNKNOWN/NONE은 모든 게이트가 가질 수 있는 틀 결과다(미등록·예외)"""
    for code in GateCode:
        assert (L.UNKNOWN, R.NONE) in declared_results(code)
        assert outcome(code, L.UNKNOWN, R.NONE, "T", "m").override_roles == ()


@pytest.mark.group_a
def test_the_basis_hash_binds_the_judgement_not_the_wording() -> None:
    """basis_hash는 (게이트, 라인, 결과, 이유 코드, 근거)의 canonical JSON sha256 — 키 순서·메시지 문구와 무관하고 한 값이라도 다르면 달라진다. 64자 소문자 hex"""
    base = outcome(
        G.PRICE_DEVIATION,
        L.BLOCK,
        R.OVERRIDE,
        "BELOW_MOQ",
        "문구 A",
        {"quantity": 9, "moq": 10},
        line_id=1,
    )
    reordered = outcome(
        G.PRICE_DEVIATION,
        L.BLOCK,
        R.OVERRIDE,
        "BELOW_MOQ",
        "문구 B",
        {"moq": 10, "quantity": 9},
        line_id=1,
    )
    assert base.basis_hash == reordered.basis_hash
    assert len(base.basis_hash) == 64 and base.basis_hash == base.basis_hash.lower()
    variants = [
        outcome(
            G.PRICE_DEVIATION,
            L.BLOCK,
            R.OVERRIDE,
            "BELOW_MOQ",
            "m",
            {"quantity": 8, "moq": 10},
            line_id=1,
        ),
        outcome(
            G.PRICE_DEVIATION,
            L.BLOCK,
            R.OVERRIDE,
            "BELOW_MOQ",
            "m",
            {"quantity": 9, "moq": 10},
            line_id=2,
        ),
        outcome(
            G.PRICE_DEVIATION,
            L.BLOCK,
            R.OVERRIDE,
            "OTHER",
            "m",
            {"quantity": 9, "moq": 10},
            line_id=1,
        ),
        outcome(
            G.PRICE_DEVIATION,
            L.UNKNOWN,
            R.OVERRIDE,
            "BELOW_MOQ",
            "m",
            {"quantity": 9, "moq": 10},
            line_id=1,
        ),
    ]
    assert len({v.basis_hash for v in variants} | {base.basis_hash}) == 5


# ══ 평가 틀 — 미등록·예외·계약 위반 = UNKNOWN ═══════════════════════════════════


def _registry_with(**evaluators: Any) -> EvaluatorRegistry:
    registry = EvaluatorRegistry()
    for name, function in evaluators.items():
        registry.register(GateCode(name), function)
    return registry


for _gate in (
    GateCode
):  # 평가기 미등록·예외 → UNKNOWN/NONE은 7종 모두의 틀 결과(아래 두 테스트가 7종 전수로 검증)
    register(_gate, GateLevel.UNKNOWN, GateResolution.NONE)


@pytest.mark.group_a
@pytest.mark.parametrize("gate", list(GateCode))
def test_an_unregistered_evaluator_is_unknown_for_every_gate(gate: GateCode) -> None:
    """등록부가 비어 있으면 모든 게이트가 UNKNOWN/NONE(EVALUATOR_NOT_REGISTERED) — 평가기가 없다는 사실이 통과가 되지 않는다"""
    results = evaluate_all(
        _FakeSession(), _subject(), GatePhase.CONFIRM, registry=EvaluatorRegistry(), only=[gate]
    )  # type: ignore[arg-type]
    assert [(o.gate_code, o.level, o.resolution, o.reason_code) for o in results] == [
        (gate.value, L.UNKNOWN, R.NONE, "EVALUATOR_NOT_REGISTERED")
    ]
    assert clearance(results, {}, True).cleared is False


@pytest.mark.group_a
def test_a_crashing_evaluator_is_unknown_and_only_the_class_name_leaks() -> None:
    """평가기가 예외를 던지면 UNKNOWN/NONE(EVALUATION_ERROR) — 응답·근거에는 예외 클래스명만(내부 메시지 금지), 다른 게이트 평가는 계속된다"""

    def boom(_s: Any, _subject: Any, _p: Any) -> list[GateOutcome]:
        raise RuntimeError("secret internal detail: cost=1234")

    def fine(_s: Any, _subject: Any, _p: Any) -> list[GateOutcome]:
        return [outcome(G.DUPLICATE_PO, L.PASS, R.NONE, "OK", "ok")]

    registry = _registry_with(MOQ=boom, DUPLICATE_PO=fine)
    results = evaluate_all(
        _FakeSession(),
        _subject(),
        GatePhase.CONFIRM,
        registry=registry,
        only=[G.DUPLICATE_PO, G.MOQ],  # type: ignore[arg-type]
    )
    by_gate = {o.gate_code: o for o in results}
    assert by_gate["DUPLICATE_PO"].level is L.PASS
    crashed = by_gate["MOQ"]
    assert (crashed.level, crashed.resolution, crashed.reason_code) == (
        L.UNKNOWN,
        R.NONE,
        "EVALUATION_ERROR",
    )
    assert crashed.basis == {"error_class": "RuntimeError"}
    assert "secret" not in crashed.message_ko and "1234" not in str(crashed.basis)


@pytest.mark.group_a
def test_lock_timeout_and_deadlock_propagate_instead_of_becoming_unknown() -> None:
    """잠금 대기 초과(55P03)·교착(40P01)은 평가 불능으로 삼키지 않고 그대로 전파(409 LOCK_BUSY로 번역된다) — 같은 틀의 다른 DB 오류는 UNKNOWN"""

    class _Orig(Exception):
        def __init__(self, sqlstate: str) -> None:
            self.sqlstate = sqlstate

    def locked(sqlstate: str) -> Any:
        def evaluator(_s: Any, _subject: Any, _p: Any) -> list[GateOutcome]:
            raise OperationalError("SELECT 1", {}, _Orig(sqlstate))

        return evaluator

    for state in ("55P03", "40P01"):
        registry = _registry_with(MOQ=locked(state))
        with pytest.raises(OperationalError):
            evaluate_all(
                _FakeSession(), _subject(), GatePhase.CONFIRM, registry=registry, only=[G.MOQ]
            )  # type: ignore[arg-type]
    other = evaluate_all(
        _FakeSession(),
        _subject(),
        GatePhase.CONFIRM,
        registry=_registry_with(MOQ=locked("08006")),
        only=[G.MOQ],  # type: ignore[arg-type]
    )
    assert other[0].reason_code == "EVALUATION_ERROR"


@pytest.mark.group_a
def test_evaluator_contract_violations_are_unknown() -> None:
    """평가기가 빈 목록·남의 게이트 결과·명세 밖 조합·(게이트,라인) 중복을 돌려주면 UNKNOWN(EVALUATION_ERROR) — 통과로 흐르지 않는다"""
    wrong_gate = outcome(G.DUPLICATE_PO, L.PASS, R.NONE, "OK", "ok")
    undeclared = GateOutcome(
        "MOQ", None, L.BLOCK, R.APPROVAL, "X", "m"
    )  # 팩토리를 우회해 만든 명세 밖 조합
    pass_once = outcome(G.MOQ, L.PASS, R.NONE, "OK", "ok")
    cases: dict[str, list[GateOutcome]] = {
        "empty": [],
        "foreign": [wrong_gate],
        "undeclared": [undeclared],
        "duplicate": [pass_once, pass_once],
    }
    for name, produced in cases.items():
        registry = _registry_with(MOQ=lambda _s, _subj, _p, _r=produced: _r)
        results = evaluate_all(
            _FakeSession(),
            _subject(),
            GatePhase.CONFIRM,
            registry=registry,
            only=[G.MOQ],  # type: ignore[arg-type]
        )
        assert [(o.level, o.reason_code) for o in results] == [(L.UNKNOWN, "EVALUATION_ERROR")], (
            name
        )


@pytest.mark.group_a
def test_phase_and_only_filters_select_the_gates_to_run() -> None:
    """인테이크 시점에는 확정 전용 게이트(CREDIT·PI_DEPOSIT)가 실행되지 않고, `only`는 지정한 게이트만 실행한다 — 결과는 표시 순서"""
    registry = EvaluatorRegistry()
    seen: list[str] = []
    for code in GateCode:

        def make(name: str) -> Any:
            def evaluator(_s: Any, _subject: Any, _p: Any) -> list[GateOutcome]:
                seen.append(name)
                return [outcome(GateCode(name), L.PASS, R.NONE, "OK", "ok")]

            return evaluator

        registry.register(code, make(code.value))
    intake = evaluate_all(_FakeSession(), _subject(), GatePhase.INTAKE, registry=registry)  # type: ignore[arg-type]
    assert [o.gate_code for o in intake] == [
        "ITEM_MAPPING",
        "DUPLICATE_PO",
        "PRICE_DEVIATION",
        "MARKET_READINESS",
        "MOQ",
    ]
    confirm = evaluate_all(_FakeSession(), _subject(), GatePhase.CONFIRM, registry=registry)  # type: ignore[arg-type]
    assert len(confirm) == 7
    seen.clear()
    only = evaluate_all(
        _FakeSession(), _subject(), GatePhase.CONFIRM, registry=registry, only=["PI_DEPOSIT", "MOQ"]
    )  # type: ignore[arg-type]
    assert [o.gate_code for o in only] == ["MOQ", "PI_DEPOSIT"] and sorted(seen) == [
        "MOQ",
        "PI_DEPOSIT",
    ]


@pytest.mark.group_a
def test_the_registry_refuses_a_second_registration_and_unknown_codes() -> None:
    """같은 게이트의 2차 등록과 모르는 게이트 코드는 거부 — 조용한 덮어쓰기·오타 등록이 없다"""
    registry = EvaluatorRegistry()
    registry.register(G.MOQ, lambda *_a: [])
    with pytest.raises(ValueError):
        registry.register(G.MOQ, lambda *_a: [])
    with pytest.raises(ValueError):
        registry.register("MOQ_TYPO", lambda *_a: [])


@pytest.mark.group_h
def test_the_reason_validator_enforces_five_to_five_hundred_characters_after_trimming() -> None:
    """사유는 앞뒤 공백을 자른 뒤 5~500자 — 4자·공백으로 채운 5자·제어문자는 거부, 5자·500자는 통과"""
    from app.core.errors.exceptions import AppError

    assert service.clean_reason("  다섯글자요  ") == "다섯글자요"
    assert service.clean_reason("12345") == "12345"
    assert len(service.clean_reason("가" * 500)) == 500
    for bad in ["1234", "    a    ", "가" * 501, "줄\n바꿈사유", "탭\t사유입니다"]:
        with pytest.raises(AppError):
            service.clean_reason(bad)


class _NoWriteSession:
    """쓰기를 하면 실패하는 가짜 세션 — 서비스가 DB CHECK 이전에 거부하는지(방어층이 DB뿐이 아닌지) 확인한다."""

    def add(self, *_args: Any) -> None:
        raise AssertionError("거부해야 할 override가 DB 쓰기까지 갔다")

    def flush(self) -> None:
        raise AssertionError("거부해야 할 override가 DB 쓰기까지 갔다")


@pytest.mark.group_h
@pytest.mark.parametrize(
    ("level", "resolution", "expected"),
    [
        (L.PASS, R.NONE, "GATES.OVERRIDE.NOT_APPLICABLE"),
        (L.WARN, R.NONE, "GATES.OVERRIDE.NOT_APPLICABLE"),
        (L.UNKNOWN, R.NONE, "GATES.OVERRIDE.NOT_APPLICABLE"),
        (L.BLOCK, R.OVERRIDE, "OK"),
    ],
    ids=["pass", "warn", "unknown-none", "block-override"],
)
def test_the_service_refuses_a_non_overridable_result_before_touching_the_database(
    level: GateLevel, resolution: GateResolution, expected: str
) -> None:
    """서비스가 PASS·WARN·해소 수단 없는 UNKNOWN에는 DB 쓰기 전에 422 NOT_APPLICABLE로 거부한다(DB CHECK는 마지막 방어선일 뿐 — 서비스 규칙이 먼저). 양성 대조: BLOCK/OVERRIDE는 쓰기까지 간다"""
    from app.core.errors.exceptions import AppError

    target = outcome(G.PRICE_DEVIATION, level, resolution, "T", "m", {"p": 1}, line_id=3)
    args: dict[str, Any] = {
        "subject_type": "SALES_ORDER",
        "subject_id": 1,
        "outcomes": [target],
        "gate_code": "PRICE_DEVIATION",
        "line_id": 3,
        "basis_hash": target.basis_hash,
        "reason": "시험 사유입니다",
        "actor_user_id": 1,
        "actor_roles": {RoleCode.TRADE},
    }
    if expected == "OK":
        with pytest.raises(AssertionError):  # 쓰기에 도달했다(양성 대조)
            service.grant_override(_NoWriteSession(), **args)  # type: ignore[arg-type]
        return
    with pytest.raises(AppError) as caught:
        service.grant_override(_NoWriteSession(), **args)  # type: ignore[arg-type]
    assert caught.value.code.value == expected
